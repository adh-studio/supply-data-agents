"""Tests du module agent_qa contre la base fixture.

Aucun appel reseau : le mode repli est utilise par defaut et le mode LLM
est teste avec un client entierement mocke.
"""

import json
import os

import pytest

from agent_qa import moteur
from agent_qa.__main__ import principal
from agent_qa.moteur import QuestionNonCompriseErreur, repondre
from lib.base import RequeteInterditeErreur


@pytest.fixture(autouse=True)
def environnement_propre(monkeypatch, tmp_path):
    """Sans cle API (mode repli par defaut) et journal isole par test."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_QA_JOURNAL", str(tmp_path / "journal_qa.jsonl"))


def lire_journal():
    with open(os.environ["AGENT_QA_JOURNAL"], encoding="utf-8") as fichier:
        return [json.loads(ligne) for ligne in fichier if ligne.strip()]


# ---------------------------------------------------------------------------
# Les 8 questions types du mode repli, valeurs verifiees en dur
# ---------------------------------------------------------------------------


def test_pire_rotation():
    resultat = repondre("quelle famille a la pire rotation ?")
    assert resultat["mode"] == "repli"
    assert resultat["question_type"] == "pire_rotation"
    assert resultat["lignes"][0]["famille"] == "Objets d art"
    assert resultat["lignes"][0]["rotation_periode"] == pytest.approx(0.6)
    assert "Objets d art" in resultat["reponse"]
    assert "0.6" in resultat["reponse"]


def test_meilleures_marges():
    resultat = repondre("quelles familles ont les meilleures marges ?")
    assert resultat["question_type"] == "meilleures_marges"
    familles = [l["famille"] for l in resultat["lignes"]]
    assert familles == ["Objets d art", "Decoration", "Miroirs"]
    assert resultat["lignes"][0]["taux_marge"] == pytest.approx(61.4)
    assert "61.4" in resultat["reponse"]


def test_produits_en_rupture():
    resultat = repondre("quels produits sont en rupture de stock ?")
    assert resultat["question_type"] == "produits_rupture"
    noms = [l["nom_produit"] for l in resultat["lignes"]]
    assert noms == [
        "Canape 3 places Oslo gris",
        "Lampadaire arc Milo noir",
        "Tapis berbere Atlas 160x230",
    ]
    assert resultat["reponse"].startswith("3 produits sont en rupture")


def test_couverture_moyenne():
    resultat = repondre("quelle est la couverture moyenne du stock ?")
    assert resultat["question_type"] == "couverture_moyenne"
    # Moyenne sur les 17 produits dont la couverture est calculable
    # (les 3 dormants ont une couverture NULL) : 502.7 / 17 = 29.6.
    assert resultat["lignes"][0]["couverture_moyenne"] == pytest.approx(29.6)
    assert "29.6" in resultat["reponse"]


def test_top_produits():
    resultat = repondre("quels sont les top produits en vente ?")
    assert resultat["question_type"] == "top_produits"
    assert resultat["lignes"][0]["nom_produit"] == "Coussin lin lave Mia ocre"
    assert resultat["lignes"][0]["rang_ca"] == 1
    assert len(resultat["lignes"]) == 5
    assert "Coussin lin lave Mia ocre" in resultat["reponse"]


def test_flop_produits():
    resultat = repondre("quels sont les produits flop ?")
    assert resultat["question_type"] == "flop_produits"
    assert resultat["lignes"][0]["nom_produit"] == "Tapis shaggy Como taupe"
    assert resultat["lignes"][0]["quantite_vendue"] == 12
    assert "Tapis shaggy Como taupe" in resultat["reponse"]


def test_otif_fournisseurs():
    resultat = repondre("quel est le taux otif des fournisseurs ?")
    assert resultat["question_type"] == "otif_fournisseurs"
    noms = [l["nom_fournisseur"] for l in resultat["lignes"]]
    assert noms == [
        "Tissages du Maroc",
        "Mobilier Atlas Export",
        "Metal Design Torino",
    ]
    assert resultat["lignes"][0]["taux_otif"] == pytest.approx(41.7)
    assert "41.7" in resultat["reponse"]


def test_chiffre_affaires_par_famille():
    resultat = repondre("quel est le chiffre d'affaires par famille ?")
    assert resultat["question_type"] == "ca_par_famille"
    assert resultat["lignes"][0]["famille"] == "Canapes"
    assert resultat["lignes"][0]["chiffre_affaires"] == pytest.approx(279450.0)
    assert "Canapes" in resultat["reponse"]
    assert "279450" in resultat["reponse"]


def test_question_non_comprise():
    with pytest.raises(QuestionNonCompriseErreur):
        repondre("quelle est la meteo a Lyon demain ?")


# ---------------------------------------------------------------------------
# Journal d'audit
# ---------------------------------------------------------------------------


def test_journalisation():
    repondre("quelle famille a la pire rotation ?", utilisateur="melody")
    entrees = lire_journal()
    assert len(entrees) == 1
    entree = entrees[0]
    assert entree["utilisateur"] == "melody"
    assert entree["statut"] == "ok"
    assert entree["mode"] == "repli"
    assert "rotation_periode" in entree["sql"]
    assert "Objets d art" in entree["reponse"]


# ---------------------------------------------------------------------------
# Mode LLM avec client mocke (aucun reseau)
# ---------------------------------------------------------------------------


class BlocTexte:
    def __init__(self, texte):
        self.text = texte


class FauxMessage:
    def __init__(self, texte):
        self.content = [BlocTexte(texte)]


class FauxMessages:
    def __init__(self, reponses):
        self.reponses = list(reponses)
        self.appels = []

    def create(self, **parametres):
        self.appels.append(parametres)
        return FauxMessage(self.reponses.pop(0))


class FauxClient:
    def __init__(self, reponses):
        self.messages = FauxMessages(reponses)


def test_llm_sql_passe_par_la_garde(monkeypatch):
    """Le SQL genere par le LLM mocke passe bien par lib.base.requete."""
    sql_attendu = (
        "SELECT famille, rotation_periode FROM offre_marge_familles "
        "ORDER BY rotation_periode ASC LIMIT 1"
    )
    client = FauxClient(
        [
            f"```sql\n{sql_attendu}\n```",
            "La famille Objets d art a la pire rotation avec 0.6.",
        ]
    )

    sql_vus = []
    requete_reelle = moteur.base.requete

    def espion(sql):
        sql_vus.append(sql)
        return requete_reelle(sql)

    monkeypatch.setattr(moteur.base, "requete", espion)

    resultat = repondre("quelle famille a la pire rotation ?", client=client)

    assert resultat["mode"] == "llm"
    assert sql_vus == [sql_attendu]
    assert resultat["sql"] == sql_attendu
    assert resultat["lignes"][0]["famille"] == "Objets d art"
    assert resultat["reponse"] == "La famille Objets d art a la pire rotation avec 0.6."
    assert len(client.messages.appels) == 2
    assert client.messages.appels[0]["model"] == moteur.MODELE_LLM


def test_llm_update_refuse():
    """Une requete UPDATE generee par le LLM est bloquee par la garde."""
    client = FauxClient(
        ["UPDATE supply_couverture_stock SET quantite_disponible = 0"]
    )
    with pytest.raises(RequeteInterditeErreur):
        repondre("mets tous les stocks a zero", client=client)
    # L'echec est journalise pour l'audit.
    entrees = lire_journal()
    assert entrees[-1]["statut"] == "erreur"
    assert "RequeteInterditeErreur" in entrees[-1]["erreur"]


def test_llm_table_hors_allowlist_refusee():
    """Une table hors des 5 marts autorises est refusee."""
    client = FauxClient(["SELECT * FROM clients_confidentiels"])
    with pytest.raises(RequeteInterditeErreur):
        repondre("liste les clients", client=client)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_repond_en_une_phrase(capsys):
    code = principal(["quelle famille a la pire rotation ?"])
    sortie = capsys.readouterr().out.strip()
    assert code == 0
    assert "Objets d art" in sortie
    assert "0.6" in sortie


def test_cli_question_non_comprise(capsys):
    code = principal(["raconte moi une blague"])
    assert code == 2
    assert "Question non comprise" in capsys.readouterr().err
