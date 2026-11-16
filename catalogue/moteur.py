"""Moteur du catalogue de data products : les marts declares cote producteur.

Les declarations vivent dans le repo dbt (catalogue/<domaine>/*.yaml,
chemin DBT_DIR, defaut ../supply-dbt-warehouse), un YAML par data
product, valide contre le JSON Schema catalogue/schema/
data_product.schema.json du meme repo. Ce moteur ne lit que : il ne
modifie ni les declarations, ni l'entrepot.

Pour chaque data product, le moteur :

- valide le YAML contre le JSON Schema (structure, champs obligatoires) ;
- verifie les references croisees : chaque table declaree en port de
  sortie est un mart reellement present dans l'entrepot DuckDB (lecture
  seule via lib.base), le contrat ODCS reference existe dans contracts/,
  les consommateurs declares sont des vues, modules ou exports connus,
  et le dossier du YAML correspond au domaine declare ;
- calcule son ETAT VIVANT : fraicheur reelle (age du dernier build en
  succes lu dans etat_build.json, compare au SLO fraicheur_heures),
  nombre de lignes de chaque mart, statut du contrat lie (via le moteur
  des contrats), et score de maturite (documentation, contrat, SLO,
  owner presents : x / 4).

Statut d'un data product :
- "invalide" : YAML hors schema ou reference croisee cassee ;
- "degrade"  : declaration valide mais SLO de fraicheur depasse ou
  contrat viole ;
- "sain"     : tout est verifie et respecte.

CLI : python -m catalogue (code retour 1 des qu'un data product est
invalide, 0 sinon).
"""

from __future__ import annotations

import datetime
import json
import os

import yaml
from jsonschema import Draft202012Validator

from contrats import moteur as moteur_contrats
from lib import base
from pipeline import moteur as moteur_pipeline

NOM_DOSSIER_CATALOGUE = "catalogue"
CHEMIN_SCHEMA = os.path.join("schema", "data_product.schema.json")
SUFFIXE_DATA_PRODUCT = ".yaml"

STATUT_SAIN = "sain"
STATUT_DEGRADE = "degrade"
STATUT_INVALIDE = "invalide"

CONSOMMATEURS_CONNUS = {
    "vue": frozenset(
        {
            "dashboard",
            "rapport",
            "pipeline",
            "contrats",
            "catalogue",
            "assistant",
            "alertes",
            "fiches",
            "sante",
        }
    ),
    "module": frozenset(
        {
            "alerte_rupture",
            "rapport_hebdo",
            "agent_qa",
            "fiches_produits",
            "gardien_qualite",
        }
    ),
    "export": frozenset({"rapport-hebdo.html", "rapport.md", "outbox-slack"}),
}
"""Consommateurs declarables : vues SupplySense, modules agents, exports."""

CRITERES_MATURITE = ("documentation", "contrat", "slo", "owner")


class ErreurCatalogue(Exception):
    """Catalogue illisible : dossier ou schema absent, YAML invalide."""


# ---------------------------------------------------------------------------
# Chargement
# ---------------------------------------------------------------------------

def dossier_catalogue(repertoire_dbt: str | None = None) -> str:
    """Dossier catalogue/ du repo dbt."""
    return os.path.join(
        repertoire_dbt or moteur_contrats.dossier_dbt(), NOM_DOSSIER_CATALOGUE
    )


def charger_schema(repertoire_dbt: str | None = None) -> dict:
    """Charge le JSON Schema des data products depuis le repo dbt."""
    chemin = os.path.join(dossier_catalogue(repertoire_dbt), CHEMIN_SCHEMA)
    if not os.path.exists(chemin):
        raise ErreurCatalogue(f"JSON Schema introuvable : {chemin}.")
    with open(chemin, encoding="utf-8") as fichier:
        return json.load(fichier)


def charger_data_products(repertoire_dbt: str | None = None) -> list[dict]:
    """Charge les YAML du catalogue, tries par domaine puis nom de fichier.

    Chaque data product est enrichi de _fichier (chemin relatif au dossier
    catalogue/) et _domaine_dossier (nom du sous-dossier). Le dossier
    schema/ est ignore. Dossier catalogue/ absent : liste vide.
    """
    dossier = dossier_catalogue(repertoire_dbt)
    if not os.path.isdir(dossier):
        return []
    data_products: list[dict] = []
    for domaine in sorted(os.listdir(dossier)):
        chemin_domaine = os.path.join(dossier, domaine)
        if domaine == "schema" or not os.path.isdir(chemin_domaine):
            continue
        for nom in sorted(os.listdir(chemin_domaine)):
            if not nom.endswith(SUFFIXE_DATA_PRODUCT):
                continue
            chemin = os.path.join(chemin_domaine, nom)
            try:
                with open(chemin, encoding="utf-8") as fichier:
                    data_product = yaml.safe_load(fichier)
            except (OSError, yaml.YAMLError) as erreur:
                raise ErreurCatalogue(
                    f"Data product illisible : {chemin} ({erreur})."
                )
            if not isinstance(data_product, dict):
                raise ErreurCatalogue(
                    f"Data product invalide : {chemin} (objet YAML attendu)."
                )
            data_product["_fichier"] = f"{domaine}/{nom}"
            data_product["_domaine_dossier"] = domaine
            data_products.append(data_product)
    return data_products


# ---------------------------------------------------------------------------
# Validation : JSON Schema puis references croisees
# ---------------------------------------------------------------------------

def valider_schema(data_product: dict, schema: dict) -> list[str]:
    """Erreurs de validation JSON Schema, en francais, triees et stables."""
    declare = {
        cle: valeur
        for cle, valeur in data_product.items()
        if not cle.startswith("_")
    }
    validateur = Draft202012Validator(schema)
    erreurs = []
    for erreur in sorted(validateur.iter_errors(declare), key=str):
        chemin = ".".join(str(segment) for segment in erreur.absolute_path)
        ou = f" (champ {chemin})" if chemin else ""
        erreurs.append(f"Schema non respecte{ou} : {erreur.message}")
    return erreurs


def verifier_references(
    data_product: dict,
    fichiers_contrats: set[str],
    marts_entrepot: set[str],
) -> list[str]:
    """References croisees : marts, contrat, consommateurs, domaine."""
    problemes: list[str] = []

    for port in data_product.get("ports_de_sortie") or []:
        table = port.get("table") or ""
        if table not in base.MARTS_AUTORISES:
            problemes.append(
                f"Port de sortie inconnu : {table} n'est pas un mart "
                "autorise de l'entrepot."
            )
        elif table not in marts_entrepot:
            problemes.append(
                f"Port de sortie absent : le mart {table} n'existe pas "
                "dans l'entrepot DuckDB."
            )

    contrat = data_product.get("contrat_reference") or ""
    if contrat and contrat not in fichiers_contrats:
        problemes.append(
            f"Contrat introuvable : {contrat} n'existe pas dans contracts/."
        )

    for consommateur in data_product.get("consommateurs") or []:
        type_consommateur = consommateur.get("type") or ""
        identifiant = consommateur.get("id") or ""
        connus = CONSOMMATEURS_CONNUS.get(type_consommateur, frozenset())
        if identifiant not in connus:
            problemes.append(
                f"Consommateur inconnu : {type_consommateur} {identifiant} "
                "n'est ni une vue, ni un module, ni un export connu."
            )

    domaine = data_product.get("domaine")
    dossier = data_product.get("_domaine_dossier")
    if domaine and dossier and domaine != dossier:
        problemes.append(
            f"Domaine incoherent : le YAML est range dans {dossier}/ mais "
            f"declare le domaine {domaine}."
        )
    return problemes


# ---------------------------------------------------------------------------
# Etat vivant : fraicheur, volumes, contrat, maturite
# ---------------------------------------------------------------------------

def _marts_presents() -> set[str]:
    """Marts autorises reellement presents dans l'entrepot (lecture seule)."""
    presents: set[str] = set()
    connexion = base.connexion()
    try:
        lignes = connexion.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    finally:
        connexion.close()
    for (nom_table,) in lignes:
        if nom_table in base.MARTS_AUTORISES:
            presents.add(nom_table)
    return presents


def _compter_lignes(table: str) -> int | None:
    """Nombre de lignes d'un mart, ou None si la table est inaccessible."""
    try:
        lignes = base.requete(f"SELECT COUNT(*) AS nb FROM {table}")
    except Exception:
        return None
    return int(lignes[0]["nb"]) if lignes else None


def dernier_build_succes(etat: dict) -> str | None:
    """Horodatage du dernier build en succes d'etat_build.json, ou None."""
    if etat.get("statut") == "succes" and etat.get("horodatage"):
        return etat["horodatage"]
    for entree in etat.get("historique") or []:
        if entree.get("statut") == "succes" and entree.get("horodatage"):
            return entree["horodatage"]
    return None


def calculer_fraicheur(slo: dict, horodatage_build: str | None) -> dict:
    """Fraicheur reelle contre fraicheur promise (SLO fraicheur_heures)."""
    limite = float(slo.get("fraicheur_heures") or 0)
    if not horodatage_build:
        return {
            "statut": "inconnue",
            "age_heures": None,
            "limite_heures": limite,
            "dernier_build": None,
            "detail": "aucun build en succes dans etat_build.json",
        }
    age_secondes = (
        datetime.datetime.now()
        - datetime.datetime.fromisoformat(horodatage_build)
    ).total_seconds()
    age_heures = round(max(age_secondes, 0.0) / 3600, 1)
    a_jour = age_heures <= limite
    return {
        "statut": "a_jour" if a_jour else "depassee",
        "age_heures": age_heures,
        "limite_heures": limite,
        "dernier_build": horodatage_build,
        "detail": (
            f"dernier build en succes il y a {age_heures} h "
            f"(promesse : {limite:g} h)"
        ),
    }


def calculer_maturite(data_product: dict, contrat_resolu: bool) -> dict:
    """Score de maturite : documentation, contrat, SLO et owner presents."""
    description = str(data_product.get("description") or "")
    ports = data_product.get("ports_de_sortie") or []
    colonnes_documentees = bool(ports) and all(
        (colonne.get("sens") or "").strip()
        for port in ports
        for colonne in port.get("colonnes") or []
    )
    slo = data_product.get("slo") or {}
    owner = data_product.get("owner") or {}
    criteres = {
        "documentation": len(description.strip()) >= 30 and colonnes_documentees,
        "contrat": contrat_resolu,
        "slo": all(
            slo.get(cle) for cle in ("fraicheur_heures", "completude_pct", "disponibilite")
        ),
        "owner": bool((owner.get("nom") or "").strip() and (owner.get("role") or "").strip()),
    }
    return {
        "score": sum(1 for present in criteres.values() if present),
        "sur": len(CRITERES_MATURITE),
        "criteres": criteres,
    }


# ---------------------------------------------------------------------------
# Evaluation complete
# ---------------------------------------------------------------------------

def evaluer_data_product(
    data_product: dict,
    schema: dict,
    verdict_contrats: dict,
    marts_entrepot: set[str],
    horodatage_build: str | None,
) -> dict:
    """Evalue un data product : schema, references, etat vivant, maturite."""
    contrats_par_fichier = {
        contrat.get("fichier"): contrat
        for contrat in verdict_contrats.get("contrats") or []
    }
    problemes = valider_schema(data_product, schema)
    problemes += verifier_references(
        data_product, set(contrats_par_fichier), marts_entrepot
    )

    contrat_lie = contrats_par_fichier.get(
        data_product.get("contrat_reference")
    )
    fraicheur = calculer_fraicheur(
        data_product.get("slo") or {}, horodatage_build
    )
    ports = []
    for port in data_product.get("ports_de_sortie") or []:
        table = port.get("table") or ""
        ports.append(
            {
                "table": table,
                "grain": port.get("grain") or "",
                "colonnes": list(port.get("colonnes") or []),
                "nb_lignes": (
                    _compter_lignes(table) if table in marts_entrepot else None
                ),
            }
        )

    if problemes:
        statut = STATUT_INVALIDE
    elif (
        fraicheur["statut"] != "a_jour"
        or (contrat_lie or {}).get("statut") != moteur_contrats.STATUT_CONFORME
    ):
        statut = STATUT_DEGRADE
    else:
        statut = STATUT_SAIN

    return {
        "id": data_product.get("id"),
        "nom": data_product.get("nom") or data_product.get("id"),
        "domaine": data_product.get("domaine"),
        "fichier": data_product.get("_fichier"),
        "description": data_product.get("description") or "",
        "owner": data_product.get("owner") or {},
        "version": str(data_product.get("version") or ""),
        "changelog": list(data_product.get("changelog") or []),
        "ports_de_sortie": ports,
        "nb_lignes_total": sum(
            port["nb_lignes"] or 0 for port in ports
        ),
        "contrat": {
            "reference": data_product.get("contrat_reference"),
            "nom": (contrat_lie or {}).get("nom"),
            "version": (contrat_lie or {}).get("version"),
            "statut": (contrat_lie or {}).get("statut"),
        },
        "slo": data_product.get("slo") or {},
        "fraicheur": fraicheur,
        "consommateurs": list(data_product.get("consommateurs") or []),
        "question_exemple": data_product.get("question_exemple") or "",
        "maturite": calculer_maturite(data_product, contrat_lie is not None),
        "statut": statut,
        "problemes": problemes,
    }


def evaluer_tout(repertoire_dbt: str | None = None) -> dict:
    """Evalue tout le catalogue. Verdict global + detail par data product."""
    repertoire = repertoire_dbt or moteur_contrats.dossier_dbt()
    schema = charger_schema(repertoire)
    data_products = charger_data_products(repertoire)
    verdict_contrats = moteur_contrats.valider_tout(repertoire)
    marts_entrepot = _marts_presents()
    horodatage_build = dernier_build_succes(moteur_pipeline.lire_etat())

    evaluations = [
        evaluer_data_product(
            data_product, schema, verdict_contrats, marts_entrepot,
            horodatage_build,
        )
        for data_product in data_products
    ]
    domaines = sorted({e["domaine"] for e in evaluations if e["domaine"]})
    return {
        "verifie_le": datetime.datetime.now().isoformat(timespec="seconds"),
        "dossier": dossier_catalogue(repertoire),
        "domaines": domaines,
        "nb_domaines": len(domaines),
        "nb_data_products": len(evaluations),
        "nb_sains": sum(1 for e in evaluations if e["statut"] == STATUT_SAIN),
        "nb_invalides": sum(
            1 for e in evaluations if e["statut"] == STATUT_INVALIDE
        ),
        "tous_sains": bool(evaluations)
        and all(e["statut"] == STATUT_SAIN for e in evaluations),
        "data_products": evaluations,
    }
