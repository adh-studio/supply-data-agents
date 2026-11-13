"""Tests de la plateforme de saisie SupplyOps.

Tout se joue sur un repo dbt factice temporaire : seeds CSV en CRLF
copies a l'identique de la structure reelle, contrat schema.yml minimal.
Aucun dbt build reel : la publication est bouchonnee via
serveur.FONCTION_BUILD. La preservation du CRLF est verifiee octet pour
octet (lecture binaire).
"""

from __future__ import annotations

import datetime
import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from supplyops import magasin
from supplyops import serveur as ops_serveur

AUJOURDHUI = datetime.date.today().isoformat()

SEEDS_CRLF = {
    "produits.csv": (
        "id_produit,nom_produit,famille,id_fournisseur,prix_achat,prix_vente\r\n"
        "P0001,Console de test,Consoles et gaming,F001,2000000,2900000\r\n"
        "P0002,Casque de test,Audio,F002,90000,160000\r\n"
    ),
    "fournisseurs.csv": (
        "id_fournisseur,nom_fournisseur,pays,mode_fret,delai_contractuel_jours,note_rse\r\n"
        "F001,Fournisseur Air,Chine,AIR,12,B\r\n"
        "F002,Fournisseur Mer,Chine,SEA,45,C\r\n"
    ),
    "entrepots.csv": (
        "id_entrepot,nom_entrepot,ville,capacite_m3,type_entrepot\r\n"
        "E01,Depot central,Antananarivo,1200,central\r\n"
        "E02,Boutique,Antananarivo,150,boutique\r\n"
    ),
    "commandes_achats.csv": (
        "id_commande,id_produit,id_fournisseur,id_entrepot,date_commande,"
        "quantite_commandee,statut\r\n"
        "CA0001,P0001,F001,E01,2026-10-01,20,en_cours\r\n"
        "CA0002,P0002,F002,E01,2026-09-01,50,recue\r\n"
    ),
    "receptions.csv": (
        "id_reception,id_commande,date_reception,quantite_recue\r\n"
        "R0001,CA0002,2026-10-15,50\r\n"
    ),
    "stocks.csv": (
        "id_produit,id_entrepot,date_inventaire,quantite_disponible\r\n"
        "P0001,E01,2026-11-01,5\r\n"
        "P0002,E01,2026-11-01,12\r\n"
        "P0002,E02,2026-11-01,3\r\n"
    ),
    "ventes.csv": (
        "id_vente,date_vente,id_produit,id_entrepot,canal,quantite,prix_unitaire\r\n"
        "V00001,2026-11-02,P0001,E01,magasin,1,2900000\r\n"
    ),
}

SCHEMA_YML = """\
version: 2

models:
  - name: stg_produits
    columns:
      - name: famille
        tests:
          - not_null
          - accepted_values:
              values:
                - "Consoles et gaming"
                - "Audio"
                - "Eclairage LED"
"""


@pytest.fixture()
def repo_factice(tmp_path, monkeypatch):
    """Repo dbt factice : seeds CRLF + contrat schema.yml minimal."""
    repo = tmp_path / "repo-dbt"
    seeds = repo / "seeds"
    seeds.mkdir(parents=True)
    for nom, contenu in SEEDS_CRLF.items():
        (seeds / nom).write_bytes(contenu.encode("utf-8"))
    staging = repo / "models" / "staging"
    staging.mkdir(parents=True)
    (staging / "schema.yml").write_text(SCHEMA_YML, encoding="utf-8")
    monkeypatch.setenv("DBT_DIR", str(repo))
    monkeypatch.delenv("OPS_SEEDS_DIR", raising=False)
    monkeypatch.delenv("OPS_SCHEMA_PATH", raising=False)
    return repo


def _octets(repo, nom):
    with open(repo / "seeds" / nom, "rb") as fichier:
        return fichier.read()


# ---------------------------------------------------------------------------
# Lecture / ecriture et preservation du CRLF
# ---------------------------------------------------------------------------

class TestLectureEcriture:
    def test_lecture_retourne_les_lignes(self, repo_factice):
        produits = magasin.lire_table("produits")
        assert [p["id_produit"] for p in produits] == ["P0001", "P0002"]
        assert produits[0]["famille"] == "Consoles et gaming"

    def test_reecriture_identique_octet_pour_octet(self, repo_factice):
        avant = _octets(repo_factice, "produits.csv")
        magasin.ecrire_table("produits", magasin.lire_table("produits"))
        assert _octets(repo_factice, "produits.csv") == avant

    def test_table_inconnue_refusee(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="inconnue"):
            magasin.lire_table("inexistante")

    def test_colonnes_inattendues_refusees(self, repo_factice):
        lignes = magasin.lire_table("produits")
        lignes[0]["colonne_pirate"] = "x"
        with pytest.raises(magasin.ErreurValidation, match="Colonnes invalides"):
            magasin.ecrire_table("produits", lignes)

    def test_surcharge_ops_seeds_dir(self, repo_factice, tmp_path, monkeypatch):
        autre = tmp_path / "seeds-isoles"
        autre.mkdir()
        (autre / "produits.csv").write_bytes(
            SEEDS_CRLF["produits.csv"].encode("utf-8")
        )
        monkeypatch.setenv("OPS_SEEDS_DIR", str(autre))
        assert magasin.dossier_seeds() == str(autre)
        assert len(magasin.lire_table("produits")) == 2


# ---------------------------------------------------------------------------
# Contrat : familles lues depuis schema.yml
# ---------------------------------------------------------------------------

class TestContratFamilles:
    def test_familles_lues_dynamiquement(self, repo_factice):
        assert magasin.familles_autorisees() == [
            "Consoles et gaming", "Audio", "Eclairage LED",
        ]

    def test_le_contrat_fait_foi_apres_modification(self, repo_factice):
        chemin = repo_factice / "models" / "staging" / "schema.yml"
        chemin.write_text(
            SCHEMA_YML.replace('- "Eclairage LED"', '- "Drones"'),
            encoding="utf-8",
        )
        assert "Drones" in magasin.familles_autorisees()
        assert "Eclairage LED" not in magasin.familles_autorisees()

    def test_contrat_introuvable(self, repo_factice):
        os.remove(repo_factice / "models" / "staging" / "schema.yml")
        with pytest.raises(magasin.ErreurValidation, match="introuvable"):
            magasin.familles_autorisees()


# ---------------------------------------------------------------------------
# Generation d'identifiants
# ---------------------------------------------------------------------------

class TestGenerationIds:
    def test_largeur_et_increment_d_apres_l_existant(self, repo_factice):
        assert magasin.generer_id("produits") == "P0003"
        assert magasin.generer_id("ventes") == "V00002"
        assert magasin.generer_id("commandes_achats") == "CA0003"
        assert magasin.generer_id("receptions") == "R0002"

    def test_table_sans_generation(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation):
            magasin.generer_id("entrepots")


# ---------------------------------------------------------------------------
# CRUD produit
# ---------------------------------------------------------------------------

class TestCreerProduit:
    def test_creation_et_crlf_preserve(self, repo_factice):
        avant = _octets(repo_factice, "produits.csv")
        resultat = magasin.creer_produit(
            "Lampe LED de test", "Eclairage LED", "F002", 40000, 95000
        )
        assert resultat["produit"]["id_produit"] == "P0003"
        assert resultat["avertissements"] == []
        apres = _octets(repo_factice, "produits.csv")
        # Le fichier d'origine est intact, la nouvelle ligne est en CRLF.
        assert apres.startswith(avant)
        assert apres == avant + b"P0003,Lampe LED de test,Eclairage LED,F002,40000,95000\r\n"
        assert b"\r\n" in apres
        assert apres.replace(b"\r\n", b"") .find(b"\n") == -1

    def test_famille_inconnue_refusee_avec_le_contrat(self, repo_factice):
        avant = _octets(repo_factice, "produits.csv")
        with pytest.raises(magasin.ErreurValidation) as erreur:
            magasin.creer_produit("Produit X", "Papeterie", "F001", 10, 20)
        assert "Famille inconnue" in str(erreur.value)
        assert "Eclairage LED" in str(erreur.value)
        assert _octets(repo_factice, "produits.csv") == avant

    def test_fournisseur_inexistant_refuse(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="Fournisseur inexistant"):
            magasin.creer_produit("Produit X", "Audio", "F999", 10, 20)

    def test_prix_negatif_ou_nul_refuse(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="prix_achat"):
            magasin.creer_produit("Produit X", "Audio", "F001", -5, 20)
        with pytest.raises(magasin.ErreurValidation, match="prix_vente"):
            magasin.creer_produit("Produit X", "Audio", "F001", 10, 0)

    def test_prix_non_entier_refuse(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="entier"):
            magasin.creer_produit("Produit X", "Audio", "F001", "abc", 20)

    def test_marge_negative_acceptee_avec_avertissement(self, repo_factice):
        resultat = magasin.creer_produit(
            "Produit a perte", "Audio", "F001", 100, 80
        )
        assert resultat["avertissements"]
        assert "marge" in resultat["avertissements"][0]

    def test_nom_vide_refuse(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="nom_produit"):
            magasin.creer_produit("  ", "Audio", "F001", 10, 20)


class TestModifierProduit:
    def test_modification_des_prix(self, repo_factice):
        resultat = magasin.modifier_produit(
            "P0002", {"prix_achat": 95000, "prix_vente": 170000}
        )
        assert resultat["produit"]["prix_vente"] == "170000"
        produits = magasin.lire_table("produits")
        assert produits[1]["prix_achat"] == "95000"
        # Rien d'autre n'a bouge.
        assert produits[0]["prix_achat"] == "2000000"

    def test_champ_inconnu_refuse(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="non modifiables"):
            magasin.modifier_produit("P0001", {"id_produit": "P9999"})

    def test_produit_inexistant_refuse(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="inexistant"):
            magasin.modifier_produit("P9999", {"prix_vente": 100})

    def test_famille_modifiee_validee_par_le_contrat(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="Famille inconnue"):
            magasin.modifier_produit("P0001", {"famille": "Jouets"})


class TestRetirerProduit:
    def test_refus_si_le_produit_a_des_ventes(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="vente"):
            magasin.retirer_produit("P0001")
        assert any(
            p["id_produit"] == "P0001" for p in magasin.lire_table("produits")
        )

    def test_retrait_sans_ventes_et_inventaire_purge(self, repo_factice):
        # P0002 n'a pas de vente mais figure dans CA0002 : on neutralise
        # d'abord le lot pour tester le retrait pur.
        commandes = [
            c for c in magasin.lire_table("commandes_achats")
            if c["id_produit"] != "P0002"
        ]
        magasin.ecrire_table("commandes_achats", commandes)
        receptions = [
            r for r in magasin.lire_table("receptions")
            if r["id_commande"] != "CA0002"
        ]
        magasin.ecrire_table("receptions", receptions)

        resultat = magasin.retirer_produit("P0002")
        assert resultat["lignes_inventaire_retirees"] == 2
        assert all(
            p["id_produit"] != "P0002" for p in magasin.lire_table("produits")
        )
        assert all(
            s["id_produit"] != "P0002" for s in magasin.lire_table("stocks")
        )

    def test_refus_si_le_produit_a_des_lots(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="lot"):
            magasin.retirer_produit("P0002")

    def test_produit_inexistant_refuse(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="inexistant"):
            magasin.retirer_produit("P9999")


# ---------------------------------------------------------------------------
# Inventaire
# ---------------------------------------------------------------------------

class TestInventaire:
    def test_saisie_date_du_jour_et_crlf(self, repo_factice):
        avant = _octets(repo_factice, "stocks.csv")
        resultat = magasin.saisir_inventaire("P0001", "E02", 7)
        assert resultat["remplacee"] is False
        assert resultat["ligne"]["date_inventaire"] == AUJOURDHUI
        apres = _octets(repo_factice, "stocks.csv")
        assert apres == avant + f"P0001,E02,{AUJOURDHUI},7\r\n".encode()

    def test_resaisie_du_meme_jour_remplace(self, repo_factice):
        magasin.saisir_inventaire("P0001", "E02", 7)
        resultat = magasin.saisir_inventaire("P0001", "E02", 9)
        assert resultat["remplacee"] is True
        lignes = [
            s for s in magasin.lire_table("stocks")
            if s["id_produit"] == "P0001" and s["id_entrepot"] == "E02"
        ]
        assert len(lignes) == 1
        assert lignes[0]["quantite_disponible"] == "9"

    def test_quantite_zero_acceptee_rupture(self, repo_factice):
        resultat = magasin.saisir_inventaire("P0002", "E02", 0)
        assert resultat["ligne"]["quantite_disponible"] == "0"

    def test_quantite_negative_refusee(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="quantite_disponible"):
            magasin.saisir_inventaire("P0001", "E01", -3)

    def test_produit_inexistant_refuse(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="Produit inexistant"):
            magasin.saisir_inventaire("P9999", "E01", 5)

    def test_entrepot_inexistant_refuse(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="Entrepot inexistant"):
            magasin.saisir_inventaire("P0001", "E99", 5)

    def test_date_invalide_refusee(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="ISO"):
            magasin.saisir_inventaire("P0001", "E01", 5, "12/11/2026")

    def test_inventaire_courant_garde_la_derniere_date(self, repo_factice):
        magasin.saisir_inventaire("P0001", "E01", 2)
        courant = {
            (l["id_produit"], l["id_entrepot"]): l
            for l in magasin.inventaire_courant()
        }
        assert courant[("P0001", "E01")]["quantite_disponible"] == "2"
        assert courant[("P0001", "E01")]["date_inventaire"] == AUJOURDHUI


# ---------------------------------------------------------------------------
# Ventes
# ---------------------------------------------------------------------------

class TestVentes:
    def test_saisie_avec_prix_catalogue_par_defaut(self, repo_factice):
        resultat = magasin.saisir_vente("P0002", "E02", "magasin", 2)
        vente = resultat["vente"]
        assert vente["id_vente"] == "V00002"
        assert vente["prix_unitaire"] == "160000"
        assert vente["date_vente"] == AUJOURDHUI

    def test_prix_explicite_respecte_et_crlf(self, repo_factice):
        avant = _octets(repo_factice, "ventes.csv")
        resultat = magasin.saisir_vente(
            "P0001", "E01", "web", 1, 2750000, "2026-11-10"
        )
        assert resultat["vente"]["prix_unitaire"] == "2750000"
        apres = _octets(repo_factice, "ventes.csv")
        assert apres == avant + b"V00002,2026-11-10,P0001,E01,web,1,2750000\r\n"

    def test_canal_inconnu_refuse(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="Canal inconnu"):
            magasin.saisir_vente("P0001", "E01", "telephone", 1)

    def test_quantite_nulle_refusee(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="quantite"):
            magasin.saisir_vente("P0001", "E01", "web", 0)

    def test_produit_inexistant_refuse(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="Produit inexistant"):
            magasin.saisir_vente("P9999", "E01", "web", 1)


# ---------------------------------------------------------------------------
# Lots d'import et receptions
# ---------------------------------------------------------------------------

class TestLots:
    def test_creation_d_un_lot_multi_produits(self, repo_factice):
        resultat = magasin.creer_lot(
            "F001", "E01",
            [
                {"id_produit": "P0001", "quantite": 10},
                {"id_produit": "P0002", "quantite": 25},
            ],
            "2026-11-12",
        )
        assert [c["id_commande"] for c in resultat["commandes"]] == [
            "CA0003", "CA0004",
        ]
        assert all(c["statut"] == "en_cours" for c in resultat["commandes"])
        assert resultat["mode_fret"] == "AIR"
        assert resultat["delai_promis_jours"] == "12"

    def test_lot_vide_refuse(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="au moins une ligne"):
            magasin.creer_lot("F001", "E01", [])

    def test_produit_inconnu_dans_le_lot_refuse(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="Produit inexistant"):
            magasin.creer_lot(
                "F001", "E01", [{"id_produit": "P9999", "quantite": 5}]
            )

    def test_reception_totale_passe_le_lot_en_recue(self, repo_factice):
        resultat = magasin.enregistrer_reception("CA0001")
        assert resultat["reception"]["quantite_recue"] == "20"
        assert resultat["statut_lot"] == "recue"
        commande = next(
            c for c in magasin.lire_table("commandes_achats")
            if c["id_commande"] == "CA0001"
        )
        assert commande["statut"] == "recue"

    def test_reception_partielle_puis_solde(self, repo_factice):
        premier = magasin.enregistrer_reception("CA0001", quantite_recue=8)
        assert premier["statut_lot"] == "partielle"
        second = magasin.enregistrer_reception("CA0001")
        assert second["reception"]["quantite_recue"] == "12"
        assert second["statut_lot"] == "recue"

    def test_lot_deja_recu_refuse(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="deja receptionne"):
            magasin.enregistrer_reception("CA0002")

    def test_lot_inexistant_refuse(self, repo_factice):
        with pytest.raises(magasin.ErreurValidation, match="inexistant"):
            magasin.enregistrer_reception("CA9999")

    def test_lister_lots_enrichit_les_noms(self, repo_factice):
        lots = magasin.lister_lots()
        lot = next(l for l in lots if l["id_commande"] == "CA0002")
        assert lot["nom_fournisseur"] == "Fournisseur Mer"
        assert lot["mode_fret"] == "SEA"
        assert lot["quantite_recue_totale"] == 50
        assert lot["receptions"][0]["id_reception"] == "R0001"


# ---------------------------------------------------------------------------
# Serveur HTTP : API, compteur de modifications, publication bouchonnee
# ---------------------------------------------------------------------------

@pytest.fixture()
def serveur_ops(repo_factice):
    """Serveur SupplyOps sur un port libre, etat de publication remis a zero."""
    with ops_serveur._verrou_publication:
        ops_serveur._publication["en_cours"] = False
        ops_serveur._publication["dernier_code"] = None
    instance = ops_serveur.creer_serveur(0)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{instance.server_address[1]}"
    finally:
        instance.shutdown()
        instance.server_close()


def appel(url, chemin, methode="GET", corps=None):
    """Appelle l'API locale et retourne (code HTTP, objet JSON)."""
    requete = urllib.request.Request(
        url + chemin,
        method=methode,
        data=json.dumps(corps).encode("utf-8") if corps is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(requete, timeout=10) as reponse:
            return reponse.status, json.loads(reponse.read().decode("utf-8"))
    except urllib.error.HTTPError as erreur:
        return erreur.code, json.loads(erreur.read().decode("utf-8"))


class TestServeurApi:
    def test_etat_initial(self, serveur_ops):
        code, corps = appel(serveur_ops, "/api/etat")
        assert code == 200
        assert corps["application"] == "SupplyOps"
        assert corps["modifications_en_attente"] == 0
        assert corps["publication_en_cours"] is False

    def test_referentiels_exposent_le_contrat(self, serveur_ops):
        code, corps = appel(serveur_ops, "/api/referentiels")
        assert code == 200
        assert corps["familles"] == [
            "Consoles et gaming", "Audio", "Eclairage LED",
        ]
        assert [f["id_fournisseur"] for f in corps["fournisseurs"]] == [
            "F001", "F002",
        ]
        assert len(corps["entrepots"]) == 2

    def test_creation_produit_et_compteur(self, serveur_ops, repo_factice):
        code, corps = appel(
            serveur_ops, "/api/produits", "POST",
            {
                "nom_produit": "Ampoule connectee",
                "famille": "Eclairage LED",
                "id_fournisseur": "F002",
                "prix_achat": 15000,
                "prix_vente": 39000,
            },
        )
        assert code == 201
        assert corps["produit"]["id_produit"] == "P0003"
        code, etat = appel(serveur_ops, "/api/etat")
        assert etat["modifications_en_attente"] == 1
        assert etat["journal"][0]["action"] == "produit_cree"
        # Le CSV reel du repo factice contient bien la ligne, en CRLF.
        octets = _octets(repo_factice, "produits.csv")
        assert b"P0003,Ampoule connectee,Eclairage LED,F002,15000,39000\r\n" in octets

    def test_validation_metier_en_erreur_400_motivee(self, serveur_ops):
        code, corps = appel(
            serveur_ops, "/api/produits", "POST",
            {
                "nom_produit": "Produit interdit",
                "famille": "Famille pirate",
                "id_fournisseur": "F001",
                "prix_achat": 10,
                "prix_vente": 20,
            },
        )
        assert code == 400
        assert corps["ok"] is False
        assert "Famille inconnue" in corps["erreur"]
        assert "schema.yml" in corps["erreur"]

    def test_modification_et_retrait_produit(self, serveur_ops):
        code, corps = appel(
            serveur_ops, "/api/produits/P0002", "POST",
            {"prix_vente": 180000},
        )
        assert code == 200
        assert corps["produit"]["prix_vente"] == "180000"
        # P0001 a des ventes : retrait refuse.
        code, corps = appel(serveur_ops, "/api/produits/P0001", "DELETE")
        assert code == 400
        assert "vente" in corps["erreur"]

    def test_inventaire_et_vente_via_api(self, serveur_ops):
        code, corps = appel(
            serveur_ops, "/api/inventaire", "POST",
            {"id_produit": "P0001", "id_entrepot": "E02", "quantite": 4},
        )
        assert code == 201
        assert corps["ligne"]["date_inventaire"] == AUJOURDHUI

        code, corps = appel(
            serveur_ops, "/api/ventes", "POST",
            {
                "id_produit": "P0002", "id_entrepot": "E02",
                "canal": "web", "quantite": 1,
            },
        )
        assert code == 201
        assert corps["vente"]["prix_unitaire"] == "160000"

        code, etat = appel(serveur_ops, "/api/etat")
        assert etat["modifications_en_attente"] == 2

    def test_lot_et_reception_via_api(self, serveur_ops):
        code, corps = appel(
            serveur_ops, "/api/lots", "POST",
            {
                "id_fournisseur": "F002",
                "id_entrepot": "E01",
                "lignes": [{"id_produit": "P0002", "quantite": 30}],
            },
        )
        assert code == 201
        id_commande = corps["commandes"][0]["id_commande"]
        assert corps["mode_fret"] == "SEA"

        code, corps = appel(
            serveur_ops, f"/api/lots/{id_commande}/reception", "POST", {}
        )
        assert code == 201
        assert corps["statut_lot"] == "recue"

    def test_route_inconnue(self, serveur_ops):
        code, corps = appel(serveur_ops, "/api/inexistante")
        assert code == 404


class TestPublication:
    def test_publier_appelle_le_pipeline_et_remet_le_compteur(
        self, serveur_ops, monkeypatch
    ):
        appel(
            serveur_ops, "/api/inventaire", "POST",
            {"id_produit": "P0001", "id_entrepot": "E01", "quantite": 6},
        )
        appels: list[str] = []
        termine = threading.Event()

        def faux_build(declencheur):
            appels.append(declencheur)
            termine.set()
            return 0

        monkeypatch.setattr(ops_serveur, "FONCTION_BUILD", faux_build)
        code, corps = appel(serveur_ops, "/api/publier", "POST", {})
        assert code == 202
        assert termine.wait(timeout=5)
        # Attendre la fin du thread (compteur remis a zero).
        for _ in range(100):
            _, etat = appel(serveur_ops, "/api/etat")
            if not etat["publication_en_cours"]:
                break
        assert appels == ["supplyops"]
        assert etat["modifications_en_attente"] == 0
        assert etat["dernier_code_publication"] == 0

    def test_409_si_publication_deja_en_cours(self, serveur_ops, monkeypatch):
        bloque = threading.Event()
        lance = threading.Event()

        def build_bloquant(declencheur):
            lance.set()
            bloque.wait(timeout=10)
            return 0

        monkeypatch.setattr(ops_serveur, "FONCTION_BUILD", build_bloquant)
        code, _ = appel(serveur_ops, "/api/publier", "POST", {})
        assert code == 202
        assert lance.wait(timeout=5)

        code, corps = appel(serveur_ops, "/api/publier", "POST", {})
        assert code == 409
        assert "deja en cours" in corps["erreur"]
        bloque.set()
        for _ in range(100):
            _, etat = appel(serveur_ops, "/api/etat")
            if not etat["publication_en_cours"]:
                break
        assert etat["publication_en_cours"] is False

    def test_echec_de_build_conserve_le_compteur(self, serveur_ops, monkeypatch):
        appel(
            serveur_ops, "/api/inventaire", "POST",
            {"id_produit": "P0002", "id_entrepot": "E01", "quantite": 11},
        )
        termine = threading.Event()

        def build_casse(declencheur):
            termine.set()
            return 1

        monkeypatch.setattr(ops_serveur, "FONCTION_BUILD", build_casse)
        code, _ = appel(serveur_ops, "/api/publier", "POST", {})
        assert code == 202
        assert termine.wait(timeout=5)
        for _ in range(100):
            _, etat = appel(serveur_ops, "/api/etat")
            if not etat["publication_en_cours"]:
                break
        assert etat["modifications_en_attente"] == 1
        assert etat["dernier_code_publication"] == 1
