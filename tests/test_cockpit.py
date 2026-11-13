"""Tests du cockpit SupplySense : serveur demarre en thread sur un port libre.

Le serveur tourne sur la base fixture (TEST_MODE=1 pose par conftest) et
ecrit uniquement dans une racine temporaire (COCKPIT_RACINE) : outbox/,
a_valider/, validees/, rejetees/, docs/ et les sorties historiques des
modules (rapport.md, journal_qa.jsonl). Aucun appel reseau externe.
"""

from __future__ import annotations

import http.client
import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from cockpit import serveur as cockpit_serveur
from lib import base


@pytest.fixture(scope="module")
def cockpit(tmp_path_factory):
    """Demarre le serveur sur un port libre avec une racine d'ecriture isolee."""
    racine = tmp_path_factory.mktemp("cockpit_racine")
    anciens = {
        cle: os.environ.get(cle)
        for cle in ("COCKPIT_RACINE", "AGENT_QA_JOURNAL", "ALERTE_WEBHOOK_URL")
    }
    os.environ["COCKPIT_RACINE"] = str(racine)
    os.environ["AGENT_QA_JOURNAL"] = str(racine / "journal_qa.jsonl")
    os.environ.pop("ALERTE_WEBHOOK_URL", None)

    serveur = cockpit_serveur.creer_serveur(0)
    thread = threading.Thread(target=serveur.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "url": f"http://127.0.0.1:{serveur.server_address[1]}",
            "racine": str(racine),
        }
    finally:
        serveur.shutdown()
        serveur.server_close()
        for cle, valeur in anciens.items():
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur


def appel(cockpit, chemin, methode="GET", corps=None):
    """Appelle l'API et retourne (code HTTP, objet JSON ou texte)."""
    requete = urllib.request.Request(
        cockpit["url"] + chemin,
        method=methode,
        data=json.dumps(corps).encode("utf-8") if corps is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(requete, timeout=10) as reponse:
            brut = reponse.read().decode("utf-8")
            code = reponse.status
    except urllib.error.HTTPError as erreur:
        brut = erreur.read().decode("utf-8")
        code = erreur.code
    try:
        return code, json.loads(brut)
    except json.JSONDecodeError:
        return code, brut


# ---------------------------------------------------------------------------
# Etat et statique
# ---------------------------------------------------------------------------


def test_etat(cockpit):
    code, donnees = appel(cockpit, "/api/etat")
    assert code == 200
    assert donnees["ok"] is True
    assert donnees["mode_test"] is True
    assert donnees["lecture_seule"] is True
    assert sorted(donnees["marts"]) == sorted(base.MARTS_AUTORISES)


def test_page_accueil(cockpit):
    code, texte = appel(cockpit, "/")
    assert code == 200
    assert "SupplySense" in texte


def test_route_api_inconnue(cockpit):
    code, donnees = appel(cockpit, "/api/inexistant")
    assert code == 404
    assert donnees["ok"] is False


def test_traversee_de_chemin_refusee(cockpit):
    """Le serveur statique ne sort jamais de cockpit/public/."""
    hote, port = cockpit["url"].replace("http://", "").split(":")
    for chemin in ("/../lib/base.py", "/..%2f..%2flib%2fbase.py", "/%2e%2e/serveur.py"):
        connexion = http.client.HTTPConnection(hote, int(port), timeout=10)
        connexion.request("GET", chemin)
        reponse = connexion.getresponse()
        corps = reponse.read().decode("utf-8", errors="replace")
        connexion.close()
        assert reponse.status == 404, chemin
        assert "MARTS_AUTORISES" not in corps


# ---------------------------------------------------------------------------
# KPI du dashboard
# ---------------------------------------------------------------------------


def test_kpis_structure_et_valeurs(cockpit):
    code, donnees = appel(cockpit, "/api/kpis")
    assert code == 200
    kpis = donnees["kpis"]

    lignes = base.requete(
        "SELECT SUM(nb_observations) AS obs, SUM(nb_ruptures) AS rup "
        "FROM supply_taux_rupture"
    )
    attendu = round(100.0 * int(lignes[0]["rup"]) / int(lignes[0]["obs"]), 2)
    assert kpis["taux_rupture_global_pct"] == pytest.approx(attendu)

    nb_ruptures = base.requete(
        "SELECT COUNT(*) AS nb FROM supply_couverture_stock "
        "WHERE statut_couverture = 'rupture'"
    )[0]["nb"]
    assert kpis["nb_ruptures"] == int(nb_ruptures)

    nb_dormants = base.requete(
        "SELECT COUNT(*) AS nb FROM supply_couverture_stock "
        "WHERE statut_couverture = 'dormant'"
    )[0]["nb"]
    assert kpis["nb_dormants"] == int(nb_dormants)

    assert 0 < kpis["otif_moyen_pct"] <= 100
    assert 0 < kpis["marge_moyenne_pct"] <= 100
    assert kpis["couverture_moyenne_jours"] > 0

    series = donnees["series"]
    assert len(series["rupture_par_entrepot"]) >= 2
    assert len(series["marge_par_famille"]) >= 3
    assert len(series["rotation_par_famille"]) == len(series["marge_par_famille"])
    statuts = {s["statut"] for s in series["statuts_couverture"]}
    assert "rupture" in statuts and "sain" in statuts
    assert len(donnees["dormants"]) == int(nb_dormants)
    assert donnees["pires_rotations"][0]["famille"] == "Objets d art"


# ---------------------------------------------------------------------------
# Alertes rupture
# ---------------------------------------------------------------------------


def test_ruptures_liste_et_reappro(cockpit):
    code, donnees = appel(cockpit, "/api/ruptures")
    assert code == 200
    assert donnees["ruptures"], "la fixture contient des ruptures"
    for produit in donnees["ruptures"]:
        assert produit["statut_couverture"] == "rupture"
        assert produit["quantite_reappro"] == round(
            float(produit["vente_moyenne_jour"]) * 30
        )


def test_envoyer_alerte_ecrit_outbox(cockpit):
    code, donnees = appel(cockpit, "/api/ruptures/envoyer", "POST", {})
    assert code == 200
    assert donnees["nb_ruptures"] > 0
    chemin = donnees["fichier_outbox"]
    assert chemin.startswith(os.path.join(cockpit["racine"], "outbox"))
    assert os.path.exists(chemin)
    with open(chemin, encoding="utf-8") as fichier:
        contenu = fichier.read()
    assert "Alerte rupture de stock" in contenu


# ---------------------------------------------------------------------------
# Assistant Q&A
# ---------------------------------------------------------------------------


def test_qa_pire_rotation(cockpit):
    code, donnees = appel(
        cockpit, "/api/qa", "POST", {"question": "quelle famille a la pire rotation ?"}
    )
    assert code == 200
    assert donnees["mode"] == "repli"
    assert "Objets d art" in donnees["reponse"]
    assert donnees["sql"].lower().startswith("select")
    assert donnees["duree_ms"] >= 0
    assert donnees["lignes"][0]["famille"] == "Objets d art"


def test_qa_question_vide(cockpit):
    code, donnees = appel(cockpit, "/api/qa", "POST", {"question": "   "})
    assert code == 400


def test_qa_question_non_reconnue(cockpit):
    code, donnees = appel(
        cockpit, "/api/qa", "POST", {"question": "quelle est la meteo a Lille ?"}
    )
    assert code == 400
    assert donnees["ok"] is False
    assert "non reconnue" in donnees["erreur"]


def test_qa_journal(cockpit):
    code, donnees = appel(cockpit, "/api/qa/journal")
    assert code == 200
    questions = [entree["question"] for entree in donnees["journal"]]
    assert "quelle famille a la pire rotation ?" in questions
    assert donnees["journal"][0]["statut"] in ("ok", "erreur")


# ---------------------------------------------------------------------------
# Rapport hebdo
# ---------------------------------------------------------------------------


def test_rapport_kpis(cockpit):
    code, donnees = appel(cockpit, "/api/rapport")
    assert code == 200
    assert donnees["kpi"]["rupture"]["taux_global_pct"] > 0
    assert len(donnees["puces"]) == 3
    assert donnees["lien_html"] == "rapport-hebdo.html"


def test_rapport_generer(cockpit):
    code, donnees = appel(cockpit, "/api/rapport/generer", "POST", {})
    assert code == 200
    assert os.path.exists(donnees["html"])
    assert donnees["html"] == os.path.join(
        cockpit["racine"], "docs", "rapport-hebdo.html"
    )
    assert os.path.exists(donnees["markdown"])

    code, texte = appel(cockpit, "/rapport-hebdo.html")
    assert code == 200
    assert "Rapport hebdomadaire" in texte


# ---------------------------------------------------------------------------
# Fiches produits : generer -> approuver -> rejeter
# ---------------------------------------------------------------------------


def test_fiches_flux_complet(cockpit):
    code, donnees = appel(cockpit, "/api/fiches/generer", "POST", {"limite": 2})
    assert code == 200
    deposees = [r for r in donnees["resultats"] if r["statut"] == "en_attente"]
    assert len(deposees) == 2

    code, listes = appel(cockpit, "/api/fiches")
    assert code == 200
    ids = [f["id_produit"] for f in listes["en_attente"]]
    assert len(ids) == 2
    for fiche in listes["en_attente"]:
        assert fiche["titre"]
        assert len(fiche["points_forts"]) == 3

    # Approbation humaine de la premiere fiche.
    code, approbation = appel(
        cockpit, f"/api/fiches/{ids[0]}/approuver", "POST", {}
    )
    assert code == 200
    assert approbation["statut"] == "validee"
    assert os.path.exists(
        os.path.join(cockpit["racine"], "validees", f"{ids[0]}.md")
    )

    # Rejet motive de la seconde.
    code, rejet = appel(
        cockpit,
        f"/api/fiches/{ids[1]}/rejeter",
        "POST",
        {"motif": "description trop generique"},
    )
    assert code == 200
    assert rejet["statut"] == "rejetee"
    chemin_rejetee = os.path.join(cockpit["racine"], "rejetees", f"{ids[1]}.md")
    assert os.path.exists(chemin_rejetee)
    with open(chemin_rejetee, encoding="utf-8") as fichier:
        contenu = fichier.read()
    assert "motif_rejet: description trop generique" in contenu

    code, listes = appel(cockpit, "/api/fiches")
    assert listes["en_attente"] == []
    assert [f["id_produit"] for f in listes["validees"]] == [ids[0]]
    assert [f["id_produit"] for f in listes["rejetees"]] == [ids[1]]
    assert listes["rejetees"][0]["motif_rejet"] == "description trop generique"


def test_fiches_approuver_inconnue(cockpit):
    code, donnees = appel(cockpit, "/api/fiches/INCONNU/approuver", "POST", {})
    assert code == 404


def test_fiches_rejeter_sans_motif(cockpit):
    code, donnees = appel(cockpit, "/api/fiches/INCONNU/rejeter", "POST", {})
    assert code == 400
    assert "motif" in donnees["erreur"]


def test_fiches_limite_invalide(cockpit):
    code, donnees = appel(cockpit, "/api/fiches/generer", "POST", {"limite": 999})
    assert code == 400


# ---------------------------------------------------------------------------
# Sante des donnees (gardien sur ses deux fixtures)
# ---------------------------------------------------------------------------


def test_sante_deux_scenarios(cockpit):
    code, donnees = appel(cockpit, "/api/sante")
    assert code == 200
    vert = donnees["scenarios"]["vert"]
    casse = donnees["scenarios"]["casse"]

    assert vert["statut_global"] == "ok"
    assert vert["nb_echecs"] == 0
    assert vert["diagnostics"] == []

    assert casse["statut_global"] == "echec"
    assert casse["nb_echecs"] == 3
    assert casse["nb_bloquants"] >= 1
    for diagnostic in casse["diagnostics"]:
        assert diagnostic["gravite"] in ("bloquant", "avertissement")
        assert diagnostic["categorie"]
        assert diagnostic["action_proposee"]


# ---------------------------------------------------------------------------
# Garde d'ecriture : rien hors des dossiers autorises
# ---------------------------------------------------------------------------


def test_ecritures_limitees_aux_dossiers_autorises(cockpit):
    """Apres tout le flux, la racine ne contient que les sorties attendues."""
    autorises = {
        "outbox",
        "a_valider",
        "validees",
        "rejetees",
        "docs",
        "rapport.md",
        "journal_qa.jsonl",
    }
    contenu = set(os.listdir(cockpit["racine"]))
    assert contenu <= autorises, f"ecritures inattendues : {contenu - autorises}"
    # Et la base fixture n'a jamais ete modifiee : toujours lisible en lecture seule.
    assert base.requete("SELECT COUNT(*) AS nb FROM supply_couverture_stock")[0]["nb"] > 0
