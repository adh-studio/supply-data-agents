"""Garde-fous appliques a chaque fiche produit apres generation.

Quel que soit le mode (LLM ou gabarit deterministe), une fiche doit passer
TOUTES ces verifications avant d'etre deposee en zone de validation humaine.
Une fiche non conforme est rejetee avec la liste des motifs.
"""

from __future__ import annotations

import re

# Bornes de longueur.
MOTS_DESCRIPTION_MIN = 80
MOTS_DESCRIPTION_MAX = 120
TITRE_LONGUEUR_MIN = 10
TITRE_LONGUEUR_MAX = 90
NB_POINTS_FORTS_ATTENDU = 3

# Superlatifs vides interdits par le ton de marque (compares en minuscules).
SUPERLATIFS_INTERDITS = (
    "le meilleur",
    "la meilleure",
    "les meilleurs",
    "les meilleures",
    "numero 1",
    "n°1",
    "incontournable",
    "revolutionnaire",
    "exceptionnel",
    "exceptionnelle",
    "imbattable",
    "inegale",
    "inegalable",
    "parfait",
    "parfaite",
    "magique",
    "incroyable",
    "ultime",
    "unique au monde",
    "sans equivalent",
    "extraordinaire",
)

# Affirmations chiffrees que la fiche n'a pas le droit d'inventer :
# pourcentages (remises, taux) et prix (euros), jamais fournis au generateur.
_REGEX_POURCENTAGE = re.compile(r"\d+(?:[.,]\d+)?\s*%")
_REGEX_PRIX = re.compile(r"\d+(?:[.,]\d+)?\s*(?:€|euros?\b)", re.IGNORECASE)


def compter_mots(texte: str) -> int:
    """Compte les mots d'un texte (sequences non blanches)."""
    return len(re.findall(r"\S+", texte or ""))


def verifier_fiche(fiche: dict) -> list[str]:
    """Verifie une fiche et retourne la liste des motifs de rejet.

    Une liste vide signifie que la fiche est conforme. La fiche attendue est
    un dictionnaire avec les cles : titre (str), description (str),
    points_forts (liste de str).
    """
    motifs: list[str] = []

    titre = (fiche.get("titre") or "").strip()
    description = (fiche.get("description") or "").strip()
    points_forts = fiche.get("points_forts") or []

    # 1. Titre present et de longueur bornee.
    if not titre:
        motifs.append("titre manquant")
    elif not (TITRE_LONGUEUR_MIN <= len(titre) <= TITRE_LONGUEUR_MAX):
        motifs.append(
            f"longueur du titre hors bornes ({len(titre)} caracteres, "
            f"attendu entre {TITRE_LONGUEUR_MIN} et {TITRE_LONGUEUR_MAX})"
        )

    # 2. Description entre 80 et 120 mots.
    nb_mots = compter_mots(description)
    if not (MOTS_DESCRIPTION_MIN <= nb_mots <= MOTS_DESCRIPTION_MAX):
        motifs.append(
            f"description hors bornes ({nb_mots} mots, attendu entre "
            f"{MOTS_DESCRIPTION_MIN} et {MOTS_DESCRIPTION_MAX})"
        )

    # 3. Exactement 3 points forts non vides.
    points_valides = [p for p in points_forts if isinstance(p, str) and p.strip()]
    if len(points_valides) != NB_POINTS_FORTS_ATTENDU:
        motifs.append(
            f"points forts invalides ({len(points_valides)} fournis, "
            f"attendu exactement {NB_POINTS_FORTS_ATTENDU})"
        )

    # 4 et 5 : sur l'ensemble du texte visible de la fiche.
    texte_complet = " ".join([titre, description] + [str(p) for p in points_forts])
    texte_minuscule = texte_complet.lower()

    # 4. Aucune affirmation chiffree inventee (pourcentage ou prix).
    if _REGEX_POURCENTAGE.search(texte_complet):
        motifs.append("affirmation chiffree inventee : pourcentage non fourni")
    if _REGEX_PRIX.search(texte_complet):
        motifs.append("affirmation chiffree inventee : prix non fourni")

    # 5. Aucun superlatif vide interdit.
    for superlatif in SUPERLATIFS_INTERDITS:
        if superlatif in texte_minuscule:
            motifs.append(f"superlatif interdit : {superlatif}")

    return motifs
