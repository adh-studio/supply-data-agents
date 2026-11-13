"""Point d'entree CLI : python -m pipeline [--declencheur X] [--etat]."""

from __future__ import annotations

import argparse
import json

from pipeline.moteur import DECLENCHEURS_VALIDES, executer_build, lire_etat


def principal(arguments: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(
        prog="pipeline",
        description=(
            "Lance le build dbt de l'entrepot, met a jour etat_build.json "
            "et declenche le diagnostic du gardien en cas d'echec."
        ),
    )
    analyseur.add_argument(
        "--declencheur",
        choices=DECLENCHEURS_VALIDES,
        default="manuel",
        help="origine du build (defaut : manuel)",
    )
    analyseur.add_argument(
        "--etat",
        action="store_true",
        help="affiche l'etat du dernier build (JSON) sans rien lancer",
    )
    options = analyseur.parse_args(arguments)

    if options.etat:
        print(json.dumps(lire_etat(), ensure_ascii=False, indent=2))
        return 0

    code = executer_build(declencheur=options.declencheur)
    etat = lire_etat()
    print(
        f"Build {etat.get('statut')} : {etat.get('pass')} pass, "
        f"{etat.get('fail')} fail sur {etat.get('total')} "
        f"({etat.get('duree_s')} s, declencheur {etat.get('declencheur')})."
    )
    return code


if __name__ == "__main__":
    raise SystemExit(principal())
