"""Tests de la vue Pipeline du cockpit : etat du build dbt et rafraichissement.

Aucun dbt build reel : le repo dbt est un dossier temporaire ou l'on pose
etat_build.json a la main, et executer_build est bouchonne pour verifier
le lancement en thread et le refus (409) quand le verrou est present.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from cockpit import serveur as cockpit_serveur
from pipeline import moteur as moteur_pipeline


@pytest.fixture(scope="module")
def cockpit(tmp_path_factory):
    """Serveur cockpit sur un port libre, repo dbt et outbox isoles."""
    racine = tmp_path_factory.mktemp("cockpit_racine")
    dossier_dbt = tmp_path_factory.mktemp("repo_dbt")
    dossier_outbox = tmp_path_factory.mktemp("outbox_pipeline")
    anciens = {
        cle: os.environ.get(cle)
        for cle in ("COCKPIT_RACINE", "DBT_DIR", "PIPELINE_OUTBOX")
    }
    os.environ["COCKPIT_RACINE"] = str(racine)
    os.environ["DBT_DIR"] = str(dossier_dbt)
    os.environ["PIPELINE_OUTBOX"] = str(dossier_outbox)

    serveur = cockpit_serveur.creer_serveur(0)
    thread = threading.Thread(target=serveur.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "url": f"http://127.0.0.1:{serveur.server_address[1]}",
            "dbt": str(dossier_dbt),
            "outbox": str(dossier_outbox),
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
    """Appelle l'API et retourne (code HTTP, objet JSON)."""
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
    return code, json.loads(brut)


def ecrire_etat(cockpit, etat):
    with open(
        os.path.join(cockpit["dbt"], "etat_build.json"), "w", encoding="utf-8"
    ) as fichier:
        json.dump(etat, fichier, ensure_ascii=False)


def etat_exemple(statut="succes", **surcharges):
    etat = {
        "horodatage": "2026-11-13T07:30:00",
        "statut": statut,
        "pass": 151,
        "fail": 0,
        "total": 151,
        "duree_s": 42.5,
        "declencheur": "nocturne",
        "historique": [
            {
                "horodatage": "2026-11-13T07:30:00",
                "statut": statut,
                "pass": 151,
                "fail": 0,
                "total": 151,
                "duree_s": 42.5,
                "declencheur": "nocturne",
            },
            {
                "horodatage": "2026-11-12T07:30:00",
                "statut": "echec",
                "pass": 149,
                "fail": 2,
                "total": 151,
                "duree_s": 40.1,
                "declencheur": "manuel",
            },
        ],
    }
    etat.update(surcharges)
    return etat


DIAGNOSTIC_MARKDOWN = """# Diagnostic qualite des donnees

Genere le : 2026-11-13T07:31:00
Source : /tmp/run_results.json
Tests examines : 151
Echecs : 1 (dont bloquants : 1)

Chaque diagnostic ci-dessous attend une validation humaine avant toute action corrective.

## 1. not_null_stg_ventes_id_produit

- Gravite : **bloquant**
- Categorie : valeur manquante
- Mart concerne : stg_ventes
- Ce qui a casse : Le test a echoue : 3 ligne(s) en violation.
- Hypothese de cause : Une saisie incomplete dans les ventes.
- Action proposee : Verifier la saisie des ventes et completer l'identifiant produit.
"""


# ---------------------------------------------------------------------------
# GET /api/pipeline
# ---------------------------------------------------------------------------


def test_pipeline_jamais_construit(cockpit):
    code, donnees = appel(cockpit, "/api/pipeline")
    assert code == 200
    assert donnees["ok"] is True
    assert donnees["etat"]["statut"] == "jamais_construit"
    assert donnees["etat"]["historique"] == []
    assert donnees["en_cours"] is False
    assert donnees["diagnostics"] == []
    assert donnees["outbox"] == cockpit["outbox"]


def test_pipeline_etat_succes(cockpit):
    ecrire_etat(cockpit, etat_exemple("succes"))
    code, donnees = appel(cockpit, "/api/pipeline")
    assert code == 200
    etat = donnees["etat"]
    assert etat["statut"] == "succes"
    assert etat["pass"] == 151
    assert etat["fail"] == 0
    assert etat["total"] == 151
    assert etat["duree_s"] == 42.5
    assert etat["declencheur"] == "nocturne"
    assert len(etat["historique"]) == 2
    assert donnees["en_cours"] is False
    assert donnees["diagnostics"] == []


def test_pipeline_etat_en_cours(cockpit):
    ecrire_etat(cockpit, etat_exemple("en_cours") | {"pass": 0, "total": 0})
    code, donnees = appel(cockpit, "/api/pipeline")
    assert code == 200
    assert donnees["etat"]["statut"] == "en_cours"
    assert donnees["en_cours"] is True


def test_pipeline_echec_remonte_les_3_derniers_diagnostics(cockpit):
    ecrire_etat(cockpit, etat_exemple("echec", fail=2))
    for heure in ("070000", "071000", "072000", "073000"):
        chemin = os.path.join(
            cockpit["outbox"], f"diagnostic-20261113-{heure}.md"
        )
        with open(chemin, "w", encoding="utf-8") as fichier:
            fichier.write(DIAGNOSTIC_MARKDOWN)

    code, donnees = appel(cockpit, "/api/pipeline")
    assert code == 200
    diagnostics = donnees["diagnostics"]
    assert len(diagnostics) == 3, "limite aux 3 derniers diagnostics"
    assert diagnostics[0]["fichier"] == "diagnostic-20261113-073000.md"
    assert diagnostics[-1]["fichier"] == "diagnostic-20261113-071000.md"
    premier = diagnostics[0]
    assert premier["genere_le"] == "2026-11-13T07:31:00"
    assert len(premier["tests"]) == 1
    test = premier["tests"][0]
    assert test["nom_test"] == "not_null_stg_ventes_id_produit"
    assert test["gravite"] == "bloquant"
    assert test["categorie"] == "valeur manquante"
    assert test["action_proposee"].startswith("Verifier la saisie des ventes")


# ---------------------------------------------------------------------------
# POST /api/pipeline/rafraichir
# ---------------------------------------------------------------------------


def test_rafraichir_lance_le_build_en_thread(cockpit, monkeypatch):
    appels = {}
    demarre = threading.Event()
    terminer = threading.Event()

    def faux_build(declencheur="manuel"):
        appels["declencheur"] = declencheur
        appels["thread"] = threading.get_ident()
        demarre.set()
        terminer.wait(5)
        return 0

    monkeypatch.setattr(moteur_pipeline, "executer_build", faux_build)

    code, donnees = appel(cockpit, "/api/pipeline/rafraichir", "POST", {})
    # La reponse arrive tout de suite, pendant que le build tourne encore.
    assert code == 200
    assert donnees == {"ok": True, "demarre": True}
    assert demarre.wait(5), "executer_build n'a pas ete lance"
    assert not terminer.is_set()
    terminer.set()

    assert appels["declencheur"] == "supplysense"
    assert appels["thread"] != threading.get_ident(), "le build doit tourner dans un thread"


def test_rafraichir_409_si_verrou_present(cockpit, monkeypatch):
    def build_interdit(declencheur="manuel"):
        raise AssertionError("executer_build ne doit pas etre appele sous verrou")

    monkeypatch.setattr(moteur_pipeline, "executer_build", build_interdit)

    verrou = moteur_pipeline.chemin_verrou()
    assert verrou.startswith(cockpit["dbt"])
    with open(verrou, "w", encoding="utf-8") as fichier:
        fichier.write("{}")
    try:
        code, donnees = appel(cockpit, "/api/pipeline/rafraichir", "POST", {})
        assert code == 409
        assert donnees["ok"] is False
        assert donnees["demarre"] is False
        assert "verrou" in donnees["erreur"]

        # Et la vue sait qu'un build est en cours tant que le verrou existe.
        code, donnees = appel(cockpit, "/api/pipeline")
        assert code == 200
        assert donnees["en_cours"] is True
    finally:
        os.remove(verrou)
