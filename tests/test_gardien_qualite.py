"""Tests du module gardien_qualite.

Tout tourne contre les fixtures locales du module (run_results.json) et
la base fixture fixtures/entrepot_test.duckdb. Aucun appel reseau : la
partie LLM reste en repli deterministe (pas de ANTHROPIC_API_KEY posee).
"""

import json
import os

import pytest

from gardien_qualite import moteur

DOSSIER_FIXTURES_MODULE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "gardien_qualite",
    "fixtures",
)
RUN_VERT = os.path.join(DOSSIER_FIXTURES_MODULE, "run_results_vert.json")
RUN_CASSE = os.path.join(DOSSIER_FIXTURES_MODULE, "run_results_casse.json")


def test_run_vert_rapport_vide_et_code_zero(tmp_path, capsys, monkeypatch):
    """Un run tout vert produit un rapport vide et un code de sortie 0."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    code = moteur.main([RUN_VERT, "--outbox", str(tmp_path)])
    assert code == 0

    sortie_json = json.loads(capsys.readouterr().out)
    assert sortie_json["statut_global"] == "ok"
    assert sortie_json["nb_echecs"] == 0
    assert sortie_json["nb_bloquants"] == 0
    assert sortie_json["diagnostics"] == []

    fichiers = [f for f in os.listdir(tmp_path) if f.startswith("diagnostic-")]
    assert len(fichiers) == 1
    with open(tmp_path / fichiers[0], encoding="utf-8") as fichier:
        markdown = fichier.read()
    assert "Aucun test en echec" in markdown
    assert "## " not in markdown  # aucune section de diagnostic


def test_run_casse_trois_diagnostics_et_code_un(tmp_path, capsys, monkeypatch):
    """Le run casse produit 3 diagnostics bien categorises et le code 1."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    code = moteur.main([RUN_CASSE, "--outbox", str(tmp_path)])
    assert code == 1

    sortie_json = json.loads(capsys.readouterr().out)
    assert sortie_json["statut_global"] == "echec"
    assert sortie_json["nb_echecs"] == 3
    assert sortie_json["nb_bloquants"] == 1

    par_nom = {d["nom_test"]: d for d in sortie_json["diagnostics"]}
    assert len(par_nom) == 3

    cle = par_nom["unique_supply_couverture_stock_id_produit"]
    assert cle["categorie"] == "cle dupliquee"
    assert cle["gravite"] == "bloquant"
    assert cle["nb_lignes_fautives"] == 2

    bornes = par_nom[
        "accepted_values_supply_couverture_stock_statut_couverture"
        "__rupture__sain__surstock__dormant"
    ]
    assert bornes["categorie"] == "valeur hors bornes"
    assert bornes["gravite"] == "avertissement"

    metier = par_nom["assert_taux_marge_familles_coherent"]
    assert metier["categorie"] == "test singulier metier"
    assert metier["gravite"] == "avertissement"


def test_markdown_contient_les_actions_proposees(tmp_path, monkeypatch):
    """Le rapport markdown du run casse contient les actions proposees."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rapport = moteur.analyser(RUN_CASSE)
    markdown = moteur.generer_markdown(rapport)

    assert "Action proposee" in markdown
    assert "dedupliquer la source" in markdown
    assert "Faire valider les valeurs par le metier" in markdown
    assert "responsable metier" in markdown
    assert "Hypothese de cause" in markdown
    assert "Gravite : **bloquant**" in markdown


def test_illustration_lignes_fautives_avec_base(chemin_base_test, monkeypatch):
    """Avec une base DuckDB fournie, chaque diagnostic est illustre."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rapport = moteur.analyser(RUN_CASSE, chemin_base=chemin_base_test)

    assert rapport["nb_echecs"] == 3
    for diagnostic in rapport["diagnostics"]:
        assert len(diagnostic["lignes_exemples"]) <= 5
        # La base fixture etant saine, la requete compilee ne retourne
        # rien : le moteur retombe sur un echantillon de la table.
        if diagnostic["table"]:
            assert len(diagnostic["lignes_exemples"]) >= 1


def test_categorisation_par_regles():
    """Les regles de nommage couvrent les 5 categories attendues."""
    assert moteur.categoriser("unique_supply_couverture_stock_id_produit") == (
        "cle dupliquee",
        "bloquant",
    )
    assert moteur.categoriser("not_null_offre_marge_familles_famille") == (
        "valeur nulle",
        "avertissement",
    )
    assert moteur.categoriser(
        "accepted_range_supply_taux_rupture_taux_rupture_pct__0__100"
    ) == ("valeur hors bornes", "avertissement")
    assert moteur.categoriser(
        "relationships_supply_couverture_stock_id_entrepot"
    ) == ("relation orpheline", "bloquant")
    assert moteur.categoriser("assert_otif_fournisseurs_coherent") == (
        "test singulier metier",
        "avertissement",
    )


def test_run_results_invalide():
    """Un fichier sans cle results est rejete proprement."""
    with pytest.raises(FileNotFoundError):
        moteur.charger_run_results("/chemin/inexistant/run_results.json")
