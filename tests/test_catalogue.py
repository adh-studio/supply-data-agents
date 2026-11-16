"""Tests du catalogue de data products (module catalogue + API cockpit).

Tout se joue sur un repo dbt factice temporaire : les seeds et contrats
ODCS de test_contrats, plus un dossier catalogue/ ecrit par la fixture
(JSON Schema identique a celui publie dans supply-dbt-warehouse et deux
data products, un par domaine). L'etat vivant est calcule depuis un
etat_build.json de fixture et la base DuckDB fixture (TEST_MODE=1) :
aucun dbt build reel, aucun reseau.
"""

from __future__ import annotations

import datetime
import json
import os
import threading
import urllib.error
import urllib.request

import pytest
import yaml

from catalogue import moteur
import catalogue.__main__ as cli
from test_contrats import construire_repo

MAINTENANT = datetime.datetime.now()


# ---------------------------------------------------------------------------
# Fixtures : schema, data products, etat_build.json
# ---------------------------------------------------------------------------

SCHEMA_DATA_PRODUCT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Data product du catalogue (copie de test)",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "id", "nom", "domaine", "description", "owner", "ports_de_sortie",
        "contrat_reference", "slo", "consommateurs", "version", "changelog",
    ],
    "properties": {
        "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]+$"},
        "nom": {"type": "string", "minLength": 3},
        "domaine": {"type": "string", "enum": ["supply", "offre"]},
        "description": {"type": "string", "minLength": 30},
        "owner": {
            "type": "object",
            "additionalProperties": False,
            "required": ["nom", "role"],
            "properties": {
                "nom": {"type": "string", "minLength": 2},
                "role": {"type": "string", "minLength": 2},
            },
        },
        "ports_de_sortie": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["table", "grain", "colonnes"],
                "properties": {
                    "table": {"type": "string", "pattern": "^[a-z][a-z0-9_]+$"},
                    "grain": {"type": "string", "minLength": 3},
                    "colonnes": {
                        "type": "array",
                        "minItems": 2,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["nom", "sens"],
                            "properties": {
                                "nom": {"type": "string", "minLength": 1},
                                "sens": {"type": "string", "minLength": 10},
                            },
                        },
                    },
                },
            },
        },
        "contrat_reference": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_]+\\.odcs\\.yaml$",
        },
        "slo": {
            "type": "object",
            "additionalProperties": False,
            "required": ["fraicheur_heures", "completude_pct", "disponibilite"],
            "properties": {
                "fraicheur_heures": {"type": "number", "exclusiveMinimum": 0},
                "completude_pct": {
                    "type": "number", "minimum": 0, "maximum": 100,
                },
                "disponibilite": {"type": "string", "minLength": 5},
            },
        },
        "consommateurs": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "id"],
                "properties": {
                    "type": {
                        "type": "string", "enum": ["vue", "module", "export"],
                    },
                    "id": {"type": "string", "minLength": 2},
                    "usage": {"type": "string"},
                },
            },
        },
        "question_exemple": {"type": "string", "minLength": 10},
        "version": {"type": "string", "pattern": "^\\d+\\.\\d+\\.\\d+$"},
        "changelog": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["version", "date", "description", "auteur"],
                "properties": {
                    "version": {
                        "type": "string", "pattern": "^\\d+\\.\\d+\\.\\d+$",
                    },
                    "date": {
                        "type": "string",
                        "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
                    },
                    "description": {"type": "string", "minLength": 5},
                    "auteur": {"type": "string", "minLength": 2},
                },
            },
        },
    },
}


def data_product_valide(**surcharges) -> dict:
    """Data product conforme au schema, surchargable champ par champ."""
    declaration = {
        "id": "couverture_stock",
        "nom": "Couverture de stock",
        "domaine": "supply",
        "description": (
            "Combien de jours de vente chaque produit importe peut encore "
            "tenir dans chaque entrepot d'Antananarivo."
        ),
        "owner": {"nom": "Heni", "role": "Direction, Product Owner Data"},
        "ports_de_sortie": [
            {
                "table": "supply_couverture_stock",
                "grain": "une ligne par produit x entrepot",
                "colonnes": [
                    {"nom": "id_produit", "sens": "Produit concerne par la ligne."},
                    {"nom": "couverture_jours", "sens": "Jours de vente couverts par le stock."},
                ],
            }
        ],
        "contrat_reference": "stocks_quotidiens.odcs.yaml",
        "slo": {
            "fraicheur_heures": 26,
            "completude_pct": 100,
            "disponibilite": "7 j / 7, build nocturne de 2 h 30",
        },
        "consommateurs": [
            {"type": "vue", "id": "dashboard", "usage": "KPI de couverture"},
            {"type": "module", "id": "alerte_rupture"},
            {"type": "export", "id": "outbox-slack"},
        ],
        "question_exemple": "Quelle est la couverture de stock moyenne ?",
        "version": "1.0.0",
        "changelog": [
            {
                "version": "1.0.0",
                "date": "2026-11-16",
                "description": "Declaration initiale.",
                "auteur": "Heni",
            }
        ],
    }
    declaration.update(surcharges)
    return declaration


def data_product_offre() -> dict:
    """Second data product valide, domaine offre."""
    return data_product_valide(
        id="rentabilite_familles",
        nom="Rentabilite des familles",
        domaine="offre",
        description=(
            "Chiffre d'affaires, marge brute et rotation des stocks par "
            "famille de produits importes, en ariary."
        ),
        ports_de_sortie=[
            {
                "table": "offre_marge_familles",
                "grain": "une ligne par famille marchande",
                "colonnes": [
                    {"nom": "famille", "sens": "Famille marchande du referentiel."},
                    {"nom": "taux_marge", "sens": "Taux de marge brute entre 0 et 1."},
                ],
            }
        ],
        contrat_reference="ventes.odcs.yaml",
        question_exemple="Quelles familles ont les meilleures marges ?",
    )


def ecrire_data_product(repo: str, declaration: dict, nom: str | None = None) -> str:
    """Ecrit un data product YAML dans catalogue/<domaine>/ du repo."""
    domaine = declaration.get("domaine") or "supply"
    dossier = os.path.join(repo, "catalogue", domaine)
    os.makedirs(dossier, exist_ok=True)
    chemin = os.path.join(dossier, nom or f"{declaration['id']}.yaml")
    with open(chemin, "w", encoding="utf-8") as fichier:
        yaml.safe_dump(declaration, fichier, allow_unicode=True, sort_keys=False)
    return chemin


def ecrire_etat_build(repo: str, il_y_a_heures: float, statut: str = "succes") -> None:
    """etat_build.json de fixture : dernier build a `il_y_a_heures` heures."""
    horodatage = (
        MAINTENANT - datetime.timedelta(hours=il_y_a_heures)
    ).isoformat(timespec="seconds")
    etat = {
        "horodatage": horodatage,
        "statut": statut,
        "pass": 151 if statut == "succes" else 0,
        "fail": 0 if statut == "succes" else 1,
        "total": 151,
        "duree_s": 1.0,
        "declencheur": "nocturne",
        "historique": [
            {"horodatage": horodatage, "statut": statut},
            {
                "horodatage": (
                    MAINTENANT - datetime.timedelta(hours=il_y_a_heures + 10)
                ).isoformat(timespec="seconds"),
                "statut": "succes",
            },
        ],
    }
    with open(
        os.path.join(repo, "etat_build.json"), "w", encoding="utf-8"
    ) as fichier:
        json.dump(etat, fichier, ensure_ascii=False, indent=2)


def construire_catalogue(repo: str) -> None:
    """Dossier catalogue/ complet : schema + un data product par domaine."""
    dossier_schema = os.path.join(repo, "catalogue", "schema")
    os.makedirs(dossier_schema, exist_ok=True)
    with open(
        os.path.join(dossier_schema, "data_product.schema.json"),
        "w",
        encoding="utf-8",
    ) as fichier:
        json.dump(SCHEMA_DATA_PRODUCT, fichier, ensure_ascii=False, indent=2)
    ecrire_data_product(repo, data_product_valide())
    ecrire_data_product(repo, data_product_offre())


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """Repo dbt factice avec catalogue/ et un build en succes il y a 8 h."""
    chemin = construire_repo(tmp_path)
    construire_catalogue(chemin)
    ecrire_etat_build(chemin, il_y_a_heures=8)
    monkeypatch.setenv("DBT_DIR", chemin)
    return chemin


def _data_product(verdict: dict, identifiant: str) -> dict:
    return next(
        d for d in verdict["data_products"] if d["id"] == identifiant
    )


# ---------------------------------------------------------------------------
# Chargement et validation JSON Schema
# ---------------------------------------------------------------------------

class TestChargementEtSchema:
    def test_catalogue_absent_liste_vide(self, tmp_path):
        assert moteur.charger_data_products(str(tmp_path)) == []

    def test_data_products_charges_et_ranges_par_domaine(self, repo):
        data_products = moteur.charger_data_products(repo)
        assert [d["_fichier"] for d in data_products] == [
            "offre/rentabilite_familles.yaml",
            "supply/couverture_stock.yaml",
        ]
        assert [d["_domaine_dossier"] for d in data_products] == [
            "offre", "supply",
        ]

    def test_yaml_valides_contre_le_schema(self, repo):
        verdict = moteur.evaluer_tout(repo)
        assert verdict["nb_data_products"] == 2
        assert verdict["nb_domaines"] == 2
        assert verdict["domaines"] == ["offre", "supply"]
        assert verdict["tous_sains"] is True
        assert all(not d["problemes"] for d in verdict["data_products"])

    def test_schema_detecte_un_champ_manquant(self, repo):
        declaration = data_product_valide(id="sans_owner")
        del declaration["owner"]
        ecrire_data_product(repo, declaration)
        verdict = moteur.evaluer_tout(repo)
        casse = _data_product(verdict, "sans_owner")
        assert casse["statut"] == "invalide"
        assert any("owner" in p for p in casse["problemes"])
        assert verdict["tous_sains"] is False
        assert verdict["nb_invalides"] == 1

    def test_schema_introuvable(self, tmp_path):
        with pytest.raises(moteur.ErreurCatalogue):
            moteur.charger_schema(str(tmp_path))


# ---------------------------------------------------------------------------
# References croisees : marts, contrats, consommateurs, domaine
# ---------------------------------------------------------------------------

class TestReferencesCroisees:
    def test_mart_inexistant_detecte(self, repo):
        declaration = data_product_valide(id="mart_fantome")
        declaration["ports_de_sortie"][0]["table"] = "mart_fantome"
        ecrire_data_product(repo, declaration)
        verdict = moteur.evaluer_tout(repo)
        casse = _data_product(verdict, "mart_fantome")
        assert casse["statut"] == "invalide"
        assert any(
            "mart_fantome" in p and "Port de sortie" in p
            for p in casse["problemes"]
        )
        assert casse["ports_de_sortie"][0]["nb_lignes"] is None

    def test_contrat_manquant_detecte(self, repo):
        declaration = data_product_valide(
            id="contrat_absent",
            contrat_reference="inexistant.odcs.yaml",
        )
        ecrire_data_product(repo, declaration)
        verdict = moteur.evaluer_tout(repo)
        casse = _data_product(verdict, "contrat_absent")
        assert casse["statut"] == "invalide"
        assert any(
            "inexistant.odcs.yaml" in p and "Contrat introuvable" in p
            for p in casse["problemes"]
        )

    def test_consommateur_inconnu_detecte(self, repo):
        declaration = data_product_valide(
            id="consommateur_inconnu",
            consommateurs=[{"type": "vue", "id": "tableau_magique"}],
        )
        ecrire_data_product(repo, declaration)
        verdict = moteur.evaluer_tout(repo)
        casse = _data_product(verdict, "consommateur_inconnu")
        assert casse["statut"] == "invalide"
        assert any("tableau_magique" in p for p in casse["problemes"])

    def test_domaine_incoherent_detecte(self, repo):
        declaration = data_product_valide(id="mal_range", domaine="offre")
        # Range dans supply/ alors qu'il declare le domaine offre.
        dossier = os.path.join(repo, "catalogue", "supply")
        with open(
            os.path.join(dossier, "mal_range.yaml"), "w", encoding="utf-8"
        ) as fichier:
            yaml.safe_dump(declaration, fichier, allow_unicode=True,
                           sort_keys=False)
        verdict = moteur.evaluer_tout(repo)
        casse = _data_product(verdict, "mal_range")
        assert casse["statut"] == "invalide"
        assert any("Domaine incoherent" in p for p in casse["problemes"])


# ---------------------------------------------------------------------------
# Etat vivant : fraicheur, volumes, contrat lie, maturite
# ---------------------------------------------------------------------------

class TestEtatVivant:
    def test_etat_vivant_depuis_les_fixtures(self, repo):
        verdict = moteur.evaluer_tout(repo)
        couverture = _data_product(verdict, "couverture_stock")

        fraicheur = couverture["fraicheur"]
        assert fraicheur["statut"] == "a_jour"
        assert 7.5 <= fraicheur["age_heures"] <= 8.5
        assert fraicheur["limite_heures"] == 26

        # Volume lu dans la base fixture DuckDB (TEST_MODE=1).
        assert couverture["ports_de_sortie"][0]["nb_lignes"] > 0

        assert couverture["contrat"]["statut"] == "conforme"
        assert couverture["contrat"]["nom"] == "Stocks quotidiens"
        assert couverture["maturite"]["score"] == 4
        assert couverture["statut"] == "sain"

    def test_fraicheur_depassee_degrade(self, repo):
        ecrire_etat_build(repo, il_y_a_heures=30)
        verdict = moteur.evaluer_tout(repo)
        couverture = _data_product(verdict, "couverture_stock")
        assert couverture["fraicheur"]["statut"] == "depassee"
        assert couverture["statut"] == "degrade"
        assert couverture["problemes"] == []
        assert verdict["tous_sains"] is False
        assert verdict["nb_invalides"] == 0

    def test_dernier_succes_cherche_dans_l_historique(self, repo):
        # Dernier build en echec : la fraicheur se cale sur le dernier succes.
        ecrire_etat_build(repo, il_y_a_heures=2, statut="echec")
        verdict = moteur.evaluer_tout(repo)
        couverture = _data_product(verdict, "couverture_stock")
        assert couverture["fraicheur"]["statut"] == "a_jour"
        assert 11.5 <= couverture["fraicheur"]["age_heures"] <= 12.5
        assert couverture["fraicheur"]["dernier_build"]

    def test_sans_aucun_build_fraicheur_inconnue(self, repo):
        os.remove(os.path.join(repo, "etat_build.json"))
        verdict = moteur.evaluer_tout(repo)
        couverture = _data_product(verdict, "couverture_stock")
        assert couverture["fraicheur"]["statut"] == "inconnue"
        assert couverture["statut"] == "degrade"

    def test_contrat_viole_degrade_le_data_product(self, repo):
        chemin = os.path.join(repo, "seeds", "ventes.csv")
        with open(chemin, "rb") as fichier:
            contenu = fichier.read().decode("utf-8")
        date_hier = (
            datetime.date.today() - datetime.timedelta(days=1)
        ).isoformat()
        contenu += f"V00666,{date_hier},P0001,E01,magasin,1,-5000\r\n"
        with open(chemin, "wb") as fichier:
            fichier.write(contenu.encode("utf-8"))

        verdict = moteur.evaluer_tout(repo)
        rentabilite = _data_product(verdict, "rentabilite_familles")
        assert rentabilite["contrat"]["statut"] == "viole"
        assert rentabilite["statut"] == "degrade"
        # L'autre data product reste sain : son contrat n'est pas touche.
        assert _data_product(verdict, "couverture_stock")["statut"] == "sain"


# ---------------------------------------------------------------------------
# CLI python -m catalogue
# ---------------------------------------------------------------------------

class TestCli:
    def test_tout_sain_code_zero(self, repo, capsys):
        assert cli.principal() == 0
        sortie = capsys.readouterr().out
        assert "2 domaine(s), 2 data product(s), 2 sain(s)" in sortie
        assert "tous les data products sont sains" in sortie

    def test_invalide_code_un(self, repo, capsys):
        declaration = data_product_valide(id="mart_fantome")
        declaration["ports_de_sortie"][0]["table"] = "mart_fantome"
        ecrire_data_product(repo, declaration)
        assert cli.principal() == 1
        sortie = capsys.readouterr().out
        assert "[INVALIDE]" in sortie
        assert "declarations a corriger" in sortie

    def test_degrade_reste_code_zero(self, repo, capsys):
        ecrire_etat_build(repo, il_y_a_heures=30)
        assert cli.principal() == 0
        sortie = capsys.readouterr().out
        assert "[DEGRADE]" in sortie
        assert "au moins un data product degrade" in sortie


# ---------------------------------------------------------------------------
# API SupplySense : GET /api/catalogue
# ---------------------------------------------------------------------------

def appel(url: str, chemin: str):
    requete = urllib.request.Request(url + chemin)
    try:
        with urllib.request.urlopen(requete, timeout=10) as reponse:
            return reponse.status, json.loads(reponse.read().decode("utf-8"))
    except urllib.error.HTTPError as erreur:
        return erreur.code, json.loads(erreur.read().decode("utf-8"))


class TestApiCockpit:
    @pytest.fixture()
    def cockpit(self, repo):
        from cockpit import serveur as cockpit_serveur

        serveur = cockpit_serveur.creer_serveur(0)
        thread = threading.Thread(target=serveur.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{serveur.server_address[1]}"
        finally:
            serveur.shutdown()
            serveur.server_close()

    def test_get_catalogue(self, cockpit):
        code, corps = appel(cockpit, "/api/catalogue")
        assert code == 200
        assert corps["ok"] is True
        assert corps["nb_domaines"] == 2
        assert corps["nb_data_products"] == 2
        assert corps["tous_sains"] is True
        assert {d["id"] for d in corps["data_products"]} == {
            "couverture_stock", "rentabilite_familles",
        }
        couverture = next(
            d for d in corps["data_products"] if d["id"] == "couverture_stock"
        )
        assert couverture["fraicheur"]["statut"] == "a_jour"
        assert couverture["maturite"]["score"] == 4
        assert couverture["question_exemple"]
