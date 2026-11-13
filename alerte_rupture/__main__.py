"""Point d'entree CLI : python -m alerte_rupture [--json] [--surstock]."""

from __future__ import annotations

import argparse

from alerte_rupture.moteur import executer


def principal(arguments: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(
        prog="alerte_rupture",
        description=(
            "Detecte les produits en rupture dans supply_couverture_stock "
            "et diffuse une alerte (outbox, webhook, CLI)."
        ),
    )
    analyseur.add_argument(
        "--json",
        action="store_true",
        help="sortie machine JSON (liste des ruptures + message), pour n8n",
    )
    analyseur.add_argument(
        "--surstock",
        action="store_true",
        help="inclure aussi les produits en surstock dans l'alerte",
    )
    options = analyseur.parse_args(arguments)

    executer(
        inclure_surstock=options.surstock,
        sortie_json=options.json,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(principal())
