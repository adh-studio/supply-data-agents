"""Couche d'acces aux seeds CSV du repo dbt (lecture ET ecriture controlee).

Regles non negociables :
- les fins de ligne du fichier d'origine sont preservees octet pour octet
  (les seeds historiques sont en CRLF \r\n, elles le restent) ;
- toute ecriture est atomique : fichier temporaire dans le meme dossier
  puis os.replace ;
- aucune ecriture sans validation : famille lue dynamiquement depuis le
  contrat models/staging/schema.yml du repo dbt (accepted_values),
  references fournisseur / entrepot / produit existantes, prix et
  quantites positifs, dates ISO. Tout refus leve ErreurValidation avec
  un motif clair ;
- aucun changement de schema des CSV : memes colonnes, meme ordre.

Chemins : DBT_DIR designe le repo dbt (defaut ../supply-dbt-warehouse),
OPS_SEEDS_DIR permet de pointer un dossier de seeds isole pour les tests
(le contrat schema.yml est alors cherche a cote via OPS_SCHEMA_PATH, ou
retombe sur celui du repo dbt).
"""

from __future__ import annotations

import csv
import datetime
import io
import os
import re
import tempfile

import yaml

RACINE_PROJET = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DBT_DIR_DEFAUT = os.path.join(RACINE_PROJET, "..", "supply-dbt-warehouse")

TERMINATEUR_DEFAUT = "\r\n"

CANAUX_VALIDES = ("magasin", "web")

COLONNES = {
    "produits": [
        "id_produit", "nom_produit", "famille",
        "id_fournisseur", "prix_achat", "prix_vente",
    ],
    "fournisseurs": [
        "id_fournisseur", "nom_fournisseur", "pays",
        "mode_fret", "delai_contractuel_jours", "note_rse",
    ],
    "entrepots": [
        "id_entrepot", "nom_entrepot", "ville", "capacite_m3", "type_entrepot",
    ],
    "commandes_achats": [
        "id_commande", "id_produit", "id_fournisseur", "id_entrepot",
        "date_commande", "quantite_commandee", "statut",
    ],
    "receptions": [
        "id_reception", "id_commande", "date_reception", "quantite_recue",
    ],
    "stocks": [
        "id_produit", "id_entrepot", "date_inventaire", "quantite_disponible",
    ],
    "ventes": [
        "id_vente", "date_vente", "id_produit", "id_entrepot",
        "canal", "quantite", "prix_unitaire",
    ],
}

PREFIXES_ID = {
    "produits": ("id_produit", "P"),
    "ventes": ("id_vente", "V"),
    "commandes_achats": ("id_commande", "CA"),
    "receptions": ("id_reception", "R"),
}


class ErreurValidation(Exception):
    """Refus d'ecriture : le motif est le message de l'exception."""


# ---------------------------------------------------------------------------
# Chemins
# ---------------------------------------------------------------------------

def dossier_dbt() -> str:
    """Chemin absolu du repo dbt (DBT_DIR, defaut ../supply-dbt-warehouse)."""
    return os.path.abspath(os.environ.get("DBT_DIR", DBT_DIR_DEFAUT))


def dossier_seeds() -> str:
    """Dossier des seeds CSV (OPS_SEEDS_DIR pour les tests, sinon DBT_DIR/seeds)."""
    surcharge = os.environ.get("OPS_SEEDS_DIR")
    if surcharge:
        return os.path.abspath(surcharge)
    return os.path.join(dossier_dbt(), "seeds")


def chemin_table(nom: str) -> str:
    """Chemin du CSV d'une table seed connue."""
    if nom not in COLONNES:
        raise ErreurValidation(f"Table seed inconnue : {nom}.")
    return os.path.join(dossier_seeds(), nom + ".csv")


def chemin_schema() -> str:
    """Chemin du contrat schema.yml (OPS_SCHEMA_PATH pour les tests)."""
    surcharge = os.environ.get("OPS_SCHEMA_PATH")
    if surcharge:
        return os.path.abspath(surcharge)
    return os.path.join(dossier_dbt(), "models", "staging", "schema.yml")


# ---------------------------------------------------------------------------
# Lecture / ecriture avec preservation du terminateur de ligne
# ---------------------------------------------------------------------------

def _detecter_terminateur(octets: bytes) -> str:
    """Terminateur de ligne du fichier : CRLF si present, sinon LF."""
    return "\r\n" if b"\r\n" in octets else "\n"


def lire_table(nom: str) -> list[dict]:
    """Lit un seed CSV et retourne ses lignes (liste de dicts ordonnes)."""
    chemin = chemin_table(nom)
    if not os.path.exists(chemin):
        raise ErreurValidation(f"Seed introuvable : {chemin}.")
    with open(chemin, "rb") as fichier:
        octets = fichier.read()
    lecteur = csv.DictReader(io.StringIO(octets.decode("utf-8")))
    return [dict(ligne) for ligne in lecteur]


def ecrire_table(nom: str, lignes: list[dict]) -> str:
    """Ecrit un seed CSV de facon atomique, terminateur d'origine preserve.

    Les colonnes et leur ordre sont imposes par COLONNES : aucune ligne ne
    peut introduire ou omettre une colonne. Retourne le chemin ecrit.
    """
    chemin = chemin_table(nom)
    colonnes = COLONNES[nom]
    terminateur = TERMINATEUR_DEFAUT
    if os.path.exists(chemin):
        with open(chemin, "rb") as fichier:
            terminateur = _detecter_terminateur(fichier.read())

    for ligne in lignes:
        if sorted(ligne.keys()) != sorted(colonnes):
            raise ErreurValidation(
                f"Colonnes invalides pour {nom} : {sorted(ligne.keys())} "
                f"(attendu : {sorted(colonnes)})."
            )

    tampon = io.StringIO()
    scribe = csv.DictWriter(
        tampon, fieldnames=colonnes, lineterminator=terminateur
    )
    scribe.writeheader()
    for ligne in lignes:
        scribe.writerow({cle: str(ligne[cle]) for cle in colonnes})

    descripteur, chemin_temporaire = tempfile.mkstemp(
        prefix="." + nom + ".", suffix=".tmp", dir=os.path.dirname(chemin)
    )
    try:
        with os.fdopen(descripteur, "wb") as fichier:
            fichier.write(tampon.getvalue().encode("utf-8"))
        os.replace(chemin_temporaire, chemin)
    except BaseException:
        if os.path.exists(chemin_temporaire):
            os.remove(chemin_temporaire)
        raise
    return chemin


# ---------------------------------------------------------------------------
# Contrat : familles autorisees lues depuis schema.yml
# ---------------------------------------------------------------------------

def familles_autorisees() -> list[str]:
    """Liste des familles acceptees par le contrat dbt (accepted_values).

    Lue dynamiquement dans models/staging/schema.yml : la colonne famille
    du modele stg_produits. Si le contrat change, SupplyOps suit sans
    modification de code.
    """
    chemin = chemin_schema()
    if not os.path.exists(chemin):
        raise ErreurValidation(f"Contrat schema.yml introuvable : {chemin}.")
    with open(chemin, encoding="utf-8") as fichier:
        contrat = yaml.safe_load(fichier)
    for modele in contrat.get("models") or []:
        if modele.get("name") != "stg_produits":
            continue
        for colonne in modele.get("columns") or []:
            if colonne.get("name") != "famille":
                continue
            for test in colonne.get("tests") or []:
                if isinstance(test, dict) and "accepted_values" in test:
                    valeurs = test["accepted_values"].get("values") or []
                    return [str(valeur) for valeur in valeurs]
    raise ErreurValidation(
        "Contrat illisible : accepted_values de la famille introuvable "
        "dans stg_produits (models/staging/schema.yml)."
    )


# ---------------------------------------------------------------------------
# Generation d'identifiants d'apres l'existant
# ---------------------------------------------------------------------------

def generer_id(nom_table: str, lignes: list[dict] | None = None) -> str:
    """Prochain identifiant de la table, d'apres les ids existants.

    Exemple : produits P0001..P0042 -> P0043 ; ventes V00750 -> V00751.
    La largeur numerique est celle observee dans le seed (zero-paddee).
    """
    if nom_table not in PREFIXES_ID:
        raise ErreurValidation(
            f"Pas de generation d'identifiant pour la table {nom_table}."
        )
    colonne, prefixe = PREFIXES_ID[nom_table]
    if lignes is None:
        lignes = lire_table(nom_table)
    motif = re.compile(r"^" + re.escape(prefixe) + r"(\d+)$")
    numeros: list[str] = []
    for ligne in lignes:
        correspondance = motif.match(str(ligne.get(colonne, "")))
        if correspondance:
            numeros.append(correspondance.group(1))
    if not numeros:
        return prefixe + "0001"
    largeur = max(len(numero) for numero in numeros)
    prochain = max(int(numero) for numero in numeros) + 1
    return prefixe + str(prochain).zfill(largeur)


# ---------------------------------------------------------------------------
# Validations elementaires
# ---------------------------------------------------------------------------

def _texte_obligatoire(valeur, champ: str) -> str:
    texte = str(valeur or "").strip()
    if not texte:
        raise ErreurValidation(f"Champ obligatoire manquant : {champ}.")
    return texte


def _entier_positif(valeur, champ: str, strict: bool = True) -> int:
    """Entier > 0 (ou >= 0 si strict=False). Refus motive sinon."""
    try:
        nombre = int(str(valeur).strip())
    except (TypeError, ValueError):
        raise ErreurValidation(
            f"{champ} invalide : {valeur!r} n'est pas un entier."
        ) from None
    if strict and nombre <= 0:
        raise ErreurValidation(
            f"{champ} invalide : {nombre} (strictement positif attendu)."
        )
    if not strict and nombre < 0:
        raise ErreurValidation(
            f"{champ} invalide : {nombre} (positif ou nul attendu)."
        )
    return nombre


def _date_iso(valeur, champ: str, defaut_aujourdhui: bool = False) -> str:
    """Date au format ISO AAAA-MM-JJ (date du jour si absente et permise)."""
    texte = str(valeur or "").strip()
    if not texte:
        if defaut_aujourdhui:
            return datetime.date.today().isoformat()
        raise ErreurValidation(f"Champ obligatoire manquant : {champ}.")
    try:
        return datetime.date.fromisoformat(texte).isoformat()
    except ValueError:
        raise ErreurValidation(
            f"{champ} invalide : {texte!r} (format ISO AAAA-MM-JJ attendu)."
        ) from None


def _verifier_famille(famille: str) -> str:
    familles = familles_autorisees()
    if famille not in familles:
        raise ErreurValidation(
            f"Famille inconnue du contrat : {famille!r}. "
            f"Familles autorisees (models/staging/schema.yml) : "
            + ", ".join(familles) + "."
        )
    return famille


def _verifier_reference(
    nom_table: str, colonne: str, valeur: str, libelle: str
) -> dict:
    """Verifie qu'une reference existe et retourne la ligne correspondante."""
    for ligne in lire_table(nom_table):
        if ligne.get(colonne) == valeur:
            return ligne
    raise ErreurValidation(
        f"{libelle} inexistant : {valeur!r} (absent de {nom_table}.csv)."
    )


def _avertissement_marge(prix_achat: int, prix_vente: int) -> list[str]:
    if prix_vente <= prix_achat:
        return [
            f"prix_vente ({prix_vente} MGA) inferieur ou egal au prix_achat "
            f"({prix_achat} MGA) : marge nulle ou negative."
        ]
    return []


# ---------------------------------------------------------------------------
# Produits
# ---------------------------------------------------------------------------

def lister_produits() -> list[dict]:
    return lire_table("produits")


def lister_fournisseurs() -> list[dict]:
    return lire_table("fournisseurs")


def lister_entrepots() -> list[dict]:
    return lire_table("entrepots")


def creer_produit(
    nom_produit, famille, id_fournisseur, prix_achat, prix_vente
) -> dict:
    """Cree un produit valide et retourne {produit, avertissements}."""
    nom = _texte_obligatoire(nom_produit, "nom_produit")
    famille = _verifier_famille(_texte_obligatoire(famille, "famille"))
    fournisseur = _texte_obligatoire(id_fournisseur, "id_fournisseur")
    _verifier_reference(
        "fournisseurs", "id_fournisseur", fournisseur, "Fournisseur"
    )
    achat = _entier_positif(prix_achat, "prix_achat")
    vente = _entier_positif(prix_vente, "prix_vente")

    produits = lire_table("produits")
    produit = {
        "id_produit": generer_id("produits", produits),
        "nom_produit": nom,
        "famille": famille,
        "id_fournisseur": fournisseur,
        "prix_achat": str(achat),
        "prix_vente": str(vente),
    }
    produits.append(produit)
    ecrire_table("produits", produits)
    return {
        "produit": produit,
        "avertissements": _avertissement_marge(achat, vente),
    }


def modifier_produit(id_produit: str, champs: dict) -> dict:
    """Modifie un produit existant (nom, famille, fournisseur, prix)."""
    modifiables = {
        "nom_produit", "famille", "id_fournisseur", "prix_achat", "prix_vente"
    }
    inconnus = set(champs) - modifiables
    if inconnus:
        raise ErreurValidation(
            "Champs non modifiables : " + ", ".join(sorted(inconnus)) + "."
        )
    if not champs:
        raise ErreurValidation("Aucun champ a modifier.")

    produits = lire_table("produits")
    cible = next(
        (p for p in produits if p["id_produit"] == id_produit), None
    )
    if cible is None:
        raise ErreurValidation(f"Produit inexistant : {id_produit!r}.")

    nouveau = dict(cible)
    if "nom_produit" in champs:
        nouveau["nom_produit"] = _texte_obligatoire(
            champs["nom_produit"], "nom_produit"
        )
    if "famille" in champs:
        nouveau["famille"] = _verifier_famille(
            _texte_obligatoire(champs["famille"], "famille")
        )
    if "id_fournisseur" in champs:
        fournisseur = _texte_obligatoire(
            champs["id_fournisseur"], "id_fournisseur"
        )
        _verifier_reference(
            "fournisseurs", "id_fournisseur", fournisseur, "Fournisseur"
        )
        nouveau["id_fournisseur"] = fournisseur
    if "prix_achat" in champs:
        nouveau["prix_achat"] = str(
            _entier_positif(champs["prix_achat"], "prix_achat")
        )
    if "prix_vente" in champs:
        nouveau["prix_vente"] = str(
            _entier_positif(champs["prix_vente"], "prix_vente")
        )

    cible.update(nouveau)
    ecrire_table("produits", produits)
    return {
        "produit": cible,
        "avertissements": _avertissement_marge(
            int(cible["prix_achat"]), int(cible["prix_vente"])
        ),
    }


def retirer_produit(id_produit: str) -> dict:
    """Retire un produit du catalogue (pas de changement de schema CSV).

    Refus si le produit a des ventes ou des lots d'import : l'historique
    commercial ne se reecrit pas. Sinon le produit est retire, ainsi que
    ses lignes d'inventaire (sans quoi les tests de reference du contrat
    dbt echoueraient sur stocks -> produits).
    """
    produits = lire_table("produits")
    if not any(p["id_produit"] == id_produit for p in produits):
        raise ErreurValidation(f"Produit inexistant : {id_produit!r}.")

    ventes = lire_table("ventes")
    nb_ventes = sum(1 for v in ventes if v["id_produit"] == id_produit)
    if nb_ventes:
        raise ErreurValidation(
            f"Retrait refuse : le produit {id_produit} a {nb_ventes} "
            "vente(s) enregistree(s). L'historique commercial est conserve."
        )
    commandes = lire_table("commandes_achats")
    nb_commandes = sum(
        1 for c in commandes if c["id_produit"] == id_produit
    )
    if nb_commandes:
        raise ErreurValidation(
            f"Retrait refuse : le produit {id_produit} figure dans "
            f"{nb_commandes} lot(s) d'import."
        )

    stocks = lire_table("stocks")
    stocks_conserves = [s for s in stocks if s["id_produit"] != id_produit]
    nb_stocks_retires = len(stocks) - len(stocks_conserves)
    if nb_stocks_retires:
        ecrire_table("stocks", stocks_conserves)
    ecrire_table(
        "produits", [p for p in produits if p["id_produit"] != id_produit]
    )
    return {
        "id_produit": id_produit,
        "lignes_inventaire_retirees": nb_stocks_retires,
    }


# ---------------------------------------------------------------------------
# Inventaire
# ---------------------------------------------------------------------------

def saisir_inventaire(
    id_produit, id_entrepot, quantite, date_inventaire=None
) -> dict:
    """Photographie de stock produit x entrepot a une date (defaut du jour).

    La combinaison (produit, entrepot, date) est unique dans le contrat :
    une nouvelle saisie du meme jour remplace la precedente.
    """
    produit = _texte_obligatoire(id_produit, "id_produit")
    entrepot = _texte_obligatoire(id_entrepot, "id_entrepot")
    _verifier_reference("produits", "id_produit", produit, "Produit")
    _verifier_reference("entrepots", "id_entrepot", entrepot, "Entrepot")
    nombre = _entier_positif(quantite, "quantite_disponible", strict=False)
    date = _date_iso(date_inventaire, "date_inventaire", defaut_aujourdhui=True)

    stocks = lire_table("stocks")
    ligne = {
        "id_produit": produit,
        "id_entrepot": entrepot,
        "date_inventaire": date,
        "quantite_disponible": str(nombre),
    }
    remplacee = False
    for existante in stocks:
        if (
            existante["id_produit"] == produit
            and existante["id_entrepot"] == entrepot
            and existante["date_inventaire"] == date
        ):
            existante["quantite_disponible"] = str(nombre)
            remplacee = True
            break
    if not remplacee:
        stocks.append(ligne)
    ecrire_table("stocks", stocks)
    return {"ligne": ligne, "remplacee": remplacee}


def inventaire_courant() -> list[dict]:
    """Derniere photographie connue par produit x entrepot."""
    dernieres: dict[tuple[str, str], dict] = {}
    for ligne in lire_table("stocks"):
        cle = (ligne["id_produit"], ligne["id_entrepot"])
        if (
            cle not in dernieres
            or ligne["date_inventaire"] >= dernieres[cle]["date_inventaire"]
        ):
            dernieres[cle] = ligne
    return sorted(
        dernieres.values(),
        key=lambda l: (l["id_produit"], l["id_entrepot"]),
    )


# ---------------------------------------------------------------------------
# Ventes
# ---------------------------------------------------------------------------

def saisir_vente(
    id_produit, id_entrepot, canal, quantite,
    prix_unitaire=None, date_vente=None,
) -> dict:
    """Saisie de vente rapide : prix pre-rempli au prix catalogue si absent."""
    produit_id = _texte_obligatoire(id_produit, "id_produit")
    entrepot = _texte_obligatoire(id_entrepot, "id_entrepot")
    produit = _verifier_reference(
        "produits", "id_produit", produit_id, "Produit"
    )
    _verifier_reference("entrepots", "id_entrepot", entrepot, "Entrepot")
    canal = _texte_obligatoire(canal, "canal")
    if canal not in CANAUX_VALIDES:
        raise ErreurValidation(
            f"Canal inconnu : {canal!r} "
            f"(attendu : {', '.join(CANAUX_VALIDES)})."
        )
    nombre = _entier_positif(quantite, "quantite")
    if prix_unitaire in (None, ""):
        prix = int(produit["prix_vente"])
    else:
        prix = _entier_positif(prix_unitaire, "prix_unitaire")
    date = _date_iso(date_vente, "date_vente", defaut_aujourdhui=True)

    ventes = lire_table("ventes")
    ligne = {
        "id_vente": generer_id("ventes", ventes),
        "date_vente": date,
        "id_produit": produit_id,
        "id_entrepot": entrepot,
        "canal": canal,
        "quantite": str(nombre),
        "prix_unitaire": str(prix),
    }
    ventes.append(ligne)
    ecrire_table("ventes", ventes)
    return {"vente": ligne}


def dernieres_ventes(limite: int = 30) -> list[dict]:
    """Journal des dernieres ventes saisies (les plus recentes d'abord)."""
    ventes = lire_table("ventes")
    return sorted(
        ventes, key=lambda v: (v["date_vente"], v["id_vente"]), reverse=True
    )[:limite]


# ---------------------------------------------------------------------------
# Lots d'import (commandes_achats) et receptions
# ---------------------------------------------------------------------------

def creer_lot(
    id_fournisseur, id_entrepot, lignes, date_commande=None
) -> dict:
    """Cree un lot d'import : une commande d'achat par produit du lot.

    lignes : liste de {id_produit, quantite}. Le mode de fret (AIR/SEA)
    et le delai promis sont ceux du contrat fournisseur (fournisseurs.csv),
    le schema des CSV n'etant pas modifiable.
    """
    fournisseur_id = _texte_obligatoire(id_fournisseur, "id_fournisseur")
    entrepot = _texte_obligatoire(id_entrepot, "id_entrepot")
    fournisseur = _verifier_reference(
        "fournisseurs", "id_fournisseur", fournisseur_id, "Fournisseur"
    )
    _verifier_reference("entrepots", "id_entrepot", entrepot, "Entrepot")
    date = _date_iso(date_commande, "date_commande", defaut_aujourdhui=True)
    if not isinstance(lignes, list) or not lignes:
        raise ErreurValidation(
            "Un lot d'import doit contenir au moins une ligne produit."
        )

    produits = {p["id_produit"] for p in lire_table("produits")}
    lignes_validees: list[tuple[str, int]] = []
    vus: set[str] = set()
    for ligne in lignes:
        if not isinstance(ligne, dict):
            raise ErreurValidation(
                "Ligne de lot invalide : objet {id_produit, quantite} attendu."
            )
        produit = _texte_obligatoire(ligne.get("id_produit"), "id_produit")
        if produit not in produits:
            raise ErreurValidation(
                f"Produit inexistant : {produit!r} (absent de produits.csv)."
            )
        if produit in vus:
            raise ErreurValidation(
                f"Produit en double dans le lot : {produit}."
            )
        vus.add(produit)
        nombre = _entier_positif(ligne.get("quantite"), "quantite_commandee")
        lignes_validees.append((produit, nombre))

    commandes = lire_table("commandes_achats")
    creees: list[dict] = []
    for produit, nombre in lignes_validees:
        commande = {
            "id_commande": generer_id("commandes_achats", commandes),
            "id_produit": produit,
            "id_fournisseur": fournisseur_id,
            "id_entrepot": entrepot,
            "date_commande": date,
            "quantite_commandee": str(nombre),
            "statut": "en_cours",
        }
        commandes.append(commande)
        creees.append(commande)
    ecrire_table("commandes_achats", commandes)
    return {
        "commandes": creees,
        "mode_fret": fournisseur.get("mode_fret"),
        "delai_promis_jours": fournisseur.get("delai_contractuel_jours"),
    }


def enregistrer_reception(
    id_commande, date_reception=None, quantite_recue=None
) -> dict:
    """Enregistre l'arrivee d'un lot : ligne de reception + statut du lot.

    Statut : recue si la quantite cumulee couvre la commande, partielle
    sinon. Refus si le lot est deja entierement receptionne.
    """
    commande_id = _texte_obligatoire(id_commande, "id_commande")
    commandes = lire_table("commandes_achats")
    commande = next(
        (c for c in commandes if c["id_commande"] == commande_id), None
    )
    if commande is None:
        raise ErreurValidation(f"Lot d'import inexistant : {commande_id!r}.")
    if commande["statut"] == "recue":
        raise ErreurValidation(
            f"Lot {commande_id} deja receptionne en totalite."
        )

    quantite_commandee = int(commande["quantite_commandee"])
    receptions = lire_table("receptions")
    deja_recue = sum(
        int(r["quantite_recue"])
        for r in receptions
        if r["id_commande"] == commande_id
    )
    if quantite_recue in (None, ""):
        nombre = max(quantite_commandee - deja_recue, 0)
        if nombre <= 0:
            raise ErreurValidation(
                f"Lot {commande_id} : plus rien a receptionner."
            )
    else:
        nombre = _entier_positif(quantite_recue, "quantite_recue")
    date = _date_iso(date_reception, "date_reception", defaut_aujourdhui=True)

    ligne = {
        "id_reception": generer_id("receptions", receptions),
        "id_commande": commande_id,
        "date_reception": date,
        "quantite_recue": str(nombre),
    }
    receptions.append(ligne)
    ecrire_table("receptions", receptions)

    total_recu = deja_recue + nombre
    commande["statut"] = "recue" if total_recu >= quantite_commandee else "partielle"
    ecrire_table("commandes_achats", commandes)
    return {"reception": ligne, "statut_lot": commande["statut"]}


def lister_lots() -> list[dict]:
    """Lots d'import avec leurs receptions, les plus recents d'abord."""
    receptions_par_commande: dict[str, list[dict]] = {}
    for reception in lire_table("receptions"):
        receptions_par_commande.setdefault(
            reception["id_commande"], []
        ).append(reception)
    fournisseurs = {
        f["id_fournisseur"]: f for f in lire_table("fournisseurs")
    }
    produits = {p["id_produit"]: p for p in lire_table("produits")}
    entrepots = {e["id_entrepot"]: e for e in lire_table("entrepots")}

    lots: list[dict] = []
    for commande in lire_table("commandes_achats"):
        lignes_reception = sorted(
            receptions_par_commande.get(commande["id_commande"], []),
            key=lambda r: r["date_reception"],
        )
        fournisseur = fournisseurs.get(commande["id_fournisseur"], {})
        produit = produits.get(commande["id_produit"], {})
        entrepot = entrepots.get(commande["id_entrepot"], {})
        lots.append(
            {
                **commande,
                "nom_produit": produit.get("nom_produit", commande["id_produit"]),
                "nom_fournisseur": fournisseur.get(
                    "nom_fournisseur", commande["id_fournisseur"]
                ),
                "mode_fret": fournisseur.get("mode_fret", ""),
                "delai_promis_jours": fournisseur.get(
                    "delai_contractuel_jours", ""
                ),
                "nom_entrepot": entrepot.get(
                    "nom_entrepot", commande["id_entrepot"]
                ),
                "quantite_recue_totale": sum(
                    int(r["quantite_recue"]) for r in lignes_reception
                ),
                "receptions": lignes_reception,
            }
        )
    return sorted(
        lots, key=lambda l: (l["date_commande"], l["id_commande"]), reverse=True
    )
