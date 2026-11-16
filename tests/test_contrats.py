"""Tests des contrats de donnees vivants (module contrats + integration).

Tout se joue sur un repo dbt factice temporaire : seeds CSV en CRLF,
contrat dbt schema.yml minimal et contrats ODCS v3 ecrits par la
fixture avec les memes regles que les contrats publies dans
supply-dbt-warehouse/contracts/. Aucun dbt build reel : le pipeline est
bouchonne par un script factice qui laisse une trace quand il tourne,
ce qui permet de verifier que le build n'est PAS lance sur violation.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import threading
import urllib.error
import urllib.request

import pytest
import yaml

from contrats import moteur
import contrats.__main__ as cli
from pipeline import moteur as moteur_pipeline

AUJOURDHUI = datetime.date.today()


def _date(jours: int) -> str:
    """Date ISO a `jours` d'ecart de la date du jour (negatif = passe)."""
    return (AUJOURDHUI + datetime.timedelta(days=jours)).isoformat()


def seeds_crlf() -> dict[str, str]:
    """Seeds CSV conformes a tous les contrats, en CRLF."""
    return {
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
            f"CA0001,P0001,F001,E01,{_date(-30)},20,recue\r\n"
            f"CA0002,P0002,F002,E01,{_date(-20)},50,en_cours\r\n"
        ),
        "receptions.csv": (
            "id_reception,id_commande,date_reception,quantite_recue\r\n"
            f"R0001,CA0001,{_date(-15)},20\r\n"
        ),
        "stocks.csv": (
            "id_produit,id_entrepot,date_inventaire,quantite_disponible\r\n"
            f"P0001,E01,{_date(-1)},5\r\n"
            f"P0002,E01,{_date(-1)},0\r\n"
            f"P0002,E02,{_date(-1)},3\r\n"
        ),
        "ventes.csv": (
            "id_vente,date_vente,id_produit,id_entrepot,canal,quantite,prix_unitaire\r\n"
            f"V00001,{_date(-2)},P0001,E01,magasin,1,2900000\r\n"
            f"V00002,{_date(-1)},P0002,E02,web,2,150000\r\n"
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


def _qualite(nom: str, implementation: dict) -> dict:
    """Entree quality ODCS de type custom, moteur supplysense."""
    return {
        "name": nom,
        "description": f"Regle de test {nom}.",
        "type": "custom",
        "engine": "supplysense",
        "implementation": implementation,
    }


def _propriete(nom: str, type_logique: str = "string", **options) -> dict:
    propriete = {"name": nom, "logicalType": type_logique, "required": True}
    propriete.update(options)
    return propriete


def contrats_odcs() -> dict[str, dict]:
    """Les 4 contrats de test : memes regles que ceux du repo dbt reel."""
    base = {
        "apiVersion": "v3.0.2",
        "kind": "DataContract",
        "status": "active",
        "version": "1.0.0",
        "team": [
            {"username": "heni", "name": "Heni", "role": "owner",
             "description": "Direction"}
        ],
    }
    produits = {
        **base,
        "id": "referentiel-produits",
        "name": "Referentiel produits",
        "schema": [
            {
                "name": "produits",
                "physicalName": "seeds/produits.csv",
                "properties": [
                    _propriete("id_produit", unique=True, primaryKey=True,
                               primaryKeyPosition=1),
                    _propriete("nom_produit"),
                    _propriete("famille", quality=[_qualite(
                        "famille_du_contrat",
                        {"controle": "valeurs_schema_dbt", "colonne": "famille",
                         "modele": "stg_produits", "colonne_schema": "famille"},
                    )]),
                    _propriete("id_fournisseur", quality=[_qualite(
                        "fournisseur_existant",
                        {"controle": "reference", "colonne": "id_fournisseur",
                         "table": "fournisseurs",
                         "colonne_cible": "id_fournisseur"},
                    )]),
                    _propriete("prix_achat", "integer", quality=[_qualite(
                        "prix_achat_positif",
                        {"controle": "seuil", "colonne": "prix_achat",
                         "operateur": ">", "valeur": 0},
                    )]),
                    _propriete("prix_vente", "integer", quality=[_qualite(
                        "prix_vente_positif",
                        {"controle": "seuil", "colonne": "prix_vente",
                         "operateur": ">", "valeur": 0},
                    )]),
                ],
                "quality": [_qualite(
                    "marge_positive",
                    {"controle": "comparaison_colonnes",
                     "colonne_gauche": "prix_vente", "operateur": ">",
                     "colonne_droite": "prix_achat"},
                )],
            }
        ],
    }
    stocks = {
        **base,
        "id": "stocks-quotidiens",
        "name": "Stocks quotidiens",
        "schema": [
            {
                "name": "stocks",
                "physicalName": "seeds/stocks.csv",
                "properties": [
                    _propriete("id_produit", primaryKey=True,
                               primaryKeyPosition=1, quality=[_qualite(
                                   "produit_existant",
                                   {"controle": "reference",
                                    "colonne": "id_produit",
                                    "table": "produits",
                                    "colonne_cible": "id_produit"},
                               )]),
                    _propriete("id_entrepot", primaryKey=True,
                               primaryKeyPosition=2, quality=[_qualite(
                                   "entrepot_existant",
                                   {"controle": "reference",
                                    "colonne": "id_entrepot",
                                    "table": "entrepots",
                                    "colonne_cible": "id_entrepot"},
                               )]),
                    _propriete("date_inventaire", "date", primaryKey=True,
                               primaryKeyPosition=3),
                    _propriete("quantite_disponible", "integer",
                               quality=[_qualite(
                                   "quantite_positive_ou_nulle",
                                   {"controle": "seuil",
                                    "colonne": "quantite_disponible",
                                    "operateur": ">=", "valeur": 0},
                               )]),
                ],
            }
        ],
        "slaProperties": [
            {"property": "freshness", "value": 7, "unit": "d",
             "element": "stocks.date_inventaire"}
        ],
    }
    ventes = {
        **base,
        "id": "ventes",
        "name": "Ventes",
        "schema": [
            {
                "name": "ventes",
                "physicalName": "seeds/ventes.csv",
                "properties": [
                    _propriete("id_vente", unique=True, primaryKey=True,
                               primaryKeyPosition=1),
                    _propriete("date_vente", "date", quality=[_qualite(
                        "date_non_future",
                        {"controle": "date_non_future",
                         "colonne": "date_vente"},
                    )]),
                    _propriete("id_produit", quality=[_qualite(
                        "produit_existant",
                        {"controle": "reference", "colonne": "id_produit",
                         "table": "produits", "colonne_cible": "id_produit"},
                    )]),
                    _propriete("id_entrepot", quality=[_qualite(
                        "entrepot_existant",
                        {"controle": "reference", "colonne": "id_entrepot",
                         "table": "entrepots",
                         "colonne_cible": "id_entrepot"},
                    )]),
                    _propriete("canal", quality=[_qualite(
                        "canal_autorise",
                        {"controle": "valeurs_autorisees", "colonne": "canal",
                         "valeurs": ["magasin", "web"]},
                    )]),
                    _propriete("quantite", "integer", quality=[_qualite(
                        "quantite_strictement_positive",
                        {"controle": "seuil", "colonne": "quantite",
                         "operateur": ">", "valeur": 0},
                    )]),
                    _propriete("prix_unitaire", "integer", quality=[_qualite(
                        "prix_unitaire_positif",
                        {"controle": "seuil", "colonne": "prix_unitaire",
                         "operateur": ">", "valeur": 0},
                    )]),
                ],
            }
        ],
    }
    lots = {
        **base,
        "id": "lots-import",
        "name": "Lots d'import",
        "schema": [
            {
                "name": "commandes_achats",
                "physicalName": "seeds/commandes_achats.csv",
                "properties": [
                    _propriete("id_commande", unique=True, primaryKey=True,
                               primaryKeyPosition=1),
                    _propriete("id_produit"),
                    _propriete("id_fournisseur"),
                    _propriete("id_entrepot"),
                    _propriete("date_commande", "date"),
                    _propriete("quantite_commandee", "integer"),
                    _propriete("statut", quality=[_qualite(
                        "statut_autorise",
                        {"controle": "valeurs_autorisees", "colonne": "statut",
                         "valeurs": ["en_cours", "partielle", "recue"]},
                    )]),
                ],
                "quality": [_qualite(
                    "fret_coherent_avec_delai",
                    {"controle": "coherence_fret_delai",
                     "cle": "id_fournisseur",
                     "table_fournisseurs": "fournisseurs",
                     "colonne_mode": "mode_fret",
                     "colonne_delai": "delai_contractuel_jours",
                     "bornes": {"AIR": [5, 25], "SEA": [30, 70]}},
                )],
            },
            {
                "name": "receptions",
                "physicalName": "seeds/receptions.csv",
                "properties": [
                    _propriete("id_reception", unique=True, primaryKey=True,
                               primaryKeyPosition=1),
                    _propriete("id_commande", quality=[_qualite(
                        "commande_existante",
                        {"controle": "reference", "colonne": "id_commande",
                         "table": "commandes_achats",
                         "colonne_cible": "id_commande"},
                    )]),
                    _propriete("date_reception", "date", quality=[_qualite(
                        "reception_apres_commande",
                        {"controle": "comparaison_jointure",
                         "colonne": "date_reception", "operateur": ">=",
                         "cle": "id_commande", "table": "commandes_achats",
                         "cle_cible": "id_commande",
                         "colonne_cible": "date_commande"},
                    )]),
                    _propriete("quantite_recue", "integer"),
                ],
            },
        ],
    }
    return {
        "lots_import.odcs.yaml": lots,
        "referentiel_produits.odcs.yaml": produits,
        "stocks_quotidiens.odcs.yaml": stocks,
        "ventes.odcs.yaml": ventes,
    }


def construire_repo(tmp_path) -> str:
    """Repo dbt factice complet : seeds CRLF, schema.yml, contrats ODCS."""
    repo = tmp_path / "repo-dbt"
    (repo / "seeds").mkdir(parents=True)
    for nom, contenu in seeds_crlf().items():
        (repo / "seeds" / nom).write_bytes(contenu.encode("utf-8"))
    (repo / "models" / "staging").mkdir(parents=True)
    (repo / "models" / "staging" / "schema.yml").write_text(
        SCHEMA_YML, encoding="utf-8"
    )
    (repo / "contracts").mkdir()
    for nom, contrat in contrats_odcs().items():
        (repo / "contracts" / nom).write_text(
            yaml.safe_dump(contrat, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    return str(repo)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    chemin = construire_repo(tmp_path)
    monkeypatch.setenv("DBT_DIR", chemin)
    monkeypatch.setenv("PIPELINE_OUTBOX", str(tmp_path / "outbox"))
    return chemin


def _saboter(repo: str, nom: str, remplacement) -> None:
    """Reecrit un seed en appliquant `remplacement` au contenu (CRLF garde)."""
    chemin = os.path.join(repo, "seeds", nom)
    with open(chemin, "rb") as fichier:
        contenu = fichier.read().decode("utf-8")
    with open(chemin, "wb") as fichier:
        fichier.write(remplacement(contenu).encode("utf-8"))


def _regle(verdict: dict, id_contrat: str, nom_regle: str) -> dict:
    contrat = next(c for c in verdict["contrats"] if c["id"] == id_contrat)
    return next(r for r in contrat["regles"] if r["nom"] == nom_regle)


# ---------------------------------------------------------------------------
# Chargement des contrats
# ---------------------------------------------------------------------------

class TestChargement:
    def test_dossier_absent_liste_vide(self, tmp_path):
        assert moteur.charger_contrats(str(tmp_path)) == []

    def test_quatre_contrats_charges_et_tries(self, repo):
        contrats = moteur.charger_contrats(repo)
        assert [c["_fichier"] for c in contrats] == sorted(
            contrats_odcs().keys()
        )

    def test_contrat_invalide_refuse(self, repo):
        with open(
            os.path.join(repo, "contracts", "casse.odcs.yaml"),
            "w",
            encoding="utf-8",
        ) as fichier:
            fichier.write("kind: AutreChose\n")
        with pytest.raises(moteur.ErreurContrat):
            moteur.charger_contrats(repo)

    def test_owner_du_contrat(self, repo):
        contrat = moteur.charger_contrats(repo)[0]
        assert moteur.owner_du_contrat(contrat) == "Heni - Direction"


# ---------------------------------------------------------------------------
# Seeds conformes : tout est vert
# ---------------------------------------------------------------------------

class TestConformite:
    def test_tout_conforme(self, repo):
        verdict = moteur.valider_tout(repo)
        assert verdict["conforme"] is True
        assert verdict["nb_contrats"] == 4
        assert verdict["nb_violations"] == 0
        assert all(c["statut"] == "conforme" for c in verdict["contrats"])

    def test_structure_du_verdict(self, repo):
        verdict = moteur.valider_tout(repo)
        contrat = next(
            c for c in verdict["contrats"] if c["id"] == "referentiel-produits"
        )
        assert contrat["nom"] == "Referentiel produits"
        assert contrat["version"] == "1.0.0"
        assert contrat["owner"] == "Heni - Direction"
        assert contrat["seeds"] == ["seeds/produits.csv"]
        noms = [r["nom"] for r in contrat["regles"]]
        assert "schema_produits" in noms
        assert "unicite_produits" in noms
        assert "marge_positive" in noms
        assert all(r["statut"] == "conforme" for r in contrat["regles"])

    def test_fraicheur_conforme_avec_age_reel(self, repo):
        verdict = moteur.valider_tout(repo)
        stocks = next(
            c for c in verdict["contrats"] if c["id"] == "stocks-quotidiens"
        )
        assert stocks["fraicheur"]["statut"] == "conforme"
        assert stocks["fraicheur"]["age_jours"] == 1
        assert stocks["fraicheur"]["limite_jours"] == 7
        assert stocks["fraicheur"]["derniere_date"] == _date(-1)


# ---------------------------------------------------------------------------
# Chaque type de violation est detecte
# ---------------------------------------------------------------------------

class TestViolations:
    def test_famille_inconnue(self, repo):
        _saboter(
            repo, "produits.csv",
            lambda c: c.replace("Consoles et gaming", "Famille pirate"),
        )
        regle = _regle(
            moteur.valider_tout(repo), "referentiel-produits",
            "famille_du_contrat",
        )
        assert regle["statut"] == "viole"
        assert "Famille pirate" in regle["detail"]

    def test_prix_de_vente_negatif(self, repo):
        _saboter(
            repo, "ventes.csv",
            lambda c: c + f"V00666,{_date(-1)},P0001,E01,magasin,1,-5000\r\n",
        )
        verdict = moteur.valider_tout(repo)
        regle = _regle(verdict, "ventes", "prix_unitaire_positif")
        assert regle["statut"] == "viole"
        assert "V00666" in regle["detail"]
        assert verdict["conforme"] is False

    def test_marge_negative_au_catalogue(self, repo):
        _saboter(
            repo, "produits.csv",
            lambda c: c + "P0003,Produit a perte,Audio,F001,500000,400000\r\n",
        )
        regle = _regle(
            moteur.valider_tout(repo), "referentiel-produits", "marge_positive"
        )
        assert regle["statut"] == "viole"
        assert "P0003" in regle["detail"]

    def test_doublon_id_vente(self, repo):
        _saboter(
            repo, "ventes.csv",
            lambda c: c + f"V00001,{_date(-1)},P0001,E01,web,1,100000\r\n",
        )
        regle = _regle(moteur.valider_tout(repo), "ventes", "unicite_ventes")
        assert regle["statut"] == "viole"
        assert "V00001" in regle["detail"]

    def test_doublon_cle_composite_stocks(self, repo):
        _saboter(
            repo, "stocks.csv",
            lambda c: c + f"P0001,E01,{_date(-1)},9\r\n",
        )
        regle = _regle(
            moteur.valider_tout(repo), "stocks-quotidiens", "unicite_stocks"
        )
        assert regle["statut"] == "viole"
        assert "P0001/E01" in regle["detail"]

    def test_id_produit_orphelin(self, repo):
        _saboter(
            repo, "ventes.csv",
            lambda c: c + f"V00777,{_date(-1)},P9999,E01,web,1,100000\r\n",
        )
        regle = _regle(moteur.valider_tout(repo), "ventes", "produit_existant")
        assert regle["statut"] == "viole"
        assert "P9999" in regle["detail"]

    def test_fraicheur_depassee(self, repo):
        _saboter(
            repo, "stocks.csv",
            lambda c: c.replace(_date(-1), _date(-10)),
        )
        verdict = moteur.valider_tout(repo)
        stocks = next(
            c for c in verdict["contrats"] if c["id"] == "stocks-quotidiens"
        )
        assert stocks["fraicheur"]["statut"] == "viole"
        assert stocks["fraicheur"]["age_jours"] == 10
        assert stocks["statut"] == "viole"
        assert verdict["conforme"] is False

    def test_colonne_manquante(self, repo):
        _saboter(
            repo, "ventes.csv",
            lambda c: "\r\n".join(
                ",".join(ligne.split(",")[:4] + ligne.split(",")[5:])
                for ligne in c.split("\r\n")
            ),
        )
        verdict = moteur.valider_tout(repo)
        regle = _regle(verdict, "ventes", "schema_ventes")
        assert regle["statut"] == "viole"
        assert "canal" in regle["detail"]
        # La regle qui vise la colonne absente est elle aussi violee.
        assert _regle(verdict, "ventes", "canal_autorise")["statut"] == "viole"

    def test_canal_inconnu(self, repo):
        _saboter(
            repo, "ventes.csv",
            lambda c: c + f"V00888,{_date(-1)},P0001,E01,marche_noir,1,5000\r\n",
        )
        regle = _regle(moteur.valider_tout(repo), "ventes", "canal_autorise")
        assert regle["statut"] == "viole"
        assert "marche_noir" in regle["detail"]

    def test_date_de_vente_future(self, repo):
        _saboter(
            repo, "ventes.csv",
            lambda c: c + f"V00999,{_date(3)},P0001,E01,web,1,100000\r\n",
        )
        regle = _regle(moteur.valider_tout(repo), "ventes", "date_non_future")
        assert regle["statut"] == "viole"
        assert "V00999" in regle["detail"]

    def test_reception_avant_commande(self, repo):
        _saboter(
            repo, "receptions.csv",
            lambda c: c + f"R0002,CA0001,{_date(-45)},5\r\n",
        )
        regle = _regle(
            moteur.valider_tout(repo), "lots-import", "reception_apres_commande"
        )
        assert regle["statut"] == "viole"
        assert "R0002" in regle["detail"]

    def test_fret_incoherent_avec_le_delai(self, repo):
        _saboter(
            repo, "fournisseurs.csv",
            lambda c: c.replace("F001,Fournisseur Air,Chine,AIR,12,B",
                                "F001,Fournisseur Air,Chine,AIR,40,B"),
        )
        regle = _regle(
            moteur.valider_tout(repo), "lots-import", "fret_coherent_avec_delai"
        )
        assert regle["statut"] == "viole"
        assert "F001" in regle["detail"]

    def test_phrases_des_regles_violees(self, repo):
        _saboter(
            repo, "ventes.csv",
            lambda c: c + f"V00666,{_date(-1)},P0001,E01,magasin,1,-5000\r\n",
        )
        phrases = moteur.regles_violees(moteur.valider_tout(repo))
        assert len(phrases) == 1
        assert "Ventes" in phrases[0]
        assert "prix_unitaire_positif" in phrases[0]
        assert "V00666" in phrases[0]


# ---------------------------------------------------------------------------
# CLI python -m contrats
# ---------------------------------------------------------------------------

class TestCli:
    def test_conforme_code_zero(self, repo, capsys):
        assert cli.principal() == 0
        sortie = capsys.readouterr().out
        assert "4 contrat(s)" in sortie
        assert "tous les contrats sont respectes" in sortie

    def test_violation_code_un(self, repo, capsys):
        _saboter(
            repo, "ventes.csv",
            lambda c: c + f"V00666,{_date(-1)},P0001,E01,magasin,1,-5000\r\n",
        )
        assert cli.principal() == 1
        sortie = capsys.readouterr().out
        assert "[VIOLE] Ventes" in sortie
        assert "publication a refuser" in sortie


# ---------------------------------------------------------------------------
# Integration pipeline : le build est refuse sur violation
# ---------------------------------------------------------------------------

SCRIPT_TEMOIN = (
    "import json, os, shutil, sys\n"
    "open('build_lance.temoin', 'w').write('oui')\n"
    "os.makedirs('target', exist_ok=True)\n"
    "shutil.copy(sys.argv[1], os.path.join('target', 'run_results.json'))\n"
    "sys.exit(0)\n"
)
RUN_VERT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "gardien_qualite", "fixtures", "run_results_vert.json",
)


class TestPipelineRefuse:
    @pytest.fixture()
    def script_build(self, tmp_path, monkeypatch):
        script = tmp_path / "faux_build.py"
        script.write_text(SCRIPT_TEMOIN, encoding="utf-8")
        monkeypatch.setattr(
            moteur_pipeline, "PIPELINE_COMMANDE",
            [sys.executable, str(script), RUN_VERT],
        )
        return script

    def test_build_refuse_sur_violation(self, repo, script_build, tmp_path):
        _saboter(
            repo, "ventes.csv",
            lambda c: c + f"V00666,{_date(-1)},P0001,E01,magasin,1,-5000\r\n",
        )
        assert moteur_pipeline.executer_build("supplyops") == 1

        # Le dbt build n'a jamais ete lance.
        assert not os.path.exists(os.path.join(repo, "build_lance.temoin"))

        etat = moteur_pipeline.lire_etat()
        assert etat["statut"] == "echec_contrats"
        assert etat["contrats"]["nb_violations"] == 1
        assert any(
            "prix_unitaire_positif" in phrase
            for phrase in etat["contrats"]["regles_violees"]
        )
        assert etat["historique"][0]["statut"] == "echec_contrats"
        assert etat["historique"][0]["modifications"] == []

        # Diagnostic depose dans l'outbox, verrou libere.
        diagnostics = [
            nom for nom in os.listdir(tmp_path / "outbox")
            if nom.startswith("diagnostic-contrats-") and nom.endswith(".md")
        ]
        assert len(diagnostics) == 1
        with open(
            tmp_path / "outbox" / diagnostics[0], encoding="utf-8"
        ) as fichier:
            markdown = fichier.read()
        assert "Publication refusee" in markdown
        assert "prix_unitaire_positif" in markdown
        assert not os.path.exists(os.path.join(repo, ".pipeline.lock"))

    def test_build_passe_quand_tout_est_conforme(self, repo, script_build):
        assert moteur_pipeline.executer_build("supplyops") == 0
        assert os.path.exists(os.path.join(repo, "build_lance.temoin"))
        assert moteur_pipeline.lire_etat()["statut"] == "succes"

    def test_contrat_corrompu_refuse_le_build(self, repo, script_build):
        with open(
            os.path.join(repo, "contracts", "casse.odcs.yaml"),
            "w",
            encoding="utf-8",
        ) as fichier:
            fichier.write("kind: AutreChose\n")
        assert moteur_pipeline.executer_build("supplyops") == 1
        etat = moteur_pipeline.lire_etat()
        assert etat["statut"] == "echec_contrats"
        assert any(
            "Contrats invalides" in phrase
            for phrase in etat["contrats"]["regles_violees"]
        )
        assert not os.path.exists(os.path.join(repo, "build_lance.temoin"))


# ---------------------------------------------------------------------------
# API SupplySense : GET /api/contrats et POST /api/contrats/verifier
# ---------------------------------------------------------------------------

def appel(url: str, chemin: str, methode: str = "GET", corps=None):
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


class TestApiCockpit:
    @pytest.fixture()
    def cockpit(self, repo, tmp_path):
        from cockpit import serveur as cockpit_serveur

        cockpit_serveur._cache_contrats["verdict"] = None
        serveur = cockpit_serveur.creer_serveur(0)
        thread = threading.Thread(target=serveur.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{serveur.server_address[1]}"
        finally:
            serveur.shutdown()
            serveur.server_close()
            cockpit_serveur._cache_contrats["verdict"] = None

    def test_get_contrats(self, cockpit):
        code, corps = appel(cockpit, "/api/contrats")
        assert code == 200
        assert corps["ok"] is True
        assert corps["nb_contrats"] == 4
        assert corps["conforme"] is True
        assert corps["verifie_le"]
        assert {c["id"] for c in corps["contrats"]} == {
            "referentiel-produits", "stocks-quotidiens", "ventes",
            "lots-import",
        }

    def test_get_sert_le_cache_et_post_reverifie(self, cockpit, repo):
        code, avant = appel(cockpit, "/api/contrats")
        assert avant["conforme"] is True

        _saboter(
            repo, "ventes.csv",
            lambda c: c + f"V00666,{_date(-1)},P0001,E01,magasin,1,-5000\r\n",
        )
        # GET : toujours le dernier verdict memorise.
        code, cache = appel(cockpit, "/api/contrats")
        assert cache["verifie_le"] == avant["verifie_le"]
        assert cache["conforme"] is True

        # POST verifier : revalidation immediate, la violation apparait.
        code, apres = appel(cockpit, "/api/contrats/verifier", "POST", {})
        assert code == 200
        assert apres["conforme"] is False
        assert apres["nb_violations"] == 1
        regle = _regle(apres, "ventes", "prix_unitaire_positif")
        assert regle["statut"] == "viole"

        # Et le GET suivant sert ce nouveau verdict.
        code, final = appel(cockpit, "/api/contrats")
        assert final["conforme"] is False
