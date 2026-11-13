"""Configuration pytest du repo : base fixture + mode test force.

Toute la suite de tests travaille sur fixtures/entrepot_test.duckdb,
jamais sur l'entrepot reel ni sur le reseau.
"""

import os
import sys

import pytest

RACINE = os.path.dirname(os.path.abspath(__file__))
if RACINE not in sys.path:
    sys.path.insert(0, RACINE)

from fixtures.construire_base import CHEMIN_BASE_TEST, construire  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def base_de_test():
    """Construit la base fixture et pose TEST_MODE=1 pour toute la session."""
    os.environ["TEST_MODE"] = "1"
    chemin = construire()
    yield chemin


@pytest.fixture()
def chemin_base_test():
    """Chemin de la base fixture, pour les tests qui en ont besoin."""
    return CHEMIN_BASE_TEST
