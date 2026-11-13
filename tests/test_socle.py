"""Tests du socle : construction de la base fixture et gardes de lib.base."""

import os

import duckdb
import pytest

from lib import base as socle
from lib.base import MARTS_AUTORISES, RequeteInterditeErreur, connexion, requete


class TestConstructionBase:
    def test_la_base_fixture_existe(self, base_de_test):
        assert os.path.exists(base_de_test)

    def test_les_cinq_marts_sont_presents(self, base_de_test):
        con = duckdb.connect(base_de_test, read_only=True)
        try:
            tables = {ligne[0] for ligne in con.execute("SHOW TABLES").fetchall()}
        finally:
            con.close()
        assert MARTS_AUTORISES <= tables

    def test_chaque_mart_contient_des_lignes(self, base_de_test):
        con = duckdb.connect(base_de_test, read_only=True)
        try:
            for table in MARTS_AUTORISES:
                nb = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                assert nb >= 15, f"{table} ne contient que {nb} lignes"
        finally:
            con.close()

    def test_au_moins_trois_produits_en_rupture(self):
        lignes = requete(
            "SELECT id_produit FROM supply_couverture_stock "
            "WHERE statut_couverture = 'rupture'"
        )
        assert len(lignes) >= 3

    def test_les_dates_sont_recentes(self):
        lignes = requete(
            "SELECT MAX(date_jour) AS derniere FROM supply_taux_rupture"
        )
        import datetime

        derniere = lignes[0]["derniere"]
        assert (datetime.date.today() - derniere).days <= 1


class TestConnexion:
    def test_connexion_ouvre_la_fixture_en_mode_test(self):
        con = connexion()
        try:
            nb = con.execute(
                "SELECT COUNT(*) FROM supply_couverture_stock"
            ).fetchone()[0]
            assert nb > 0
        finally:
            con.close()

    def test_connexion_est_en_lecture_seule(self):
        con = connexion()
        try:
            with pytest.raises(duckdb.Error):
                con.execute(
                    "UPDATE supply_couverture_stock SET couverture_jours = 0"
                )
        finally:
            con.close()

    def test_chemin_base_hors_mode_test(self, monkeypatch):
        monkeypatch.delenv("TEST_MODE", raising=False)
        monkeypatch.setenv("DUCKDB_PATH", "/tmp/entrepot_bidon.duckdb")
        assert socle.chemin_base() == "/tmp/entrepot_bidon.duckdb"


class TestGardesRequete:
    def test_select_simple_autorise(self):
        lignes = requete("SELECT * FROM supply_couverture_stock LIMIT 3")
        assert len(lignes) == 3
        assert "statut_couverture" in lignes[0]

    def test_cte_autorisee(self):
        lignes = requete(
            "WITH ruptures AS ("
            "  SELECT famille FROM supply_couverture_stock "
            "  WHERE statut_couverture = 'rupture'"
            ") SELECT COUNT(*) AS nb FROM ruptures"
        )
        assert lignes[0]["nb"] >= 3

    def test_update_rejete(self):
        with pytest.raises(RequeteInterditeErreur):
            requete("UPDATE supply_couverture_stock SET couverture_jours = 0")

    def test_delete_rejete(self):
        with pytest.raises(RequeteInterditeErreur):
            requete("DELETE FROM supply_couverture_stock")

    def test_drop_rejete(self):
        with pytest.raises(RequeteInterditeErreur):
            requete("DROP TABLE supply_couverture_stock")

    def test_create_rejete_meme_apres_select(self):
        with pytest.raises(RequeteInterditeErreur):
            requete(
                "SELECT * FROM supply_couverture_stock; "
                "DROP TABLE supply_couverture_stock"
            )

    def test_table_hors_liste_rejetee(self):
        with pytest.raises(RequeteInterditeErreur):
            requete("SELECT * FROM clients_secrets")

    def test_jointure_avec_table_interdite_rejetee(self):
        with pytest.raises(RequeteInterditeErreur):
            requete(
                "SELECT * FROM supply_couverture_stock s "
                "JOIN table_privee p ON s.id_produit = p.id_produit"
            )

    def test_jointure_entre_marts_autorisee(self):
        lignes = requete(
            "SELECT s.nom_produit, t.taux_marge "
            "FROM supply_couverture_stock s "
            "JOIN offre_top_flop_produits t ON s.id_produit = t.id_produit "
            "LIMIT 5"
        )
        assert len(lignes) > 0

    def test_requete_vide_rejetee(self):
        with pytest.raises(RequeteInterditeErreur):
            requete("   ")

    def test_mot_cle_interdit_dans_litteral_tolere(self):
        lignes = requete(
            "SELECT COUNT(*) AS nb FROM supply_couverture_stock "
            "WHERE nom_produit = 'update drop create'"
        )
        assert lignes[0]["nb"] == 0
