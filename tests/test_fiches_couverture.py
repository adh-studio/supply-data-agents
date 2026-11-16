"""Tests de la couverture du catalogue de fiches produits.

Moteur : lister_couverture, generer_pour, generer_manquantes sur la base
fixture (TEST_MODE=1, mode gabarit deterministe, aucun reseau).
API cockpit : couverture, generation par produit, generation de masse en
thread (bouchonne) avec progression et verrou 409.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request

import pytest

from cockpit import serveur as cockpit_serveur
from fiches_produits import moteur

NB_PRODUITS_FIXTURE = 16  # taille du referentiel offre_top_flop_produits


def _sans_cle_api(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# lister_couverture : statuts et priorite validee > en_attente > rejetee
# ---------------------------------------------------------------------------


def test_couverture_racine_vierge_tout_a_aucune(tmp_path):
    lignes = moteur.lister_couverture(racine=str(tmp_path))
    assert len(lignes) == NB_PRODUITS_FIXTURE
    assert all(ligne["statut"] == "aucune" for ligne in lignes)
    assert lignes[0]["id_produit"] == "P001"
    assert lignes[0]["nom_produit"]
    assert lignes[0]["famille"]


def test_couverture_statuts_et_priorite(tmp_path, monkeypatch):
    _sans_cle_api(monkeypatch)
    racine = str(tmp_path)
    # P018 : generee puis validee, P001 : en attente, P004 : rejetee.
    moteur.generer(limite=3, racine=racine)
    moteur.approuver("P018", racine=racine)
    moteur.rejeter("P004", "description trop generique", racine=racine)

    statuts = {l["id_produit"]: l["statut"] for l in moteur.lister_couverture(racine)}
    assert statuts["P018"] == "validee"
    assert statuts["P001"] == "en_attente"
    assert statuts["P004"] == "rejetee"
    assert statuts["P002"] == "aucune"

    # Priorite : une fiche validee ET un nouveau brouillon -> validee gagne ;
    # un brouillon en attente ET un vieux rejet -> en_attente gagne.
    (tmp_path / moteur.NOM_DOSSIER_A_VALIDER / "P018.md").write_text(
        "---\nid_produit: P018\n---\n# brouillon\n", encoding="utf-8"
    )
    (tmp_path / moteur.NOM_DOSSIER_REJETEES / "P001.md").write_text(
        "---\nid_produit: P001\n---\n# vieux rejet\n", encoding="utf-8"
    )
    statuts = {l["id_produit"]: l["statut"] for l in moteur.lister_couverture(racine)}
    assert statuts["P018"] == "validee"
    assert statuts["P001"] == "en_attente"


# ---------------------------------------------------------------------------
# generer_pour : id inconnu, doublon refuse, remplacement avec archivage
# ---------------------------------------------------------------------------


def test_generer_pour_id_inconnu_refuse(tmp_path, monkeypatch):
    _sans_cle_api(monkeypatch)
    with pytest.raises(ValueError, match="Produit inconnu du referentiel"):
        moteur.generer_pour("P999", racine=str(tmp_path))


def test_generer_pour_depose_la_fiche(tmp_path, monkeypatch):
    _sans_cle_api(monkeypatch)
    resultat = moteur.generer_pour("P007", racine=str(tmp_path))
    assert resultat["statut"] == "en_attente"
    assert resultat["mode"] == "gabarit"
    assert resultat["archives"] == []
    assert os.path.exists(resultat["chemin"])
    assert moteur.statut_fiche("P007", str(tmp_path)) == "en_attente"


def test_generer_pour_doublon_refuse_sauf_remplacer(tmp_path, monkeypatch):
    _sans_cle_api(monkeypatch)
    racine = str(tmp_path)
    premier = moteur.generer_pour("P007", racine=racine)

    # Fiche en attente existante : refus motive.
    with pytest.raises(moteur.FicheExistanteErreur, match="existe deja"):
        moteur.generer_pour("P007", racine=racine)

    # Avec remplacer : l'ancienne est archivee, la nouvelle deposee.
    resultat = moteur.generer_pour("P007", racine=racine, remplacer=True)
    assert resultat["statut"] == "en_attente"
    assert len(resultat["archives"]) == 1
    assert moteur.NOM_DOSSIER_ARCHIVEES in resultat["archives"][0]
    assert os.path.exists(resultat["archives"][0])
    assert os.path.exists(premier["chemin"])  # remplace par la nouvelle

    # Une fiche validee bloque aussi, et remplacer l'archive aussi.
    moteur.approuver("P007", racine=racine)
    with pytest.raises(moteur.FicheExistanteErreur, match="validee"):
        moteur.generer_pour("P007", racine=racine)
    resultat = moteur.generer_pour("P007", racine=racine, remplacer=True)
    assert resultat["statut"] == "en_attente"
    assert any(
        moteur.NOM_DOSSIER_VALIDEES in chemin for chemin in resultat["archives"]
    )


def test_generer_pour_rejetee_ne_bloque_pas(tmp_path, monkeypatch):
    _sans_cle_api(monkeypatch)
    racine = str(tmp_path)
    moteur.generer_pour("P007", racine=racine)
    moteur.rejeter("P007", "trop vague", racine=racine)
    resultat = moteur.generer_pour("P007", racine=racine)
    assert resultat["statut"] == "en_attente"


# ---------------------------------------------------------------------------
# generer_manquantes : complement seul, poursuite apres echec
# ---------------------------------------------------------------------------


def test_generer_manquantes_ne_regenere_pas_l_existant(tmp_path, monkeypatch):
    _sans_cle_api(monkeypatch)
    racine = str(tmp_path)
    moteur.generer(limite=3, racine=racine)  # P018, P001, P004 en attente
    moteur.approuver("P018", racine=racine)
    moteur.rejeter("P004", "motif de test", racine=racine)

    bilan = moteur.generer_manquantes(racine=racine)

    # 13 produits sans fiche ; P018 (validee), P001 (en attente) et P004
    # (rejetee) sont ignores par defaut.
    assert bilan["generees"] == NB_PRODUITS_FIXTURE - 3
    assert bilan["echouees"] == 0
    assert bilan["ignorees"] == 3
    ids_generes = {r["id_produit"] for r in bilan["resultats"]}
    assert {"P018", "P001", "P004"}.isdisjoint(ids_generes)
    statuts = {l["statut"] for l in moteur.lister_couverture(racine)}
    assert "aucune" not in statuts

    # Plus rien a faire : bilan vide, tout est ignore.
    bilan = moteur.generer_manquantes(racine=racine)
    assert bilan["generees"] == 0
    assert bilan["ignorees"] == NB_PRODUITS_FIXTURE


def test_generer_manquantes_inclut_les_rejetees_sur_option(tmp_path, monkeypatch):
    _sans_cle_api(monkeypatch)
    racine = str(tmp_path)
    moteur.generer_pour("P004", racine=racine)
    moteur.rejeter("P004", "motif de test", racine=racine)

    bilan = moteur.generer_manquantes(racine=racine, inclure_rejetees=True)
    assert bilan["generees"] == NB_PRODUITS_FIXTURE
    assert "P004" in {r["id_produit"] for r in bilan["resultats"]}


def test_generer_manquantes_continue_apres_un_echec(tmp_path, monkeypatch):
    _sans_cle_api(monkeypatch)
    racine = str(tmp_path)
    original = moteur.generer_fiche

    def moteur_en_panne_sur_p007(produit):
        if produit["id_produit"] == "P007":
            raise RuntimeError("panne simulee du moteur")
        return original(produit)

    monkeypatch.setattr(moteur, "generer_fiche", moteur_en_panne_sur_p007)
    progression = []
    bilan = moteur.generer_manquantes(
        racine=racine, rappel=lambda fait, total, produit: progression.append((fait, total))
    )

    assert bilan["echouees"] == 1
    assert bilan["generees"] == NB_PRODUITS_FIXTURE - 1
    echec = next(r for r in bilan["resultats"] if r["id_produit"] == "P007")
    assert echec["statut"] == "echec"
    assert "panne simulee" in echec["motifs"][0]
    # La serie est allee au bout malgre l'echec, et le rappel a suivi.
    assert moteur.statut_fiche("P020", racine) == "en_attente"
    assert progression[0] == (0, NB_PRODUITS_FIXTURE)
    assert progression[-1] == (NB_PRODUITS_FIXTURE, NB_PRODUITS_FIXTURE)


# ---------------------------------------------------------------------------
# API cockpit : couverture, generation par produit, masse + progression
# ---------------------------------------------------------------------------


@pytest.fixture()
def cockpit(tmp_path, monkeypatch):
    """Serveur cockpit sur un port libre, racine d'ecriture isolee."""
    monkeypatch.setenv("COCKPIT_RACINE", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cockpit_serveur.reinitialiser_progression_fiches()
    serveur = cockpit_serveur.creer_serveur(0)
    thread = threading.Thread(target=serveur.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "url": f"http://127.0.0.1:{serveur.server_address[1]}",
            "racine": str(tmp_path),
        }
    finally:
        serveur.shutdown()
        serveur.server_close()
        cockpit_serveur.reinitialiser_progression_fiches()


def appel(cockpit, chemin, methode="GET", corps=None):
    requete = urllib.request.Request(
        cockpit["url"] + chemin,
        method=methode,
        data=json.dumps(corps).encode("utf-8") if corps is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(requete, timeout=10) as reponse:
            return reponse.status, json.loads(reponse.read().decode("utf-8"))
    except urllib.error.HTTPError as erreur:
        return erreur.code, json.loads(erreur.read().decode("utf-8"))


def test_api_couverture(cockpit):
    code, donnees = appel(cockpit, "/api/fiches/couverture")
    assert code == 200
    assert donnees["nb_produits"] == NB_PRODUITS_FIXTURE
    assert donnees["nb_couverts"] == 0
    assert donnees["nb_manquants"] == NB_PRODUITS_FIXTURE
    assert len(donnees["produits"]) == NB_PRODUITS_FIXTURE
    assert all(p["statut"] == "aucune" for p in donnees["produits"])


def test_api_generer_par_produit(cockpit):
    code, donnees = appel(
        cockpit, "/api/fiches/generer", "POST", {"produit_id": "P007"}
    )
    assert code == 200
    assert donnees["resultats"][0]["statut"] == "en_attente"
    assert os.path.exists(
        os.path.join(cockpit["racine"], "a_valider", "P007.md")
    )

    # Doublon refuse (409), id inconnu refuse (404).
    code, donnees = appel(
        cockpit, "/api/fiches/generer", "POST", {"produit_id": "P007"}
    )
    assert code == 409
    assert "existe deja" in donnees["erreur"]
    code, donnees = appel(
        cockpit, "/api/fiches/generer", "POST", {"produit_id": "P999"}
    )
    assert code == 404

    code, donnees = appel(cockpit, "/api/fiches/couverture")
    assert donnees["nb_couverts"] == 1


def test_api_generer_tous_progression_et_verrou(cockpit, monkeypatch):
    """Generation de masse bouchonnee : progression, 409, bilan, notification."""
    feu_vert = threading.Event()

    def fausse_generation(racine, inclure_rejetees=False, rappel=None):
        rappel(0, 2, {"id_produit": "P001", "nom_produit": "Produit bouchon"})
        assert feu_vert.wait(timeout=10), "le test n'a jamais donne le feu vert"
        rappel(2, 2, None)
        return {"generees": 2, "echouees": 0, "ignorees": 14, "resultats": []}

    monkeypatch.setattr(
        cockpit_serveur.moteur_fiches, "generer_manquantes", fausse_generation
    )

    code, donnees = appel(cockpit, "/api/fiches/generer", "POST", {"tous": True})
    assert code == 200
    assert donnees["demarre"] is True

    # La progression expose le produit en cours pendant la generation.
    for _ in range(50):
        code, prog = appel(cockpit, "/api/fiches/progression")
        if prog["en_cours"] and prog["produit_courant"]:
            break
        time.sleep(0.05)
    assert prog["en_cours"] is True
    assert prog["total"] == 2
    assert prog["produit_courant"] == "Produit bouchon"
    assert prog["terminee"] is False

    # Deja en cours : nouvelle generation de masse refusee.
    code, refus = appel(cockpit, "/api/fiches/generer", "POST", {"tous": True})
    assert code == 409
    assert "deja en cours" in refus["erreur"]

    # Fin de la generation : bilan disponible, verrou libere.
    feu_vert.set()
    for _ in range(100):
        code, prog = appel(cockpit, "/api/fiches/progression")
        if prog["terminee"]:
            break
        time.sleep(0.05)
    assert prog["terminee"] is True
    assert prog["en_cours"] is False
    assert prog["faites"] == 2
    assert prog["bilan"] == {"generees": 2, "echouees": 0, "ignorees": 14}

    # La cloche recoit l'evenement de fin de generation.
    code, notifs = appel(cockpit, "/api/notifications")
    assert code == 200
    evenements = [n for n in notifs["notifications"] if n["type"] == "fiches"]
    assert evenements, "aucun evenement fiches dans les notifications"
    assert evenements[0]["generees"] == 2
    assert evenements[0]["echouees"] == 0

    # Et une nouvelle generation redevient possible (elle finit aussitot :
    # le feu vert est deja donne), puis on attend sa fin pour l'isolation.
    code, donnees = appel(cockpit, "/api/fiches/generer", "POST", {"tous": True})
    assert code == 200
    for _ in range(100):
        code, prog = appel(cockpit, "/api/fiches/progression")
        if prog["terminee"] and not prog["en_cours"]:
            break
        time.sleep(0.05)
    assert prog["terminee"] is True
