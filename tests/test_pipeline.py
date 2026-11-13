"""Tests du module pipeline.

Aucun dbt build reel : la commande de build est remplacee par un script
factice (PIPELINE_COMMANDE) qui depose un run_results.json de fixture
(vert ou casse, celles du gardien de qualite) dans un repo dbt temporaire.
"""

import json
import os
import sys
import textwrap

import pytest

from pipeline import moteur

DOSSIER_FIXTURES_GARDIEN = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "gardien_qualite",
    "fixtures",
)
RUN_VERT = os.path.join(DOSSIER_FIXTURES_GARDIEN, "run_results_vert.json")
RUN_CASSE = os.path.join(DOSSIER_FIXTURES_GARDIEN, "run_results_casse.json")

SCRIPT_FACTICE = textwrap.dedent(
    """\
    import json, os, shutil, sys

    source, code = sys.argv[1], int(sys.argv[2])
    os.makedirs("target", exist_ok=True)
    shutil.copy(source, os.path.join("target", "run_results.json"))
    # Photographie de l'etat partage pendant le build, pour verifier
    # que le pipeline a bien publie le statut en_cours avant de lancer dbt.
    if os.path.exists("etat_build.json"):
        shutil.copy(
            "etat_build.json", os.path.join("target", "etat_pendant_build.json")
        )
    sys.exit(code)
    """
)


@pytest.fixture()
def repo_dbt_factice(tmp_path, monkeypatch):
    """Repo dbt temporaire + outbox isolee, sans toucher au vrai repo."""
    dossier_dbt = tmp_path / "repo-dbt"
    dossier_dbt.mkdir()
    (dossier_dbt / "target").mkdir()
    monkeypatch.setenv("DBT_DIR", str(dossier_dbt))
    monkeypatch.setenv("PIPELINE_OUTBOX", str(tmp_path / "outbox"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    script = tmp_path / "faux_build.py"
    script.write_text(SCRIPT_FACTICE, encoding="utf-8")
    return {"dbt": dossier_dbt, "script": script, "outbox": tmp_path / "outbox"}


def _brancher_commande(monkeypatch, repo, fixture, code_sortie):
    """Branche la commande factice : copie `fixture` puis sort avec `code_sortie`."""
    monkeypatch.setattr(
        moteur,
        "PIPELINE_COMMANDE",
        [sys.executable, str(repo["script"]), fixture, str(code_sortie)],
    )


def _lire_etat_fichier(repo):
    with open(repo["dbt"] / "etat_build.json", encoding="utf-8") as fichier:
        return json.load(fichier)


class TestLireEtat:
    def test_jamais_construit_sans_fichier(self, repo_dbt_factice):
        etat = moteur.lire_etat()
        assert etat["statut"] == "jamais_construit"
        assert etat["historique"] == []
        assert etat["horodatage"] is None

    def test_relit_le_fichier_ecrit(self, repo_dbt_factice, monkeypatch):
        _brancher_commande(monkeypatch, repo_dbt_factice, RUN_VERT, 0)
        moteur.executer_build("manuel")
        assert moteur.lire_etat() == _lire_etat_fichier(repo_dbt_factice)


class TestBuildSucces:
    def test_code_zero_et_etat_succes(self, repo_dbt_factice, monkeypatch):
        _brancher_commande(monkeypatch, repo_dbt_factice, RUN_VERT, 0)
        assert moteur.executer_build("supplyops") == 0

        etat = _lire_etat_fichier(repo_dbt_factice)
        assert etat["statut"] == "succes"
        assert etat["pass"] == 4
        assert etat["fail"] == 0
        assert etat["total"] == 4
        assert etat["duree_s"] == pytest.approx(4.813)
        assert etat["declencheur"] == "supplyops"
        assert etat["horodatage"]
        assert len(etat["historique"]) == 1
        assert etat["historique"][0]["statut"] == "succes"

    def test_statut_en_cours_publie_pendant_le_build(
        self, repo_dbt_factice, monkeypatch
    ):
        _brancher_commande(monkeypatch, repo_dbt_factice, RUN_VERT, 0)
        moteur.executer_build("nocturne")
        chemin = repo_dbt_factice["dbt"] / "target" / "etat_pendant_build.json"
        with open(chemin, encoding="utf-8") as fichier:
            etat_pendant = json.load(fichier)
        assert etat_pendant["statut"] == "en_cours"
        assert etat_pendant["declencheur"] == "nocturne"

    def test_verrou_libere_apres_le_build(self, repo_dbt_factice, monkeypatch):
        _brancher_commande(monkeypatch, repo_dbt_factice, RUN_VERT, 0)
        moteur.executer_build("manuel")
        assert not os.path.exists(repo_dbt_factice["dbt"] / ".pipeline.lock")

    def test_aucun_diagnostic_en_succes(self, repo_dbt_factice, monkeypatch):
        _brancher_commande(monkeypatch, repo_dbt_factice, RUN_VERT, 0)
        moteur.executer_build("manuel")
        outbox = repo_dbt_factice["outbox"]
        assert not os.path.isdir(outbox) or not os.listdir(outbox)


class TestBuildEchec:
    def test_code_un_et_etat_echec(self, repo_dbt_factice, monkeypatch):
        _brancher_commande(monkeypatch, repo_dbt_factice, RUN_CASSE, 1)
        assert moteur.executer_build("manuel") == 1

        etat = _lire_etat_fichier(repo_dbt_factice)
        assert etat["statut"] == "echec"
        assert etat["pass"] == 2
        assert etat["fail"] == 3
        assert etat["total"] == 5
        assert etat["declencheur"] == "manuel"

    def test_echec_meme_si_la_commande_sort_a_zero(
        self, repo_dbt_factice, monkeypatch
    ):
        """Des tests fail dans run_results.json suffisent a marquer l'echec."""
        _brancher_commande(monkeypatch, repo_dbt_factice, RUN_CASSE, 0)
        assert moteur.executer_build("manuel") == 1
        assert _lire_etat_fichier(repo_dbt_factice)["statut"] == "echec"

    def test_diagnostic_du_gardien_depose_dans_outbox(
        self, repo_dbt_factice, monkeypatch
    ):
        _brancher_commande(monkeypatch, repo_dbt_factice, RUN_CASSE, 1)
        moteur.executer_build("manuel")
        fichiers = [
            f
            for f in os.listdir(repo_dbt_factice["outbox"])
            if f.startswith("diagnostic-") and f.endswith(".md")
        ]
        assert len(fichiers) == 1
        with open(
            repo_dbt_factice["outbox"] / fichiers[0], encoding="utf-8"
        ) as fichier:
            markdown = fichier.read()
        assert "Diagnostic qualite des donnees" in markdown
        assert "## 1." in markdown

    def test_verrou_libere_apres_un_echec(self, repo_dbt_factice, monkeypatch):
        _brancher_commande(monkeypatch, repo_dbt_factice, RUN_CASSE, 1)
        moteur.executer_build("manuel")
        assert not os.path.exists(repo_dbt_factice["dbt"] / ".pipeline.lock")


class TestVerrou:
    def test_refuse_un_deuxieme_build_simultane(
        self, repo_dbt_factice, monkeypatch
    ):
        verrou = repo_dbt_factice["dbt"] / ".pipeline.lock"
        verrou.write_text("{}", encoding="utf-8")
        _brancher_commande(monkeypatch, repo_dbt_factice, RUN_VERT, 0)

        assert moteur.executer_build("manuel") == 1
        # L'etat n'a pas ete touche et le verrou du build en cours reste la.
        assert not os.path.exists(repo_dbt_factice["dbt"] / "etat_build.json")
        assert os.path.exists(verrou)

    def test_builds_successifs_apres_liberation(
        self, repo_dbt_factice, monkeypatch
    ):
        _brancher_commande(monkeypatch, repo_dbt_factice, RUN_VERT, 0)
        assert moteur.executer_build("manuel") == 0
        assert moteur.executer_build("manuel") == 0
        assert len(_lire_etat_fichier(repo_dbt_factice)["historique"]) == 2


class TestHistorique:
    def test_borne_a_vingt_builds(self, repo_dbt_factice, monkeypatch):
        _brancher_commande(monkeypatch, repo_dbt_factice, RUN_VERT, 0)
        for _ in range(23):
            moteur.executer_build("nocturne")
        etat = _lire_etat_fichier(repo_dbt_factice)
        assert len(etat["historique"]) == moteur.TAILLE_HISTORIQUE == 20

    def test_le_plus_recent_en_tete(self, repo_dbt_factice, monkeypatch):
        _brancher_commande(monkeypatch, repo_dbt_factice, RUN_VERT, 0)
        moteur.executer_build("manuel")
        _brancher_commande(monkeypatch, repo_dbt_factice, RUN_CASSE, 1)
        moteur.executer_build("supplysense")
        etat = _lire_etat_fichier(repo_dbt_factice)
        assert etat["historique"][0]["statut"] == "echec"
        assert etat["historique"][0]["declencheur"] == "supplysense"
        assert etat["historique"][1]["statut"] == "succes"


class TestGardeFous:
    def test_declencheur_inconnu_refuse(self, repo_dbt_factice):
        with pytest.raises(ValueError):
            moteur.executer_build("inconnu")

    def test_repo_dbt_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DBT_DIR", str(tmp_path / "nexiste-pas"))
        assert moteur.executer_build("manuel") == 1

    def test_run_results_manquant_marque_l_echec(
        self, repo_dbt_factice, monkeypatch, tmp_path
    ):
        """Commande qui ne produit aucun run_results.json : echec propre."""
        script = tmp_path / "ne_fait_rien.py"
        script.write_text("import sys; sys.exit(0)\n", encoding="utf-8")
        monkeypatch.setattr(
            moteur, "PIPELINE_COMMANDE", [sys.executable, str(script)]
        )
        assert moteur.executer_build("manuel") == 1
        etat = _lire_etat_fichier(repo_dbt_factice)
        assert etat["statut"] == "echec"
        assert etat["total"] == 0
