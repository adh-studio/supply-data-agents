"""Point d'entree CLI du rapport hebdomadaire.

Usage :

    python -m rapport_hebdo
    python -m rapport_hebdo --sortie /chemin/du/dossier
"""

from __future__ import annotations

import argparse

from rapport_hebdo.moteur import generer_rapport


def principal() -> None:
    """Analyse les arguments et genere le rapport."""
    analyseur = argparse.ArgumentParser(
        prog="python -m rapport_hebdo",
        description=(
            "Genere le rapport hebdomadaire supply et offre : rapport.md "
            "et docs/rapport-hebdo.html."
        ),
    )
    analyseur.add_argument(
        "--sortie",
        default=None,
        help=(
            "Dossier de sortie (defaut : racine du projet). rapport.md y est "
            "ecrit, ainsi que docs/rapport-hebdo.html dans un sous-dossier docs."
        ),
    )
    arguments = analyseur.parse_args()

    resultat = generer_rapport(dossier_sortie=arguments.sortie)
    print(f"Rapport markdown : {resultat['markdown']}")
    print(f"Rapport HTML : {resultat['html']}")


if __name__ == "__main__":
    principal()
