"""Tests du module fiches_produits contre la base fixture.

Aucun appel reseau : le mode LLM est mocke, le mode gabarit est deterministe.
"""

import json
import os

import pytest
import yaml

from fiches_produits import garde_fous, moteur


def _sans_cle_api(monkeypatch):
    """Garantit l'absence de cle API : mode gabarit deterministe."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# Generation deterministe conforme pour 3 produits de la fixture
# ---------------------------------------------------------------------------

def test_generation_deterministe_trois_produits(tmp_path, monkeypatch):
    _sans_cle_api(monkeypatch)
    resultats = moteur.generer(limite=3, racine=str(tmp_path))

    assert len(resultats) == 3
    for resultat in resultats:
        assert resultat["statut"] == "en_attente"
        assert resultat["mode"] == "gabarit"
        assert resultat["motifs"] == []
        assert os.path.exists(resultat["chemin"])

    # Les tops de la fixture sortent en premier.
    ids = [r["id_produit"] for r in resultats]
    assert ids == ["P018", "P001", "P004"]

    # Chaque fiche deposee est conforme et porte le bon en-tete YAML.
    for resultat in resultats:
        entete, corps = moteur.lire_fiche(resultat["chemin"])
        assert entete["statut"] == "en_attente"
        assert entete["id_produit"] == resultat["id_produit"]
        assert entete["mode"] == "gabarit"
        assert "# " in corps and "## Points forts" in corps


def test_fiche_gabarit_respecte_les_garde_fous(monkeypatch):
    _sans_cle_api(monkeypatch)
    for produit in moteur.lire_produits(limite=3):
        fiche = moteur.generer_fiche_gabarit(produit)
        assert garde_fous.verifier_fiche(fiche) == []
        nb_mots = garde_fous.compter_mots(fiche["description"])
        assert 80 <= nb_mots <= 120
        assert len(fiche["points_forts"]) == 3


def test_matiere_et_dimension_tirees_du_nom():
    assert moteur.extraire_matiere("Coussin lin lave Mia ocre") == "lin lave"
    assert moteur.extraire_dimension("Tapis berbere Atlas 160x230") == "160x230"
    assert moteur.extraire_dimension("Canape 3 places Oslo gris") == "3 places"
    assert moteur.extraire_matiere("Miroir soleil Rani dore") is None


# ---------------------------------------------------------------------------
# Fiche non conforme injectee : rejet avec le bon motif
# ---------------------------------------------------------------------------

def _fiche_valide():
    """Fiche de reference conforme, construite par le gabarit."""
    produit = {
        "id_produit": "P007",
        "nom_produit": "Tapis berbere Atlas 160x230",
        "famille": "Tapis",
    }
    return moteur.generer_fiche_gabarit(produit)


def test_rejet_superlatif_interdit():
    fiche = _fiche_valide()
    fiche["titre"] = "Le meilleur tapis berbere de la decennie"
    motifs = garde_fous.verifier_fiche(fiche)
    assert any("superlatif interdit" in m for m in motifs)
    assert any("le meilleur" in m for m in motifs)


def test_rejet_affirmation_chiffree_inventee():
    fiche = _fiche_valide()
    fiche["description"] += " Profitez de 30 % de remise, soit 199 euros."
    motifs = garde_fous.verifier_fiche(fiche)
    assert any("pourcentage non fourni" in m for m in motifs)
    assert any("prix non fourni" in m for m in motifs)


def test_rejet_description_hors_bornes():
    fiche = _fiche_valide()
    fiche["description"] = "Un tapis douillet pour le salon."
    motifs = garde_fous.verifier_fiche(fiche)
    assert any("description hors bornes" in m for m in motifs)


def test_rejet_points_forts_manquants():
    fiche = _fiche_valide()
    fiche["points_forts"] = ["Seul point fort", ""]
    motifs = garde_fous.verifier_fiche(fiche)
    assert any("points forts invalides" in m for m in motifs)


def test_generation_rejette_une_fiche_injectee_non_conforme(
    tmp_path, monkeypatch
):
    _sans_cle_api(monkeypatch)
    fiche_truquee = _fiche_valide()
    fiche_truquee["titre"] = "L'incontournable tapis parfait du salon"
    monkeypatch.setattr(
        moteur, "generer_fiche_gabarit", lambda produit: fiche_truquee
    )

    resultats = moteur.generer(limite=1, racine=str(tmp_path))

    assert len(resultats) == 1
    assert resultats[0]["statut"] == "rejetee"
    assert resultats[0]["chemin"] is None
    assert any("incontournable" in m for m in resultats[0]["motifs"])
    assert any("parfait" in m for m in resultats[0]["motifs"])
    # Aucun fichier n'a ete depose en zone de validation.
    dossier_attente = tmp_path / moteur.NOM_DOSSIER_A_VALIDER
    assert list(dossier_attente.glob("*.md")) == []


# ---------------------------------------------------------------------------
# Flux de validation humaine : approuver deplace et change le statut
# ---------------------------------------------------------------------------

def test_approuver_deplace_la_fiche_et_change_le_statut(tmp_path, monkeypatch):
    _sans_cle_api(monkeypatch)
    resultats = moteur.generer(limite=1, racine=str(tmp_path))
    id_produit = resultats[0]["id_produit"]
    chemin_attente = resultats[0]["chemin"]
    assert os.path.exists(chemin_attente)

    chemin_validee = moteur.approuver(id_produit, racine=str(tmp_path))

    # Le fichier a quitte a_valider/ et se trouve dans validees/.
    assert not os.path.exists(chemin_attente)
    assert os.path.exists(chemin_validee)
    assert moteur.NOM_DOSSIER_VALIDEES in chemin_validee

    entete, _ = moteur.lire_fiche(chemin_validee)
    assert entete["statut"] == "validee"
    assert entete["approuvee_le"]


def test_approuver_fiche_inconnue_echoue(tmp_path):
    with pytest.raises(FileNotFoundError):
        moteur.approuver("P999", racine=str(tmp_path))


# ---------------------------------------------------------------------------
# Mode LLM mocke : memes garde-fous, jamais d'appel reseau
# ---------------------------------------------------------------------------

def test_mode_llm_mocke_fiche_conforme_acceptee(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-de-test-jamais-utilisee")
    fiche_conforme = _fiche_valide()
    reponse_llm = json.dumps(fiche_conforme, ensure_ascii=False)
    appels = []

    def faux_appel(prompt):
        appels.append(prompt)
        return reponse_llm

    monkeypatch.setattr(moteur, "_appeler_llm", faux_appel)

    resultats = moteur.generer(limite=1, racine=str(tmp_path))

    assert len(appels) == 1
    assert "ton de marque" in appels[0].lower()
    assert resultats[0]["statut"] == "en_attente"
    assert resultats[0]["mode"] == "llm"
    entete, _ = moteur.lire_fiche(resultats[0]["chemin"])
    assert entete["mode"] == "llm"
    assert entete["statut"] == "en_attente"


def test_mode_llm_mocke_fiche_non_conforme_rejetee(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-de-test-jamais-utilisee")
    fiche_truquee = _fiche_valide()
    fiche_truquee["description"] += " Une remise de 50 % vous attend."
    reponse_llm = json.dumps(fiche_truquee, ensure_ascii=False)
    monkeypatch.setattr(moteur, "_appeler_llm", lambda prompt: reponse_llm)

    resultats = moteur.generer(limite=1, racine=str(tmp_path))

    assert resultats[0]["statut"] == "rejetee"
    assert resultats[0]["mode"] == "llm"
    assert any("pourcentage non fourni" in m for m in resultats[0]["motifs"])
    dossier_attente = tmp_path / moteur.NOM_DOSSIER_A_VALIDER
    assert list(dossier_attente.glob("*.md")) == []


def test_mode_llm_en_echec_replie_sur_le_gabarit(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-de-test-jamais-utilisee")

    def appel_en_echec(prompt):
        raise RuntimeError("panne simulee du fournisseur")

    monkeypatch.setattr(moteur, "_appeler_llm", appel_en_echec)

    resultats = moteur.generer(limite=1, racine=str(tmp_path))

    assert resultats[0]["statut"] == "en_attente"
    assert resultats[0]["mode"] == "gabarit"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_generer_et_approuver(tmp_path, monkeypatch, capsys):
    _sans_cle_api(monkeypatch)
    import fiches_produits.__main__ as cli

    monkeypatch.setattr(
        cli, "generer", lambda limite: moteur.generer(limite, str(tmp_path))
    )
    monkeypatch.setattr(
        cli,
        "approuver",
        lambda id_produit: moteur.approuver(id_produit, str(tmp_path)),
    )

    assert cli.principal(["generer", "--limite", "2"]) == 0
    sortie = capsys.readouterr().out
    assert "a_valider" in sortie
    assert "Validation humaine requise" in sortie

    assert cli.principal(["approuver", "P018"]) == 0
    sortie = capsys.readouterr().out
    assert "Fiche approuvee" in sortie

    # Une fiche inconnue retourne un code d'erreur.
    assert cli.principal(["approuver", "P999"]) == 1


def test_yaml_en_tete_bien_forme(tmp_path, monkeypatch):
    _sans_cle_api(monkeypatch)
    resultats = moteur.generer(limite=1, racine=str(tmp_path))
    with open(resultats[0]["chemin"], encoding="utf-8") as fichier:
        contenu = fichier.read()
    assert contenu.startswith("---\n")
    bloc = contenu.split("---\n")[1]
    entete = yaml.safe_load(bloc)
    assert entete["statut"] == "en_attente"
    assert entete["genere_le"]
