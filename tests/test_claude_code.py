"""Tests du moteur Claude Code (claude -p) : tout est bouchonne, zero reseau."""

import json
import subprocess

import pytest

from agent_qa import moteur as qa
from fiches_produits import moteur as fiches
from lib import base, claude_code


def _forcer_cli(monkeypatch, sortie: str, code: int = 0):
    """Simule une CLI claude presente qui repond `sortie`."""
    monkeypatch.setenv("CLAUDE_CODE_FORCER", "1")
    monkeypatch.setattr(claude_code.shutil, "which", lambda _: "/usr/local/bin/claude")

    def faux_run(commande, capture_output, text, timeout):
        return subprocess.CompletedProcess(commande, code, stdout=sortie, stderr="")

    monkeypatch.setattr(claude_code.subprocess, "run", faux_run)


def test_question_inconnue_passe_par_claude_code(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_QA_JOURNAL", str(tmp_path / "journal.jsonl"))
    _forcer_cli(
        monkeypatch,
        "SELECT nom_entrepot, SUM(quantite_disponible) AS total_unites\n"
        "FROM supply_couverture_stock GROUP BY nom_entrepot\n"
        "ORDER BY total_unites DESC LIMIT 1",
    )
    resultat = qa.repondre("quel entrepot detient le plus d'unites en stock ?")
    assert resultat["mode"] == "claude_code"
    assert len(resultat["lignes"]) == 1
    assert "total unites" in resultat["reponse"] or "resultat" in resultat["reponse"].lower()
    journal = (tmp_path / "journal.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(journal[-1])["mode"] == "claude_code"


def test_question_type_reste_en_repli_meme_avec_cli(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_QA_JOURNAL", str(tmp_path / "journal.jsonl"))
    _forcer_cli(monkeypatch, "SELECT 1")
    resultat = qa.repondre("quelle famille a la pire rotation ?")
    assert resultat["mode"] == "repli"


def test_ecriture_generee_refusee_par_la_garde(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_QA_JOURNAL", str(tmp_path / "journal.jsonl"))
    _forcer_cli(monkeypatch, "UPDATE supply_couverture_stock SET quantite_disponible = 0")
    with pytest.raises(base.RequeteInterditeErreur):
        qa.repondre("mets tous les stocks a zero")


def test_table_interdite_refusee_par_la_garde(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_QA_JOURNAL", str(tmp_path / "journal.jsonl"))
    _forcer_cli(monkeypatch, "SELECT * FROM information_schema.tables")
    with pytest.raises(base.RequeteInterditeErreur):
        qa.repondre("liste toutes les tables du systeme")


def test_sans_cli_la_question_inconnue_reste_incomprise(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_QA_JOURNAL", str(tmp_path / "journal.jsonl"))
    monkeypatch.setenv("CLAUDE_CODE_FORCER", "1")
    monkeypatch.setattr(claude_code.shutil, "which", lambda _: None)
    with pytest.raises(qa.QuestionNonCompriseErreur):
        qa.repondre("question totalement hors sujet et inconnue")


def test_desactivation_explicite(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_DESACTIVE", "1")
    monkeypatch.setattr(claude_code.shutil, "which", lambda _: "/usr/local/bin/claude")
    assert claude_code.disponible() is False


def test_fiche_generee_par_claude_code(monkeypatch):
    fiche_json = json.dumps(
        {
            "titre": "Tapis jute Atlas, la chaleur naturelle",
            "description": " ".join(["Un tapis en jute tisse main qui apporte une touche"
                                     " chaleureuse et naturelle au salon."] * 6),
            "points_forts": ["Fibre naturelle", "Tissage artisanal", "Entretien facile"],
        }
    )
    _forcer_cli(monkeypatch, fiche_json)
    produit = {"nom_produit": "Tapis jute Atlas", "famille": "Tapis"}
    fiche, mode = fiches.generer_fiche(produit)
    assert mode == "claude_code"
    assert fiche["titre"].startswith("Tapis jute Atlas")
    assert len(fiche["points_forts"]) == 3


def test_fiche_claude_code_invalide_retombe_sur_gabarit(monkeypatch):
    _forcer_cli(monkeypatch, "ceci n'est pas du JSON")
    produit = {"nom_produit": "Lampe a poser Brume", "famille": "Luminaires"}
    fiche, mode = fiches.generer_fiche(produit)
    assert mode == "gabarit"
    assert fiche["titre"]
