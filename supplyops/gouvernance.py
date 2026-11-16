"""Gouvernance des contrats : amendement self-service des familles.

Le cas d'usage central : ajouter une famille marchande au referentiel
produits SANS passer par un developpeur. L'amendement est applique
ATOMIQUEMENT cote repo dbt :

- la famille est ajoutee dans TOUTES les listes accepted_values de la
  colonne famille (models/**/schema.yml et seeds/schema.yml), par
  insertion textuelle qui preserve l'indentation, l'ordre et le style
  de citation du YAML d'origine ;
- le contrat contracts/referentiel_produits.odcs.yaml est amende : bump
  de version mineur (1.0.0 -> 1.1.0) et entree de changelog ajoutee dans
  customProperties (date, auteur SupplyOps, description). La regle
  famille du contrat lit les valeurs dans schema.yml (controle
  valeurs_schema_dbt) : elle suit donc l'amendement sans reecriture ;
- tout est prepare en memoire, verifie (reparse YAML), puis ecrit
  fichier par fichier via temporaire + os.replace ; en cas d'echec en
  cours de route, les contenus d'origine sont restaures.

Garde-fous : nom valide (non vide, caracteres simples, pas de doublon),
refus si un build est en cours (verrou .pipeline.lock du pipeline).
La suppression d'une famille n'est volontairement PAS proposee : seule
l'extension du contrat est un amendement sans risque pour l'historique.
"""

from __future__ import annotations

import datetime
import os
import re
import tempfile

import yaml

from pipeline import moteur as moteur_pipeline
from supplyops import magasin

NOM_CONTRAT_REFERENTIEL = "referentiel_produits.odcs.yaml"

MOTIF_NOM_FAMILLE = re.compile(
    r"^[0-9A-Za-zÀ-ÖØ-öø-ÿ][0-9A-Za-zÀ-ÖØ-öø-ÿ' -]{0,39}$"
)
"""Caracteres simples : lettres (accents compris), chiffres, espaces,
apostrophes et traits d'union, 40 caracteres au plus."""


class ErreurGouvernance(Exception):
    """Amendement refuse : le motif est le message de l'exception."""


class ErreurVerrouBuild(ErreurGouvernance):
    """Amendement refuse parce qu'un build dbt est en cours."""


# ---------------------------------------------------------------------------
# Chemins et decouverte des fichiers accepted_values
# ---------------------------------------------------------------------------

def chemin_contrat_referentiel() -> str:
    """contracts/referentiel_produits.odcs.yaml dans le repo dbt."""
    return os.path.join(
        magasin.dossier_dbt(), "contracts", NOM_CONTRAT_REFERENTIEL
    )


def _fichiers_schema() -> list[str]:
    """Tous les schema.yml du repo dbt : models/ (recursif) et seeds/."""
    repo = magasin.dossier_dbt()
    chemins: list[str] = []
    dossier_modeles = os.path.join(repo, "models")
    for racine, _, noms in os.walk(dossier_modeles):
        for nom in sorted(noms):
            if nom in ("schema.yml", "schema.yaml"):
                chemins.append(os.path.join(racine, nom))
    for nom in ("schema.yml", "schema.yaml"):
        chemin = os.path.join(repo, "seeds", nom)
        if os.path.exists(chemin):
            chemins.append(chemin)
    return sorted(chemins)


def _nb_listes_famille(contenu_yaml: dict) -> int:
    """Nombre de listes accepted_values portees par une colonne famille."""
    nb = 0
    blocs = list(contenu_yaml.get("models") or []) + list(
        contenu_yaml.get("seeds") or []
    )
    for bloc in blocs:
        for colonne in bloc.get("columns") or []:
            if colonne.get("name") != "famille":
                continue
            for test in colonne.get("tests") or []:
                if isinstance(test, dict) and "accepted_values" in test:
                    nb += 1
    return nb


def fichiers_accepted_values() -> list[dict]:
    """Fichiers dbt portant des accepted_values de famille.

    Retourne [{chemin, relatif, nb_listes}] : chaque occurrence compte,
    un meme fichier peut porter plusieurs listes (plusieurs modeles).
    """
    repo = magasin.dossier_dbt()
    resultats: list[dict] = []
    for chemin in _fichiers_schema():
        try:
            with open(chemin, encoding="utf-8") as fichier:
                contenu = yaml.safe_load(fichier)
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(contenu, dict):
            continue
        nb = _nb_listes_famille(contenu)
        if nb:
            resultats.append(
                {
                    "chemin": chemin,
                    "relatif": os.path.relpath(chemin, repo),
                    "nb_listes": nb,
                }
            )
    return resultats


# ---------------------------------------------------------------------------
# Insertion textuelle dans les listes accepted_values (format preserve)
# ---------------------------------------------------------------------------

_MOTIF_COLONNE_FAMILLE = re.compile(r"^(\s*)- name:\s*famille\s*$")
_MOTIF_NOM_COLONNE = re.compile(r"^(\s*)- name:\s*\S")
_MOTIF_ACCEPTED = re.compile(r"^\s*- accepted_values:\s*$")
_MOTIF_VALUES = re.compile(r"^(\s*)values:\s*$")
_MOTIF_ITEM = re.compile(r"^(\s*)- (.+?)\s*$")


def inserer_famille(texte: str, famille: str) -> tuple[str, int]:
    """Ajoute la famille a chaque liste accepted_values de la colonne famille.

    Insertion ligne a ligne : la nouvelle valeur prend l'indentation et le
    style de citation du dernier element de la liste. Le reste du fichier
    n'est pas touche (commentaires et mise en forme preserves). Retourne
    (texte modifie, nombre d'insertions).
    """
    lignes = texte.splitlines(keepends=True)
    nb_insertions = 0
    i = 0
    while i < len(lignes):
        entete = _MOTIF_COLONNE_FAMILLE.match(lignes[i])
        if not entete:
            i += 1
            continue
        indent_colonne = len(entete.group(1))
        j = i + 1
        while j < len(lignes):
            autre = _MOTIF_NOM_COLONNE.match(lignes[j])
            if autre and len(autre.group(1)) <= indent_colonne:
                break  # colonne suivante : famille n'a pas d'accepted_values
            if _MOTIF_ACCEPTED.match(lignes[j]):
                k = j + 1
                while k < len(lignes) and not _MOTIF_VALUES.match(lignes[k]):
                    if _MOTIF_NOM_COLONNE.match(lignes[k]):
                        break
                    k += 1
                values = (
                    _MOTIF_VALUES.match(lignes[k]) if k < len(lignes) else None
                )
                if values:
                    indent_values = len(values.group(1))
                    dernier = None
                    m = k + 1
                    while m < len(lignes):
                        item = _MOTIF_ITEM.match(lignes[m])
                        if not item or len(item.group(1)) <= indent_values:
                            break
                        dernier = m
                        m += 1
                    if dernier is not None:
                        item = _MOTIF_ITEM.match(lignes[dernier])
                        indentation = item.group(1)
                        valeur_brute = item.group(2)
                        fin = "\r\n" if lignes[dernier].endswith("\r\n") else "\n"
                        citation = (
                            f'"{famille}"'
                            if valeur_brute.startswith('"')
                            else famille
                        )
                        lignes.insert(
                            dernier + 1, f"{indentation}- {citation}{fin}"
                        )
                        nb_insertions += 1
                        j = dernier + 1
                break
            j += 1
        i = max(j, i + 1)
    return "".join(lignes), nb_insertions


# ---------------------------------------------------------------------------
# Amendement du contrat ODCS : version + changelog (format preserve)
# ---------------------------------------------------------------------------

_MOTIF_VERSION = re.compile(r"^version:\s*[\"']?(\d+)\.(\d+)\.(\d+)[\"']?\s*$")


def _bump_mineur(version: str) -> str:
    morceaux = str(version).split(".")
    if len(morceaux) != 3 or not all(p.isdigit() for p in morceaux):
        raise ErreurGouvernance(
            f"Version de contrat illisible : {version!r} (X.Y.Z attendu)."
        )
    return f"{morceaux[0]}.{int(morceaux[1]) + 1}.0"


def _bloc_changelog(version: str, description: str, date: str) -> str:
    return (
        "  - property: changelog\n"
        "    value:\n"
        f'      date: "{date}"\n'
        f"      version: {version}\n"
        "      auteur: SupplyOps\n"
        f"      description: {description}\n"
    )


def amender_contrat_texte(
    texte: str, description: str, date: str
) -> tuple[str, str, str]:
    """Bump mineur de la version + entree de changelog dans customProperties.

    La regle famille du contrat (controle valeurs_schema_dbt) lit les
    valeurs dans schema.yml : elle n'a pas de liste en dur a reecrire.
    Retourne (texte modifie, version avant, version apres).
    """
    lignes = texte.splitlines(keepends=True)
    version_avant = version_apres = None
    for i, ligne in enumerate(lignes):
        correspondance = _MOTIF_VERSION.match(ligne)
        if correspondance:
            version_avant = ".".join(correspondance.groups())
            version_apres = _bump_mineur(version_avant)
            fin = "\r\n" if ligne.endswith("\r\n") else "\n"
            lignes[i] = f"version: {version_apres}{fin}"
            break
    if version_avant is None:
        raise ErreurGouvernance(
            "Ligne 'version: X.Y.Z' introuvable dans le contrat."
        )

    bloc = _bloc_changelog(version_apres, description, date)
    index_custom = None
    for i, ligne in enumerate(lignes):
        if re.match(r"^customProperties:\s*$", ligne):
            index_custom = i
            break
    if index_custom is None:
        if lignes and not lignes[-1].endswith("\n"):
            lignes[-1] += "\n"
        lignes.append("\ncustomProperties:\n" + bloc)
    else:
        fin_bloc = index_custom + 1
        while fin_bloc < len(lignes):
            ligne = lignes[fin_bloc]
            if ligne.strip() and not ligne[0] in (" ", "\t", "#"):
                break
            fin_bloc += 1
        lignes.insert(fin_bloc, bloc)
    return "".join(lignes), version_avant, version_apres


# ---------------------------------------------------------------------------
# Validation du nom propose
# ---------------------------------------------------------------------------

def valider_nom_famille(nom) -> str:
    """Nom de famille valide, sinon ErreurGouvernance motivee."""
    texte = str(nom or "").strip()
    if not texte:
        raise ErreurGouvernance("Le nom de la famille est obligatoire.")
    if not MOTIF_NOM_FAMILLE.match(texte):
        raise ErreurGouvernance(
            f"Nom de famille invalide : {texte!r}. Caracteres autorises : "
            "lettres, chiffres, espaces, apostrophes et traits d'union "
            "(40 caracteres au plus)."
        )
    existantes = magasin.familles_autorisees()
    for existante in existantes:
        if existante.strip().lower() == texte.lower():
            raise ErreurGouvernance(
                f"La famille {existante!r} existe deja dans le contrat."
            )
    return texte


# ---------------------------------------------------------------------------
# Etat pour la vue Gouvernance
# ---------------------------------------------------------------------------

def _contrat_referentiel() -> dict:
    """Version et changelog du contrat referentiel produits."""
    chemin = chemin_contrat_referentiel()
    if not os.path.exists(chemin):
        return {"fichier": NOM_CONTRAT_REFERENTIEL, "version": None, "changelog": []}
    with open(chemin, encoding="utf-8") as fichier:
        contrat = yaml.safe_load(fichier) or {}
    changelog = [
        entree.get("value")
        for entree in contrat.get("customProperties") or []
        if entree.get("property") == "changelog"
        and isinstance(entree.get("value"), dict)
    ]
    changelog.reverse()  # le plus recent d'abord
    return {
        "fichier": NOM_CONTRAT_REFERENTIEL,
        "nom": contrat.get("name"),
        "version": str(contrat.get("version") or ""),
        "changelog": changelog,
    }


def etat_familles() -> dict:
    """Etat complet pour la vue Gouvernance de SupplyOps."""
    familles = magasin.familles_autorisees()
    compte: dict[str, int] = {famille: 0 for famille in familles}
    for produit in magasin.lire_table("produits"):
        famille = produit.get("famille") or ""
        if famille in compte:
            compte[famille] += 1
        else:
            compte[famille] = compte.get(famille, 0) + 1
    contrat = _contrat_referentiel()
    fichiers = fichiers_accepted_values()
    version = contrat.get("version")
    return {
        "familles": [
            {"nom": famille, "nb_produits": compte.get(famille, 0)}
            for famille in familles
        ],
        "contrat": contrat,
        "fichiers_dbt": fichiers,
        "nb_listes_dbt": sum(f["nb_listes"] for f in fichiers),
        "version_apres_amendement": (
            _bump_mineur(version) if version else None
        ),
        "build_en_cours": os.path.exists(moteur_pipeline.chemin_verrou()),
    }


# ---------------------------------------------------------------------------
# Amendement atomique
# ---------------------------------------------------------------------------

def _ecrire_atomique(chemin: str, contenu: str) -> None:
    descripteur, chemin_temporaire = tempfile.mkstemp(
        prefix=".amendement.", suffix=".tmp", dir=os.path.dirname(chemin)
    )
    try:
        with os.fdopen(descripteur, "w", encoding="utf-8", newline="") as fichier:
            fichier.write(contenu)
        os.replace(chemin_temporaire, chemin)
    except BaseException:
        if os.path.exists(chemin_temporaire):
            os.remove(chemin_temporaire)
        raise


def amender_famille(nom, date: str | None = None) -> dict:
    """Ajoute une famille au contrat : schema.yml partout + contrat ODCS.

    Tout est prepare et verifie en memoire avant la moindre ecriture ;
    les ecritures sont ensuite atomiques fichier par fichier, avec
    restauration des originaux si l'une d'elles echoue.
    """
    if os.path.exists(moteur_pipeline.chemin_verrou()):
        raise ErreurVerrouBuild(
            "Amendement refuse : un build dbt est en cours "
            "(verrou .pipeline.lock present). Reessayer apres le build."
        )
    famille = valider_nom_famille(nom)
    date_amendement = date or datetime.date.today().isoformat()

    fichiers = fichiers_accepted_values()
    if not fichiers:
        raise ErreurGouvernance(
            "Aucune liste accepted_values de famille trouvee dans le repo "
            "dbt : rien a amender."
        )

    # 1. Preparation en memoire : schema.yml puis contrat.
    originaux: dict[str, str] = {}
    nouveaux: dict[str, str] = {}
    nb_listes = 0
    for fichier in fichiers:
        with open(fichier["chemin"], encoding="utf-8", newline="") as flux:
            texte = flux.read()
        modifie, nb = inserer_famille(texte, famille)
        if nb != fichier["nb_listes"]:
            raise ErreurGouvernance(
                f"Insertion incomplete dans {fichier['relatif']} : "
                f"{nb} liste(s) modifiee(s) sur {fichier['nb_listes']}."
            )
        originaux[fichier["chemin"]] = texte
        nouveaux[fichier["chemin"]] = modifie
        nb_listes += nb

    chemin_contrat = chemin_contrat_referentiel()
    if not os.path.exists(chemin_contrat):
        raise ErreurGouvernance(
            f"Contrat introuvable : {chemin_contrat}."
        )
    with open(chemin_contrat, encoding="utf-8", newline="") as flux:
        texte_contrat = flux.read()
    description = f"Ajout de la famille {famille}"
    contrat_modifie, version_avant, version_apres = amender_contrat_texte(
        texte_contrat, description, date_amendement
    )
    originaux[chemin_contrat] = texte_contrat
    nouveaux[chemin_contrat] = contrat_modifie

    # 2. Verification : chaque nouveau contenu doit rester un YAML valide
    #    et porter effectivement la famille.
    for chemin, contenu in nouveaux.items():
        try:
            yaml.safe_load(contenu)
        except yaml.YAMLError as erreur:
            raise ErreurGouvernance(
                f"Amendement annule : {os.path.basename(chemin)} "
                f"deviendrait illisible ({erreur})."
            )
        if chemin != chemin_contrat and famille not in contenu:
            raise ErreurGouvernance(
                f"Amendement annule : {os.path.basename(chemin)} "
                "ne porte pas la nouvelle famille."
            )

    # 3. Ecriture atomique fichier par fichier, restauration si echec.
    ecrits: list[str] = []
    try:
        for chemin, contenu in nouveaux.items():
            _ecrire_atomique(chemin, contenu)
            ecrits.append(chemin)
    except BaseException:
        for chemin in ecrits:
            _ecrire_atomique(chemin, originaux[chemin])
        raise

    return {
        "famille": famille,
        "version_avant": version_avant,
        "version_apres": version_apres,
        "date": date_amendement,
        "description": description,
        "fichiers_dbt": [f["relatif"] for f in fichiers],
        "nb_listes_modifiees": nb_listes,
        "contrat": os.path.relpath(chemin_contrat, magasin.dossier_dbt()),
    }
