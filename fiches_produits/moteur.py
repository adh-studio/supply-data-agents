"""Moteur de generation des fiches produits.

Lit les produits dans les marts offre_top_flop_produits et
supply_couverture_stock, genere une fiche par produit (titre, description de
80 a 120 mots, 3 points forts, mention de matiere ou dimension plausible tiree
du nom), applique les garde-fous, puis depose les fiches conformes en
a_valider/ avec un en-tete YAML (statut: en_attente).

Deux modes de generation :
- LLM (claude-sonnet-5 via la librairie anthropic) si ANTHROPIC_API_KEY est
  presente, avec le ton de marque de fiches_produits/ton_de_marque.md ;
- gabarit deterministe integre sinon (aucun appel reseau).

La publication reste un geste humain : approuver(id) bascule la fiche vers
validees/ et passe le statut a validee. Rien d'autre ne publie jamais.
"""

from __future__ import annotations

import datetime
import json
import os
import re

import yaml

from fiches_produits.garde_fous import (
    MOTS_DESCRIPTION_MIN,
    compter_mots,
    verifier_fiche,
)
from lib.base import requete

RACINE_PROJET = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOSSIER_MODULE = os.path.dirname(os.path.abspath(__file__))
CHEMIN_TON_DE_MARQUE = os.path.join(DOSSIER_MODULE, "ton_de_marque.md")

NOM_DOSSIER_A_VALIDER = "a_valider"
NOM_DOSSIER_VALIDEES = "validees"

MODELE_LLM = "claude-sonnet-5"

# Matieres plausibles reconnaissables dans le nom du produit.
_MATIERES = {
    "lin": "lin lave",
    "velours": "velours",
    "rotin": "rotin tresse",
    "manguier": "bois de manguier",
    "chene": "chene massif",
    "bambou": "bambou",
    "gres": "gres artisanal",
    "jute": "jute naturelle",
    "cannage": "cannage traditionnel",
    "ceramique": "ceramique",
    "maille": "maille douce",
    "berbere": "laine tissee",
}

# Dimensions ou formats reconnaissables dans le nom du produit.
_REGEX_DIMENSIONS = (
    re.compile(r"\b(\d{2,3}\s?x\s?\d{2,3})\b"),
    re.compile(r"\b(\d{2,3}\s?cm)\b"),
    re.compile(r"\b(\d\s?places?)\b"),
    re.compile(r"\b(\d\s?portes?)\b"),
)

# Phrases d'appoint deterministes si la description est trop courte.
_PHRASES_APPOINT = (
    "Chaque piece est controlee avant expedition pour arriver chez vous dans "
    "de bonnes conditions.",
    "Il s'installe en quelques minutes et se deplace facilement au gre de "
    "vos envies.",
    "Pensez a l'associer a des textiles naturels pour une ambiance douce et "
    "accueillante.",
)


# ---------------------------------------------------------------------------
# Lecture des produits dans l'entrepot
# ---------------------------------------------------------------------------

def lire_produits(limite: int = 3) -> list[dict]:
    """Lit les produits a documenter, classement top en premier."""
    if not isinstance(limite, int) or limite < 1:
        raise ValueError("La limite doit etre un entier positif.")
    lignes = requete(
        f"""
        SELECT
            t.id_produit,
            t.nom_produit,
            t.famille,
            t.segment,
            MAX(c.statut_couverture) AS statut_couverture
        FROM offre_top_flop_produits t
        LEFT JOIN supply_couverture_stock c
            ON t.id_produit = c.id_produit
        GROUP BY t.id_produit, t.nom_produit, t.famille, t.segment, t.rang_ca
        ORDER BY CASE WHEN t.segment = 'top' THEN 0 ELSE 1 END, t.rang_ca ASC
        LIMIT {limite}
        """
    )
    return lignes


# ---------------------------------------------------------------------------
# Extraction matiere / dimension plausibles depuis le nom
# ---------------------------------------------------------------------------

def extraire_matiere(nom_produit: str) -> str | None:
    """Retourne une matiere plausible tiree du nom du produit, ou None."""
    nom_minuscule = (nom_produit or "").lower()
    for indice, matiere in _MATIERES.items():
        if indice in nom_minuscule:
            return matiere
    return None


def extraire_dimension(nom_produit: str) -> str | None:
    """Retourne une dimension ou un format tire du nom du produit, ou None."""
    for regex in _REGEX_DIMENSIONS:
        correspondance = regex.search(nom_produit or "")
        if correspondance:
            return correspondance.group(1)
    return None


# ---------------------------------------------------------------------------
# Mode gabarit deterministe (aucun appel reseau)
# ---------------------------------------------------------------------------

def generer_fiche_gabarit(produit: dict) -> dict:
    """Genere une fiche deterministe conforme au ton de marque."""
    nom = produit["nom_produit"]
    famille = produit.get("famille") or "deco"
    matiere = extraire_matiere(nom)
    dimension = extraire_dimension(nom)

    if matiere:
        titre = f"{nom}, la chaleur du {matiere} chez vous"
    else:
        titre = f"{nom}, une piece a vivre au quotidien"
    if len(titre) > 90:
        titre = f"{nom}, l'esprit deco"

    phrases = [
        f"{nom} apporte a votre interieur une presence chaleureuse, sans "
        "jamais en faire trop.",
    ]
    if matiere:
        phrases.append(
            f"Sa matiere, le {matiere}, a ete retenue pour sa solidite et "
            "son toucher agreable au fil des saisons."
        )
    else:
        phrases.append(
            "Ses materiaux ont ete retenus pour leur solidite et leur "
            "toucher agreable au fil des saisons."
        )
    if dimension:
        phrases.append(
            f"Son format {dimension} lui permet de trouver sa place dans un "
            "grand sejour comme dans une piece plus intime."
        )
    else:
        phrases.append(
            "Ses proportions equilibrees lui permettent de trouver sa "
            "place dans un grand sejour comme dans une piece plus intime."
        )
    phrases.append(
        f"Dans la famille {famille}, ce modele se distingue par une ligne "
        "sobre qui s'accorde avec des tons neutres comme avec des couleurs "
        "plus franches."
    )
    phrases.append(
        "L'entretien reste simple : un chiffon doux et un geste regulier "
        "suffisent a le garder beau longtemps."
    )

    description = " ".join(phrases)
    for appoint in _PHRASES_APPOINT:
        if compter_mots(description) >= MOTS_DESCRIPTION_MIN:
            break
        description = f"{description} {appoint}"

    points_forts = [
        f"Matiere authentique : {matiere}" if matiere
        else "Materiaux choisis avec soin",
        f"Format pratique : {dimension}" if dimension
        else "Proportions pensees pour la vie quotidienne",
        "Entretien facile au quotidien",
    ]

    return {
        "titre": titre,
        "description": description,
        "points_forts": points_forts,
    }


# ---------------------------------------------------------------------------
# Mode LLM (claude-sonnet-5), uniquement si ANTHROPIC_API_KEY est presente
# ---------------------------------------------------------------------------

def _lire_ton_de_marque() -> str:
    """Lit le document de ton de marque du module."""
    with open(CHEMIN_TON_DE_MARQUE, encoding="utf-8") as fichier:
        return fichier.read()


def _appeler_llm(prompt: str) -> str:
    """Appelle claude-sonnet-5 et retourne le texte brut de la reponse.

    Isole pour etre facilement remplace par un bouchon dans les tests :
    aucun test ne doit toucher le reseau.
    """
    import anthropic

    client = anthropic.Anthropic()
    reponse = client.messages.create(
        model=MODELE_LLM,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return reponse.content[0].text


def generer_fiche_llm(produit: dict) -> dict:
    """Genere une fiche via le LLM, au format JSON strict."""
    matiere = extraire_matiere(produit["nom_produit"])
    dimension = extraire_dimension(produit["nom_produit"])
    prompt = (
        "Tu rediges une fiche produit e-commerce pour une enseigne de "
        "decoration et d'ameublement. Respecte strictement ce ton de "
        "marque :\n\n"
        f"{_lire_ton_de_marque()}\n\n"
        "Produit :\n"
        f"- nom : {produit['nom_produit']}\n"
        f"- famille : {produit.get('famille', '')}\n"
        f"- matiere reperee dans le nom : {matiere or 'aucune'}\n"
        f"- dimension reperee dans le nom : {dimension or 'aucune'}\n\n"
        "Reponds UNIQUEMENT avec un objet JSON de la forme :\n"
        '{"titre": "...", "description": "...", "points_forts": '
        '["...", "...", "..."]}\n'
        "La description fait entre 80 et 120 mots. Exactement 3 points "
        "forts. Aucun prix, aucun pourcentage, aucun superlatif vide."
    )
    brut = _appeler_llm(prompt).strip()
    # Tolere un eventuel bloc de code autour du JSON.
    brut = re.sub(r"^```(?:json)?\s*|\s*```$", "", brut)
    fiche = json.loads(brut)
    return {
        "titre": fiche.get("titre", ""),
        "description": fiche.get("description", ""),
        "points_forts": list(fiche.get("points_forts", [])),
    }


def generer_fiche(produit: dict) -> tuple[dict, str]:
    """Genere une fiche et retourne (fiche, mode utilise).

    Mode llm si la cle API est presente, repli gabarit deterministe sinon
    ou en cas d'echec de l'appel.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return generer_fiche_llm(produit), "llm"
        except Exception:
            return generer_fiche_gabarit(produit), "gabarit"
    return generer_fiche_gabarit(produit), "gabarit"


# ---------------------------------------------------------------------------
# Flux de validation humaine : a_valider/ puis validees/
# ---------------------------------------------------------------------------

def _dossier(racine: str, nom: str) -> str:
    chemin = os.path.join(racine, nom)
    os.makedirs(chemin, exist_ok=True)
    return chemin


def _ecrire_fiche(chemin: str, entete: dict, fiche: dict) -> None:
    """Ecrit une fiche en Markdown avec son en-tete YAML."""
    bloc_yaml = yaml.safe_dump(entete, allow_unicode=True, sort_keys=False)
    points = "\n".join(f"- {p}" for p in fiche["points_forts"])
    contenu = (
        f"---\n{bloc_yaml}---\n\n"
        f"# {fiche['titre']}\n\n"
        f"{fiche['description']}\n\n"
        f"## Points forts\n\n{points}\n"
    )
    with open(chemin, "w", encoding="utf-8") as fichier:
        fichier.write(contenu)


def lire_fiche(chemin: str) -> tuple[dict, str]:
    """Lit une fiche Markdown et retourne (en-tete YAML, corps)."""
    with open(chemin, encoding="utf-8") as fichier:
        contenu = fichier.read()
    correspondance = re.match(r"^---\n(.*?)\n---\n(.*)$", contenu, re.DOTALL)
    if not correspondance:
        raise ValueError(f"Fiche sans en-tete YAML valide : {chemin}")
    entete = yaml.safe_load(correspondance.group(1))
    return entete, correspondance.group(2)


def generer(limite: int = 3, racine: str = RACINE_PROJET) -> list[dict]:
    """Genere les fiches, applique les garde-fous, depose en a_valider/.

    Retourne un compte rendu par produit :
    - statut "en_attente" avec le chemin du fichier depose, ou
    - statut "rejetee" avec la liste des motifs (aucun fichier ecrit).
    """
    dossier_attente = _dossier(racine, NOM_DOSSIER_A_VALIDER)
    resultats = []
    for produit in lire_produits(limite):
        fiche, mode = generer_fiche(produit)
        motifs = verifier_fiche(fiche)
        if motifs:
            resultats.append(
                {
                    "id_produit": produit["id_produit"],
                    "statut": "rejetee",
                    "mode": mode,
                    "motifs": motifs,
                    "chemin": None,
                }
            )
            continue
        chemin = os.path.join(dossier_attente, f"{produit['id_produit']}.md")
        entete = {
            "id_produit": produit["id_produit"],
            "nom_produit": produit["nom_produit"],
            "statut": "en_attente",
            "mode": mode,
            "genere_le": datetime.date.today().isoformat(),
        }
        _ecrire_fiche(chemin, entete, fiche)
        resultats.append(
            {
                "id_produit": produit["id_produit"],
                "statut": "en_attente",
                "mode": mode,
                "motifs": [],
                "chemin": chemin,
            }
        )
    return resultats


def approuver(id_produit: str, racine: str = RACINE_PROJET) -> str:
    """Validation humaine : bascule une fiche de a_valider/ vers validees/.

    Change le statut en "validee", date l'approbation, deplace le fichier,
    et retourne le chemin de la fiche validee.
    """
    chemin_attente = os.path.join(
        racine, NOM_DOSSIER_A_VALIDER, f"{id_produit}.md"
    )
    if not os.path.exists(chemin_attente):
        raise FileNotFoundError(
            f"Aucune fiche en attente pour {id_produit} : {chemin_attente}"
        )
    entete, corps = lire_fiche(chemin_attente)
    entete["statut"] = "validee"
    entete["approuvee_le"] = datetime.date.today().isoformat()

    dossier_validees = _dossier(racine, NOM_DOSSIER_VALIDEES)
    chemin_validee = os.path.join(dossier_validees, f"{id_produit}.md")
    bloc_yaml = yaml.safe_dump(entete, allow_unicode=True, sort_keys=False)
    with open(chemin_validee, "w", encoding="utf-8") as fichier:
        fichier.write(f"---\n{bloc_yaml}---\n{corps}")
    os.remove(chemin_attente)
    return chemin_validee
