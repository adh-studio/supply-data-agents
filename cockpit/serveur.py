"""Serveur du cockpit SupplySense : stdlib uniquement (http.server + json).

Philosophie LeadSense : aucune dependance en plus de celles du repo.
Toutes les lectures de donnees passent par lib.base.requete (lecture seule,
SELECT uniquement, allowlist des 5 marts). Les ecritures du serveur sont
limitees aux dossiers outbox/, a_valider/, validees/, rejetees/ et au
fichier docs/rapport-hebdo.html (plus rapport.md et journal_qa.jsonl,
sorties historiques des modules reutilises). La vue Pipeline delegue au
module pipeline (etat_build.json et verrou vivent dans le repo dbt).
La base n'est JAMAIS ecrite par le cockpit.

API REST JSON :
  GET  /api/etat                      etat du cockpit (ping sidecard)
  GET  /api/kpis                      KPI globaux + series pour graphiques
  GET  /api/ruptures                  liste alerte_rupture + reappro suggere
  POST /api/ruptures/envoyer          declenche l'alerte (outbox)
  POST /api/qa {question}             assistant Q&A (mode repli sans cle)
  GET  /api/qa/journal                dernieres entrees du journal d'audit
  GET  /api/rapport                   KPI du rapport hebdo + lien HTML
  POST /api/rapport/generer           regenere rapport.md + docs/*.html
  GET  /api/fiches                    fiches en attente / validees / rejetees
  POST /api/fiches/generer {limite}   genere des brouillons de fiches
  POST /api/fiches/<id>/approuver     geste humain : approbation
  POST /api/fiches/<id>/rejeter       geste humain : rejet motive
  GET  /api/sante                     gardien_qualite sur ses deux fixtures
  GET  /api/pipeline                  etat du dernier build dbt + diagnostics
  POST /api/pipeline/rafraichir       build dbt en arriere-plan (409 si verrou)
  GET  /api/notifications             publications SupplyOps + saisies en attente
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from alerte_rupture import moteur as moteur_alerte
from agent_qa import moteur as moteur_qa
from fiches_produits import moteur as moteur_fiches
from gardien_qualite import moteur as moteur_gardien
from lib import base
from pipeline import moteur as moteur_pipeline
from rapport_hebdo import moteur as moteur_rapport

PORT_DEFAUT = 4300

DOSSIER_COCKPIT = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PUBLIC = os.path.join(DOSSIER_COCKPIT, "public")

FIXTURE_NUIT_VERTE = os.path.join(
    base.RACINE_PROJET, "gardien_qualite", "fixtures", "run_results_vert.json"
)
FIXTURE_NUIT_CASSEE = os.path.join(
    base.RACINE_PROJET, "gardien_qualite", "fixtures", "run_results_casse.json"
)

MIME = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "text/javascript",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

_REGEX_FICHE = re.compile(r"^/api/fiches/([A-Za-z0-9_-]+)/(approuver|rejeter)$")


def racine() -> str:
    """Racine des ecritures (outbox/, fiches, docs/), surchargable en test."""
    return os.environ.get("COCKPIT_RACINE", base.RACINE_PROJET)


# ---------------------------------------------------------------------------
# Calculs des vues (toutes les lectures passent par base.requete)
# ---------------------------------------------------------------------------

def calculer_kpis() -> dict:
    """KPI globaux et series pour les graphiques du dashboard."""
    rupture = base.requete(
        "SELECT nom_entrepot, SUM(nb_observations) AS nb_observations, "
        "SUM(nb_ruptures) AS nb_ruptures "
        "FROM supply_taux_rupture GROUP BY nom_entrepot ORDER BY nom_entrepot"
    )
    total_obs = sum(int(l["nb_observations"]) for l in rupture)
    total_rup = sum(int(l["nb_ruptures"]) for l in rupture)
    rupture_par_entrepot = [
        {
            "entrepot": l["nom_entrepot"],
            "nb_observations": int(l["nb_observations"]),
            "nb_ruptures": int(l["nb_ruptures"]),
            "taux_pct": round(
                100.0 * int(l["nb_ruptures"]) / int(l["nb_observations"]), 2
            )
            if int(l["nb_observations"])
            else 0.0,
        }
        for l in rupture
    ]

    familles = base.requete(
        "SELECT famille, ROUND(taux_marge * 100, 1) AS taux_marge_pct, "
        "rotation_periode, chiffre_affaires "
        "FROM offre_marge_familles ORDER BY taux_marge DESC, famille"
    )
    marge_moyenne = (
        round(sum(float(l["taux_marge_pct"]) for l in familles) / len(familles), 1)
        if familles
        else 0.0
    )

    statuts = base.requete(
        "SELECT statut_couverture, COUNT(*) AS nb "
        "FROM supply_couverture_stock GROUP BY statut_couverture "
        "ORDER BY statut_couverture"
    )
    nb_par_statut = {l["statut_couverture"]: int(l["nb"]) for l in statuts}

    couverture = base.requete(
        "SELECT ROUND(AVG(couverture_jours), 1) AS moyenne, "
        "ROUND(MEDIAN(couverture_jours), 1) AS mediane "
        "FROM supply_couverture_stock WHERE couverture_jours IS NOT NULL"
    )
    otif = base.requete(
        "SELECT ROUND(AVG(taux_service_otif) * 100, 1) AS moyen "
        "FROM supply_delais_fournisseurs"
    )
    dormants = base.requete(
        "SELECT nom_produit, famille, nom_entrepot, quantite_disponible "
        "FROM supply_couverture_stock WHERE statut_couverture = 'dormant' "
        "ORDER BY quantite_disponible DESC"
    )
    pires_rotations = [
        {
            "famille": l["famille"],
            "rotation_periode": round(float(l["rotation_periode"]), 2),
        }
        for l in sorted(familles, key=lambda x: float(x["rotation_periode"]))[:5]
    ]

    return {
        "kpis": {
            "taux_rupture_global_pct": round(100.0 * total_rup / total_obs, 2)
            if total_obs
            else 0.0,
            "couverture_moyenne_jours": float(couverture[0]["moyenne"] or 0.0),
            "couverture_mediane_jours": float(couverture[0]["mediane"] or 0.0),
            "otif_moyen_pct": float(otif[0]["moyen"] or 0.0),
            "marge_moyenne_pct": marge_moyenne,
            "nb_ruptures": nb_par_statut.get("rupture", 0),
            "nb_dormants": nb_par_statut.get("dormant", 0),
        },
        "series": {
            "rupture_par_entrepot": rupture_par_entrepot,
            "marge_par_famille": [
                {
                    "famille": l["famille"],
                    "taux_marge_pct": float(l["taux_marge_pct"]),
                    "chiffre_affaires": round(float(l["chiffre_affaires"]), 2),
                }
                for l in familles
            ],
            "rotation_par_famille": [
                {
                    "famille": l["famille"],
                    "rotation_periode": round(float(l["rotation_periode"]), 2),
                }
                for l in familles
            ],
            "statuts_couverture": [
                {"statut": l["statut_couverture"], "nb": int(l["nb"])}
                for l in statuts
            ],
        },
        "dormants": dormants,
        "pires_rotations": pires_rotations,
    }


def envoyer_alerte() -> dict:
    """Declenche l'alerte rupture : message + depot outbox, chemin retourne."""
    produits = moteur_alerte.recuperer_produits()
    message = moteur_alerte.construire_message(produits)
    dossier = os.path.join(racine(), "outbox")
    chemin = moteur_alerte.ecrire_outbox(message, dossier=dossier)
    return {
        "fichier_outbox": chemin,
        "nb_ruptures": len(produits),
        "message": message,
    }


def _resume_fiche(chemin: str) -> dict:
    """Resume une fiche markdown : en-tete YAML + titre, description, points."""
    entete, corps = moteur_fiches.lire_fiche(chemin)
    titre = ""
    description = ""
    points: list[str] = []
    section_points = False
    for ligne in corps.splitlines():
        ligne = ligne.strip()
        if ligne.startswith("# ") and not titre:
            titre = ligne[2:].strip()
        elif ligne.startswith("## "):
            section_points = "points forts" in ligne.lower()
        elif ligne.startswith("- ") and section_points:
            points.append(ligne[2:].strip())
        elif ligne and not description and titre and not ligne.startswith("#"):
            description = ligne
    return {
        "id_produit": entete.get("id_produit"),
        "nom_produit": entete.get("nom_produit"),
        "statut": entete.get("statut"),
        "mode": entete.get("mode"),
        "genere_le": entete.get("genere_le"),
        "approuvee_le": entete.get("approuvee_le"),
        "rejetee_le": entete.get("rejetee_le"),
        "motif_rejet": entete.get("motif_rejet"),
        "titre": titre,
        "description": description,
        "points_forts": points,
    }


def lister_fiches() -> dict:
    """Fiches par zone : a_valider/, validees/, rejetees/."""
    zones = {
        "en_attente": moteur_fiches.NOM_DOSSIER_A_VALIDER,
        "validees": moteur_fiches.NOM_DOSSIER_VALIDEES,
        "rejetees": moteur_fiches.NOM_DOSSIER_REJETEES,
    }
    resultat: dict = {}
    for cle, nom_dossier in zones.items():
        dossier = os.path.join(racine(), nom_dossier)
        fiches = []
        if os.path.isdir(dossier):
            for nom in sorted(os.listdir(dossier)):
                if nom.endswith(".md"):
                    try:
                        fiches.append(_resume_fiche(os.path.join(dossier, nom)))
                    except (ValueError, OSError):
                        continue
        resultat[cle] = fiches
    return resultat


def lire_journal_qa(limite: int = 20) -> list[dict]:
    """Dernieres entrees du journal d'audit de l'agent Q&A."""
    chemin = moteur_qa.chemin_journal()
    if not os.path.exists(chemin):
        return []
    entrees: list[dict] = []
    with open(chemin, encoding="utf-8") as fichier:
        for ligne in fichier:
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                entrees.append(json.loads(ligne))
            except json.JSONDecodeError:
                continue
    return list(reversed(entrees[-limite:]))


def analyser_sante() -> dict:
    """Execute le gardien de qualite sur ses deux fixtures livrees."""
    chemin_base_lecture = base.chemin_base()
    if not os.path.exists(chemin_base_lecture):
        chemin_base_lecture = None
    return {
        "scenarios": {
            "vert": moteur_gardien.analyser(
                FIXTURE_NUIT_VERTE, chemin_base=chemin_base_lecture
            ),
            "casse": moteur_gardien.analyser(
                FIXTURE_NUIT_CASSEE, chemin_base=chemin_base_lecture
            ),
        }
    }


_CLES_DIAGNOSTIC = {
    "gravite": "gravite",
    "categorie": "categorie",
    "action proposee": "action_proposee",
    "ce qui a casse": "ce_qui_a_casse",
}


def _resume_diagnostic(chemin: str) -> dict:
    """Resume un diagnostic markdown du gardien depose dans outbox/."""
    with open(chemin, encoding="utf-8") as fichier:
        contenu = fichier.read()
    genere_le = None
    tests: list[dict] = []
    courant: dict | None = None
    for ligne in contenu.splitlines():
        ligne = ligne.strip()
        if ligne.startswith("Genere le : ") and genere_le is None:
            genere_le = ligne.split(" : ", 1)[1].strip()
        elif ligne.startswith("## "):
            titre = ligne[3:].strip()
            if ". " in titre:
                titre = titre.split(". ", 1)[1]
            courant = {"nom_test": titre}
            tests.append(courant)
        elif courant is not None and ligne.startswith("- ") and " : " in ligne:
            cle, valeur = ligne[2:].split(" : ", 1)
            cle = cle.strip().lower()
            if cle in _CLES_DIAGNOSTIC:
                courant[_CLES_DIAGNOSTIC[cle]] = valeur.strip().strip("*")
    return {
        "fichier": os.path.basename(chemin),
        "chemin": chemin,
        "genere_le": genere_le,
        "tests": tests,
    }


def lister_diagnostics(limite: int = 3) -> list[dict]:
    """Les derniers diagnostics du gardien dans l'outbox du pipeline."""
    dossier = moteur_pipeline.dossier_outbox()
    if not os.path.isdir(dossier):
        return []
    noms = sorted(
        (
            nom
            for nom in os.listdir(dossier)
            if nom.startswith("diagnostic-") and nom.endswith(".md")
        ),
        reverse=True,
    )
    resultats: list[dict] = []
    for nom in noms[:limite]:
        try:
            resultats.append(_resume_diagnostic(os.path.join(dossier, nom)))
        except OSError:
            continue
    return resultats


def etat_pipeline() -> dict:
    """Etat partage du build dbt + diagnostics du gardien en cas d'echec."""
    etat = moteur_pipeline.lire_etat()
    en_cours = (
        etat.get("statut") == moteur_pipeline.STATUT_EN_COURS
        or os.path.exists(moteur_pipeline.chemin_verrou())
    )
    diagnostics = (
        lister_diagnostics()
        if etat.get("statut") == moteur_pipeline.STATUT_ECHEC
        else []
    )
    return {
        "etat": etat,
        "en_cours": en_cours,
        "outbox": moteur_pipeline.dossier_outbox(),
        "diagnostics": diagnostics,
    }


NB_NOTIFICATIONS_MAX = 30


def calculer_notifications(limite: int = NB_NOTIFICATIONS_MAX) -> dict:
    """Fusionne publications SupplyOps et saisies en attente, recent d'abord.

    Deux familles d'evenements, chacune avec un id stable derive de
    l'horodatage (le front s'en sert pour compter les non-lus) :
    - "publication" : les builds declenches par SupplyOps ou SupplySense
      dans l'historique de etat_build.json, avec les modifications
      publiees par le build et les phrases d'impact sur les KPI ;
    - "attente" : un evenement unique qui agrege les lignes publie=false
      de journal_ops.jsonl (les chiffres affiches ne les incluent pas).
    """
    evenements: list[dict] = []
    etat = moteur_pipeline.lire_etat()
    for entree in etat.get("historique") or []:
        if entree.get("declencheur") not in moteur_pipeline.DECLENCHEURS_PUBLICATION:
            continue
        horodatage = entree.get("horodatage") or ""
        evenements.append(
            {
                "id": "publication-" + horodatage,
                "type": "publication",
                "horodatage": horodatage,
                "statut": entree.get("statut"),
                "declencheur": entree.get("declencheur"),
                "pass": int(entree.get("pass") or 0),
                "fail": int(entree.get("fail") or 0),
                "total": int(entree.get("total") or 0),
                "modifications": list(entree.get("modifications") or []),
                "impact": list((entree.get("impact") or {}).get("resume") or []),
            }
        )

    en_attente = [
        ligne
        for ligne in moteur_pipeline.lire_journal_ops()
        if not ligne.get("publie")
    ]
    if en_attente:
        plus_recente = max(ligne.get("horodatage") or "" for ligne in en_attente)
        evenements.append(
            {
                "id": "attente-" + plus_recente,
                "type": "attente",
                "horodatage": plus_recente,
                "nb": len(en_attente),
                "resumes": [ligne.get("resume") or "" for ligne in en_attente],
            }
        )

    evenements.sort(key=lambda e: e.get("horodatage") or "", reverse=True)
    return {
        "notifications": evenements[:limite],
        "nb_en_attente": len(en_attente),
    }


def demarrer_build() -> tuple[int, dict]:
    """Lance executer_build("supplysense") dans un thread, 409 si verrou."""
    if os.path.exists(moteur_pipeline.chemin_verrou()):
        return 409, {
            "ok": False,
            "demarre": False,
            "erreur": (
                "Un build est deja en cours (verrou .pipeline.lock present). "
                "Nouveau build refuse."
            ),
        }
    thread = threading.Thread(
        target=moteur_pipeline.executer_build,
        args=("supplysense",),
        name="pipeline-build",
        daemon=True,
    )
    thread.start()
    return 200, {"ok": True, "demarre": True}


# ---------------------------------------------------------------------------
# Handler HTTP
# ---------------------------------------------------------------------------

class Cockpit(BaseHTTPRequestHandler):
    server_version = "SupplySense/1.0"

    # -- utilitaires ---------------------------------------------------------

    def log_message(self, format, *args):  # noqa: A002 - signature imposee
        if os.environ.get("COCKPIT_VERBOSE"):
            super().log_message(format, *args)

    def _json(self, code: int, objet: dict) -> None:
        corps = json.dumps(objet, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def _corps(self) -> dict:
        try:
            longueur = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            longueur = 0
        if longueur <= 0 or longueur > 1_000_000:
            return {}
        try:
            return json.loads(self.rfile.read(longueur).decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _fichier(self, chemin: str) -> None:
        extension = os.path.splitext(chemin)[1].lower()
        with open(chemin, "rb") as fichier:
            corps = fichier.read()
        self.send_response(200)
        self.send_header(
            "Content-Type", MIME.get(extension, "text/plain") + "; charset=utf-8"
        )
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    # -- GET -----------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - API http.server
        chemin = self.path.split("?", 1)[0]
        try:
            if chemin == "/api/etat":
                return self._json(
                    200,
                    {
                        "ok": True,
                        "application": "SupplySense",
                        "mode_test": os.environ.get("TEST_MODE") == "1",
                        "base": base.chemin_base(),
                        "lecture_seule": True,
                        "marts": sorted(base.MARTS_AUTORISES),
                    },
                )
            if chemin == "/api/kpis":
                return self._json(200, {"ok": True, **calculer_kpis()})
            if chemin == "/api/ruptures":
                return self._json(
                    200, {"ok": True, "ruptures": moteur_alerte.recuperer_produits()}
                )
            if chemin == "/api/qa/journal":
                return self._json(200, {"ok": True, "journal": lire_journal_qa()})
            if chemin == "/api/rapport":
                kpi = moteur_rapport.calculer_kpi()
                puces = moteur_rapport.generer_puces(kpi)
                chemin_html = os.path.join(racine(), "docs", "rapport-hebdo.html")
                return self._json(
                    200,
                    {
                        "ok": True,
                        "kpi": kpi,
                        "puces": puces,
                        "html_disponible": os.path.exists(chemin_html),
                        "lien_html": "rapport-hebdo.html",
                    },
                )
            if chemin == "/api/fiches":
                return self._json(200, {"ok": True, **lister_fiches()})
            if chemin == "/api/sante":
                return self._json(200, {"ok": True, **analyser_sante()})
            if chemin == "/api/pipeline":
                return self._json(200, {"ok": True, **etat_pipeline()})
            if chemin == "/api/notifications":
                return self._json(200, {"ok": True, **calculer_notifications()})
            if chemin == "/rapport-hebdo.html":
                chemin_html = os.path.join(racine(), "docs", "rapport-hebdo.html")
                if os.path.exists(chemin_html):
                    return self._fichier(chemin_html)
                return self._json(
                    404,
                    {"ok": False, "erreur": "Rapport non genere. POST /api/rapport/generer d'abord."},
                )
            if chemin.startswith("/api/"):
                return self._json(404, {"ok": False, "erreur": "route inconnue"})
            return self._statique(chemin)
        except base.RequeteInterditeErreur as erreur:
            return self._json(400, {"ok": False, "erreur": str(erreur)})
        except FileNotFoundError as erreur:
            return self._json(404, {"ok": False, "erreur": str(erreur)})
        except Exception as erreur:  # garde-fou : jamais de 500 silencieux
            return self._json(
                500, {"ok": False, "erreur": f"{type(erreur).__name__}: {erreur}"}
            )

    def _statique(self, chemin: str) -> None:
        """Sert cockpit/public/ avec protection contre la traversee de chemin."""
        if chemin == "/":
            chemin = "/index.html"
        cible = os.path.normpath(os.path.join(DOSSIER_PUBLIC, chemin.lstrip("/")))
        if not (
            cible.startswith(DOSSIER_PUBLIC + os.sep) or cible == DOSSIER_PUBLIC
        ):
            return self._json(404, {"ok": False, "erreur": "introuvable"})
        if os.path.isfile(cible):
            return self._fichier(cible)
        return self._json(404, {"ok": False, "erreur": "introuvable"})

    # -- POST ----------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802 - API http.server
        chemin = self.path.split("?", 1)[0]
        donnees = self._corps()
        try:
            if chemin == "/api/ruptures/envoyer":
                return self._json(200, {"ok": True, **envoyer_alerte()})

            if chemin == "/api/pipeline/rafraichir":
                code, reponse = demarrer_build()
                return self._json(code, reponse)

            if chemin == "/api/qa":
                question = (donnees.get("question") or "").strip()
                if not question:
                    return self._json(
                        400, {"ok": False, "erreur": "question manquante"}
                    )
                depart = time.perf_counter()
                try:
                    resultat = moteur_qa.repondre(question, utilisateur="cockpit")
                except moteur_qa.QuestionNonCompriseErreur as erreur:
                    return self._json(400, {"ok": False, "erreur": str(erreur)})
                except base.RequeteInterditeErreur as erreur:
                    return self._json(
                        400,
                        {"ok": False, "erreur": f"Requete refusee par la garde : {erreur}"},
                    )
                duree_ms = round((time.perf_counter() - depart) * 1000, 1)
                return self._json(
                    200,
                    {
                        "ok": True,
                        "reponse": resultat["reponse"],
                        "sql": resultat["sql"],
                        "lignes": resultat["lignes"],
                        "mode": resultat["mode"],
                        "duree_ms": duree_ms,
                    },
                )

            if chemin == "/api/rapport/generer":
                resultat = moteur_rapport.generer_rapport(dossier_sortie=racine())
                return self._json(
                    200,
                    {
                        "ok": True,
                        "markdown": resultat["markdown"],
                        "html": resultat["html"],
                        "kpi": resultat["kpi"],
                        "puces": resultat["puces"],
                        "lien_html": "rapport-hebdo.html",
                    },
                )

            if chemin == "/api/fiches/generer":
                limite = donnees.get("limite", 2)
                if not isinstance(limite, int) or not (1 <= limite <= 10):
                    return self._json(
                        400,
                        {"ok": False, "erreur": "limite invalide (entier entre 1 et 10)"},
                    )
                resultats = moteur_fiches.generer(limite=limite, racine=racine())
                return self._json(200, {"ok": True, "resultats": resultats})

            correspondance = _REGEX_FICHE.match(chemin)
            if correspondance:
                id_produit, action = correspondance.groups()
                if action == "approuver":
                    chemin_fiche = moteur_fiches.approuver(
                        id_produit, racine=racine()
                    )
                    return self._json(
                        200, {"ok": True, "statut": "validee", "chemin": chemin_fiche}
                    )
                motif = (donnees.get("motif") or "").strip()
                if not motif:
                    return self._json(
                        400, {"ok": False, "erreur": "motif de rejet obligatoire"}
                    )
                chemin_fiche = moteur_fiches.rejeter(
                    id_produit, motif, racine=racine()
                )
                return self._json(
                    200, {"ok": True, "statut": "rejetee", "chemin": chemin_fiche}
                )

            return self._json(404, {"ok": False, "erreur": "route inconnue"})
        except FileNotFoundError as erreur:
            return self._json(404, {"ok": False, "erreur": str(erreur)})
        except ValueError as erreur:
            return self._json(400, {"ok": False, "erreur": str(erreur)})
        except base.RequeteInterditeErreur as erreur:
            return self._json(400, {"ok": False, "erreur": str(erreur)})
        except Exception as erreur:
            return self._json(
                500, {"ok": False, "erreur": f"{type(erreur).__name__}: {erreur}"}
            )


# ---------------------------------------------------------------------------
# Demarrage
# ---------------------------------------------------------------------------

def creer_serveur(port: int = 0) -> ThreadingHTTPServer:
    """Cree le serveur sur 127.0.0.1 (port 0 = port libre, pour les tests)."""
    serveur = ThreadingHTTPServer(("127.0.0.1", port), Cockpit)
    serveur.daemon_threads = True
    return serveur


def demarrer(port: int = PORT_DEFAUT) -> None:
    """Demarre le cockpit et affiche l'URL."""
    serveur = creer_serveur(port)
    mode = "MODE TEST (fixture)" if os.environ.get("TEST_MODE") == "1" else (
        f"entrepot : {base.chemin_base()}"
    )
    print(
        f"SupplySense (lecture seule, {mode}) "
        f"-> http://127.0.0.1:{serveur.server_address[1]}"
    )
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        serveur.server_close()
