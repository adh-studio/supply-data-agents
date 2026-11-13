"""Construit la base DuckDB de test a partir des CSV du dossier fixtures.

Chaque CSV correspond a un mart de l'entrepot reel (memes noms de tables,
memes colonnes). Les colonnes se terminant par _offset sont des decalages
en jours par rapport a aujourd'hui : elles sont converties en vraies dates
au moment de la construction, pour que la base de test contienne toujours
des dates recentes.

Usage : python fixtures/construire_base.py
"""

from __future__ import annotations

import os

import duckdb

DOSSIER_FIXTURES = os.path.dirname(os.path.abspath(__file__))
CHEMIN_BASE_TEST = os.path.join(DOSSIER_FIXTURES, "entrepot_test.duckdb")

# Pour chaque mart : (nom du CSV, SQL de projection appliquee au CSV brut).
# Les offsets de jours deviennent des dates relatives a CURRENT_DATE.
DEFINITIONS = {
    "supply_couverture_stock": (
        "supply_couverture_stock.csv",
        "SELECT * FROM lecture",
    ),
    "supply_taux_rupture": (
        "supply_taux_rupture.csv",
        """
        SELECT
            famille,
            id_entrepot,
            CURRENT_DATE - CAST(date_jour_offset AS INTEGER) AS date_jour,
            nb_references_actives,
            nb_references_rupture,
            taux_rupture_pct
        FROM lecture
        """,
    ),
    "supply_delais_fournisseurs": (
        "supply_delais_fournisseurs.csv",
        """
        SELECT
            id_fournisseur,
            nom_fournisseur,
            famille_principale,
            nb_commandes_periode,
            nb_commandes_a_temps,
            nb_commandes_completes,
            delai_moyen_jours,
            delai_contractuel_jours,
            taux_otif,
            CURRENT_DATE - CAST(date_derniere_reception_offset AS INTEGER)
                AS date_derniere_reception
        FROM lecture
        """,
    ),
    "offre_marge_familles": (
        "offre_marge_familles.csv",
        "SELECT * FROM lecture",
    ),
    "offre_top_flop_produits": (
        "offre_top_flop_produits.csv",
        "SELECT * FROM lecture",
    ),
}


def construire(chemin_base: str = CHEMIN_BASE_TEST) -> str:
    """Construit (ou reconstruit) la base de test et retourne son chemin."""
    if os.path.exists(chemin_base):
        os.remove(chemin_base)

    connexion = duckdb.connect(chemin_base)
    try:
        for nom_table, (nom_csv, projection) in DEFINITIONS.items():
            chemin_csv = os.path.join(DOSSIER_FIXTURES, nom_csv)
            if not os.path.exists(chemin_csv):
                raise FileNotFoundError(f"CSV de fixture manquant : {chemin_csv}")
            connexion.execute(
                f"""
                CREATE OR REPLACE TABLE {nom_table} AS
                WITH lecture AS (
                    SELECT * FROM read_csv_auto('{chemin_csv}', header = true)
                )
                {projection}
                """
            )
        connexion.execute("CHECKPOINT")
    finally:
        connexion.close()
    return chemin_base


if __name__ == "__main__":
    chemin = construire()
    print(f"Base de test construite : {chemin}")
