"""Moteur du pipeline : lance le build dbt et tient l'etat partage.

Responsabilites :
- poser un verrou simple (fichier .pipeline.lock dans le repo dbt) pour
  refuser deux builds concurrents ;
- ecrire etat_build.json dans le repo dbt : statut en_cours pendant le
  build, puis succes ou echec avec les compteurs pass/fail/total, la
  duree et un historique borne aux 20 derniers builds ;
- en cas d'echec, appeler gardien_qualite.moteur sur le run_results.json
  reel pour deposer un diagnostic lisible dans outbox/.

Aucune ecriture dans les seeds ni dans la base : le pipeline orchestre,
il ne saisit pas. La commande de build est surchargable (constante
PIPELINE_COMMANDE) pour que les tests n'executent jamais un vrai dbt.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import time

RACINE_PROJET = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DBT_DIR_DEFAUT = os.path.join(RACINE_PROJET, "..", "supply-dbt-warehouse")
"""Repo dbt par defaut, surchargable par la variable d'environnement DBT_DIR."""

PIPELINE_COMMANDE: list[str] | None = None
"""Commande de build, surchargable pour les tests.

Si None (defaut), la commande reelle est <DBT_DIR>/.venv/bin/dbt build,
executee dans le repo dbt avec DBT_PROFILES_DIR pointant dessus.
"""

NOM_ETAT = "etat_build.json"
NOM_VERROU = ".pipeline.lock"
TAILLE_HISTORIQUE = 20
DECLENCHEURS_VALIDES = ("manuel", "nocturne", "supplyops", "supplysense")

STATUT_SUCCES = "succes"
STATUT_ECHEC = "echec"
STATUT_EN_COURS = "en_cours"
STATUT_JAMAIS_CONSTRUIT = "jamais_construit"

STATUTS_DBT_PASSANTS = ("pass", "success")
STATUTS_DBT_ECHOUANTS = ("fail", "error")


def dossier_dbt() -> str:
    """Chemin absolu du repo dbt (DBT_DIR, defaut ../supply-dbt-warehouse)."""
    return os.path.abspath(os.environ.get("DBT_DIR", DBT_DIR_DEFAUT))


def chemin_etat() -> str:
    """Chemin du fichier d'etat partage etat_build.json (dans le repo dbt)."""
    return os.path.join(dossier_dbt(), NOM_ETAT)


def chemin_verrou() -> str:
    """Chemin du fichier de verrou contre les builds concurrents."""
    return os.path.join(dossier_dbt(), NOM_VERROU)


def dossier_outbox() -> str:
    """Dossier des diagnostics du gardien, surchargable par PIPELINE_OUTBOX."""
    return os.environ.get(
        "PIPELINE_OUTBOX", os.path.join(RACINE_PROJET, "outbox")
    )


def commande_build() -> list[str]:
    """Commande de build effective (constante de test ou vrai dbt build)."""
    if PIPELINE_COMMANDE is not None:
        return list(PIPELINE_COMMANDE)
    return [os.path.join(dossier_dbt(), ".venv", "bin", "dbt"), "build"]


def lire_etat() -> dict:
    """Lit etat_build.json, ou retourne l'etat "jamais construit"."""
    chemin = chemin_etat()
    if os.path.exists(chemin):
        with open(chemin, encoding="utf-8") as fichier:
            return json.load(fichier)
    return {
        "horodatage": None,
        "statut": STATUT_JAMAIS_CONSTRUIT,
        "pass": 0,
        "fail": 0,
        "total": 0,
        "duree_s": 0.0,
        "declencheur": None,
        "historique": [],
    }


def _ecrire_etat_atomique(etat: dict) -> None:
    """Ecrit etat_build.json de facon atomique (temporaire puis rename)."""
    chemin = chemin_etat()
    chemin_temporaire = chemin + ".tmp"
    with open(chemin_temporaire, "w", encoding="utf-8") as fichier:
        json.dump(etat, fichier, ensure_ascii=False, indent=2)
        fichier.write("\n")
    os.replace(chemin_temporaire, chemin)


def _parser_run_results(chemin: str) -> dict:
    """Extrait pass/fail/total et la duree d'un run_results.json de dbt."""
    with open(chemin, encoding="utf-8") as fichier:
        contenu = json.load(fichier)
    resultats = contenu.get("results")
    if not isinstance(resultats, list):
        raise ValueError(
            f"Fichier {chemin} invalide : cle 'results' absente ou mal formee."
        )
    nb_pass = sum(
        1 for r in resultats
        if (r.get("status") or "").lower() in STATUTS_DBT_PASSANTS
    )
    nb_fail = sum(
        1 for r in resultats
        if (r.get("status") or "").lower() in STATUTS_DBT_ECHOUANTS
    )
    return {
        "pass": nb_pass,
        "fail": nb_fail,
        "total": len(resultats),
        "duree_s": round(float(contenu.get("elapsed_time") or 0.0), 3),
    }


def _diagnostiquer_echec(chemin_run_results: str) -> str | None:
    """Delegue au gardien de qualite le diagnostic d'un build en echec.

    Depose un rapport markdown dans outbox/ et retourne son chemin.
    Un gardien qui echoue ne doit pas masquer l'echec du build : toute
    erreur est signalee sur stderr et la fonction retourne None.
    """
    try:
        from gardien_qualite import moteur as gardien

        rapport = gardien.analyser(chemin_run_results)
        return gardien.ecrire_rapport(rapport, dossier_outbox())
    except Exception as erreur:
        print(
            f"Gardien de qualite indisponible pour le diagnostic : {erreur}",
            file=sys.stderr,
        )
        return None


def executer_build(declencheur: str = "manuel") -> int:
    """Lance le build dbt et met a jour l'etat partage. Retourne 0 ou 1.

    Deroulement : pose du verrou, statut en_cours, execution de la
    commande de build, parsing de target/run_results.json, mise a jour de
    etat_build.json (historique borne a 20), liberation du verrou. En cas
    d'echec, le gardien de qualite depose un diagnostic dans outbox/.
    """
    if declencheur not in DECLENCHEURS_VALIDES:
        raise ValueError(
            f"Declencheur inconnu : {declencheur!r} "
            f"(attendu : {', '.join(DECLENCHEURS_VALIDES)})."
        )

    repertoire_dbt = dossier_dbt()
    if not os.path.isdir(repertoire_dbt):
        print(
            f"Repo dbt introuvable : {repertoire_dbt} "
            "(verifier la variable DBT_DIR).",
            file=sys.stderr,
        )
        return 1

    # Verrou simple : creation exclusive du fichier .pipeline.lock.
    verrou = chemin_verrou()
    try:
        descripteur = os.open(verrou, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print(
            "Un build est deja en cours (verrou .pipeline.lock present). "
            "Nouveau build refuse.",
            file=sys.stderr,
        )
        return 1
    with os.fdopen(descripteur, "w", encoding="utf-8") as fichier:
        fichier.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "declencheur": declencheur,
                    "depuis": datetime.datetime.now().isoformat(
                        timespec="seconds"
                    ),
                }
            )
            + "\n"
        )

    try:
        etat_precedent = lire_etat()
        historique = list(etat_precedent.get("historique") or [])

        etat_en_cours = {
            "horodatage": datetime.datetime.now().isoformat(timespec="seconds"),
            "statut": STATUT_EN_COURS,
            "pass": 0,
            "fail": 0,
            "total": 0,
            "duree_s": 0.0,
            "declencheur": declencheur,
            "historique": historique,
        }
        _ecrire_etat_atomique(etat_en_cours)

        environnement = dict(os.environ)
        environnement.setdefault("DBT_PROFILES_DIR", repertoire_dbt)

        debut = time.monotonic()
        try:
            processus = subprocess.run(
                commande_build(), cwd=repertoire_dbt, env=environnement
            )
            code_commande = processus.returncode
        except OSError as erreur:
            print(f"Commande de build introuvable : {erreur}", file=sys.stderr)
            code_commande = 1
        duree_mesuree = round(time.monotonic() - debut, 3)

        chemin_run_results = os.path.join(
            repertoire_dbt, "target", "run_results.json"
        )
        compteurs = {"pass": 0, "fail": 0, "total": 0, "duree_s": duree_mesuree}
        run_results_disponible = os.path.exists(chemin_run_results)
        if run_results_disponible:
            try:
                compteurs = _parser_run_results(chemin_run_results)
            except (ValueError, json.JSONDecodeError) as erreur:
                print(
                    f"run_results.json illisible : {erreur}", file=sys.stderr
                )
                run_results_disponible = False

        statut = (
            STATUT_SUCCES
            if code_commande == 0 and compteurs["fail"] == 0
            and run_results_disponible
            else STATUT_ECHEC
        )

        etat_final = {
            "horodatage": datetime.datetime.now().isoformat(timespec="seconds"),
            "statut": statut,
            "pass": compteurs["pass"],
            "fail": compteurs["fail"],
            "total": compteurs["total"],
            "duree_s": compteurs["duree_s"],
            "declencheur": declencheur,
        }
        historique.insert(
            0, {cle: etat_final[cle] for cle in etat_final}
        )
        etat_final["historique"] = historique[:TAILLE_HISTORIQUE]
        _ecrire_etat_atomique(etat_final)

        if statut == STATUT_ECHEC and run_results_disponible:
            chemin_diagnostic = _diagnostiquer_echec(chemin_run_results)
            if chemin_diagnostic:
                print(
                    f"Diagnostic du gardien depose : {chemin_diagnostic}",
                    file=sys.stderr,
                )

        return 0 if statut == STATUT_SUCCES else 1
    finally:
        try:
            os.remove(verrou)
        except FileNotFoundError:
            pass
