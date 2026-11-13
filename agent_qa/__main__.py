"""CLI du self-service question/reponse.

Usage :
    python -m agent_qa "quelle famille a la pire rotation ?"
    python -m agent_qa --utilisateur melody "quels produits sont en rupture ?"
"""

from __future__ import annotations

import argparse
import sys

from agent_qa.moteur import QuestionNonCompriseErreur, repondre
from lib.base import RequeteInterditeErreur


def principal(arguments: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(
        prog="agent_qa",
        description="Pose une question metier en francais a l'entrepot analytique.",
    )
    analyseur.add_argument("question", help="Question metier en francais")
    analyseur.add_argument(
        "--utilisateur",
        default="cli",
        help="Identifiant de la personne qui pose la question (audit)",
    )
    options = analyseur.parse_args(arguments)

    try:
        resultat = repondre(options.question, utilisateur=options.utilisateur)
    except QuestionNonCompriseErreur as erreur:
        print(f"Question non comprise : {erreur}", file=sys.stderr)
        return 2
    except RequeteInterditeErreur as erreur:
        print(f"Requete refusee par la garde de securite : {erreur}", file=sys.stderr)
        return 3

    print(resultat["reponse"])
    return 0


if __name__ == "__main__":
    sys.exit(principal())
