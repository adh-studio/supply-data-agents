"""Construit la base DuckDB de test a partir des CSV du dossier fixtures.

Chaque CSV reproduit EXACTEMENT le schema d'un mart de l'entrepot reel
supply-dbt-warehouse (memes noms de tables, memes colonnes, memes types) :

- supply_couverture_stock : id_produit, nom_produit, famille, id_entrepot,
  nom_entrepot, date_dernier_inventaire, quantite_disponible,
  quantite_vendue_90j, vente_moyenne_jour, couverture_jours,
  statut_couverture (rupture / sain / surstock / dormant) ;
- supply_taux_rupture : id_entrepot, nom_entrepot, famille,
  nb_observations, nb_ruptures, taux_rupture (fraction 0-1) ;
- supply_delais_fournisseurs : id_fournisseur, nom_fournisseur, pays,
  note_rse, delai_contractuel_jours, nb_commandes,
  nb_commandes_receptionnees, delai_moyen_reel_jours, ecart_moyen_jours,
  taux_service_otif (fraction 0-1) ;
- offre_marge_familles : famille, quantite_vendue, chiffre_affaires,
  marge_brute, taux_marge (fraction 0-1), stock_moyen_unites,
  rotation_periode ;
- offre_top_flop_produits : id_produit, nom_produit, famille,
  quantite_vendue, chiffre_affaires, marge_brute, rang_ca, segment.

Usage : python fixtures/construire_base.py
"""

from __future__ import annotations

import os

import duckdb

DOSSIER_FIXTURES = os.path.dirname(os.path.abspath(__file__))
CHEMIN_BASE_TEST = os.path.join(DOSSIER_FIXTURES, "entrepot_test.duckdb")

# Pour chaque mart : (nom du CSV, projection typee appliquee au CSV brut).
# Les types sont forces pour coller a ceux des marts reels.
DEFINITIONS = {
    "supply_couverture_stock": (
        "supply_couverture_stock.csv",
        """
        SELECT
            id_produit,
            nom_produit,
            famille,
            id_entrepot,
            nom_entrepot,
            CAST(date_dernier_inventaire AS DATE) AS date_dernier_inventaire,
            CAST(quantite_disponible AS INTEGER) AS quantite_disponible,
            CAST(quantite_vendue_90j AS BIGINT) AS quantite_vendue_90j,
            CAST(vente_moyenne_jour AS DOUBLE) AS vente_moyenne_jour,
            CAST(couverture_jours AS DOUBLE) AS couverture_jours,
            statut_couverture
        FROM lecture
        """,
    ),
    "supply_taux_rupture": (
        "supply_taux_rupture.csv",
        """
        SELECT
            id_entrepot,
            nom_entrepot,
            famille,
            CAST(nb_observations AS BIGINT) AS nb_observations,
            CAST(nb_ruptures AS BIGINT) AS nb_ruptures,
            CAST(taux_rupture AS DOUBLE) AS taux_rupture
        FROM lecture
        """,
    ),
    "supply_delais_fournisseurs": (
        "supply_delais_fournisseurs.csv",
        """
        SELECT
            id_fournisseur,
            nom_fournisseur,
            pays,
            note_rse,
            CAST(delai_contractuel_jours AS INTEGER) AS delai_contractuel_jours,
            CAST(nb_commandes AS BIGINT) AS nb_commandes,
            CAST(nb_commandes_receptionnees AS BIGINT)
                AS nb_commandes_receptionnees,
            CAST(delai_moyen_reel_jours AS DOUBLE) AS delai_moyen_reel_jours,
            CAST(ecart_moyen_jours AS DOUBLE) AS ecart_moyen_jours,
            CAST(taux_service_otif AS DOUBLE) AS taux_service_otif
        FROM lecture
        """,
    ),
    "offre_marge_familles": (
        "offre_marge_familles.csv",
        """
        SELECT
            famille,
            CAST(quantite_vendue AS BIGINT) AS quantite_vendue,
            CAST(chiffre_affaires AS DOUBLE) AS chiffre_affaires,
            CAST(marge_brute AS DOUBLE) AS marge_brute,
            CAST(taux_marge AS DOUBLE) AS taux_marge,
            CAST(stock_moyen_unites AS DOUBLE) AS stock_moyen_unites,
            CAST(rotation_periode AS DOUBLE) AS rotation_periode
        FROM lecture
        """,
    ),
    "offre_top_flop_produits": (
        "offre_top_flop_produits.csv",
        """
        SELECT
            id_produit,
            nom_produit,
            famille,
            CAST(quantite_vendue AS BIGINT) AS quantite_vendue,
            CAST(chiffre_affaires AS DOUBLE) AS chiffre_affaires,
            CAST(marge_brute AS DOUBLE) AS marge_brute,
            CAST(rang_ca AS BIGINT) AS rang_ca,
            segment
        FROM lecture
        """,
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
