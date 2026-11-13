"""Point d'entree : python -m supplyops [--port 4310]."""

from __future__ import annotations

import argparse

from supplyops.serveur import PORT_DEFAUT, demarrer


def principal(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(
        description=(
            "Demarre la plateforme de saisie SupplyOps "
            "(ecriture controlee des seeds CSV)."
        )
    )
    analyseur.add_argument(
        "--port", type=int, default=PORT_DEFAUT,
        help=f"Port d'ecoute (defaut : {PORT_DEFAUT}).",
    )
    options = analyseur.parse_args(argv)
    demarrer(options.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(principal())
