"""Moteur du pipeline : lance le build dbt et tient l'etat partage.

Responsabilites :
- poser un verrou simple (fichier .pipeline.lock dans le repo dbt) pour
  refuser deux builds concurrents ;
- ecrire etat_build.json dans le repo dbt : statut en_cours pendant le
  build, puis succes ou echec avec les compteurs pass/fail/total, la
  duree et un historique borne aux 20 derniers builds ;
- photographier les KPI cles de l'entrepot AVANT et APRES le build
  (lecture seule via lib.base.requete, marts autorises uniquement) et
  ranger dans l'entree d'historique un bloc "impact" : valeurs avant,
  valeurs apres et phrases de deltas en francais ;
- apres un build reussi declenche par SupplyOps ou SupplySense, marquer
  "publie": true sur les saisies en attente de journal_ops.jsonl et
  ranger leurs resumes dans l'entree d'historique ("modifications") ;
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
import threading
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
NOM_JOURNAL_OPS = "journal_ops.jsonl"
NOM_BASE_ENTREPOT = "supply.duckdb"
TAILLE_HISTORIQUE = 20
DECLENCHEURS_VALIDES = ("manuel", "nocturne", "supplyops", "supplysense")
DECLENCHEURS_PUBLICATION = ("supplyops", "supplysense")
MAX_PHRASES_IMPACT = 5

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


def chemin_journal_ops() -> str:
    """Journal des saisies SupplyOps (JSON Lines, dans le repo dbt)."""
    return os.path.join(dossier_dbt(), NOM_JOURNAL_OPS)


def chemin_entrepot() -> str:
    """Chemin de la base DuckDB construite par le build (dans le repo dbt)."""
    return os.path.join(dossier_dbt(), NOM_BASE_ENTREPOT)


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


# ---------------------------------------------------------------------------
# Impact d'un build : photographie des KPI avant / apres et phrases de deltas
# ---------------------------------------------------------------------------

_verrou_photographie = threading.Lock()


def _nombre_fr(valeur, decimales: int = 0) -> str:
    """Formate un nombre a la francaise : 95 000 ou 6,48."""
    texte = f"{float(valeur):,.{decimales}f}"
    return texte.replace(",", " ").replace(".", ",")


def photographier_kpis(chemin_base: str | None = None) -> dict | None:
    """Photographie des KPI cles de l'entrepot, ou None sans base lisible.

    Les requetes passent par lib.base.requete (lecture seule, SELECT
    uniquement, allowlist des marts) : la base visee est pointee le temps
    de la photographie via DUCKDB_PATH, sous verrou pour ne pas perturber
    une lecture concurrente. Un echec de photographie ne doit jamais
    casser un build : toute erreur retourne None.
    """
    chemin = chemin_base or chemin_entrepot()
    if not os.path.exists(chemin):
        return None
    from lib import base

    with _verrou_photographie:
        memo_test = os.environ.pop("TEST_MODE", None)
        memo_chemin = os.environ.get("DUCKDB_PATH")
        os.environ["DUCKDB_PATH"] = chemin
        try:
            rupture = base.requete(
                "SELECT SUM(nb_observations) AS nb_observations, "
                "SUM(nb_ruptures) AS nb_ruptures FROM supply_taux_rupture"
            )[0]
            couverture = base.requete(
                "SELECT COUNT(DISTINCT id_produit) AS nb_produits, "
                "COUNT(DISTINCT CASE WHEN statut_couverture = 'rupture' "
                "THEN id_produit END) AS nb_produits_rupture, "
                "COUNT(CASE WHEN statut_couverture = 'dormant' THEN 1 END) "
                "AS nb_dormants, "
                "ROUND(MEDIAN(couverture_jours), 1) AS couverture_mediane "
                "FROM supply_couverture_stock"
            )[0]
            produits = base.requete(
                "SELECT DISTINCT id_produit, nom_produit "
                "FROM supply_couverture_stock ORDER BY id_produit"
            )
            ca = base.requete(
                "SELECT ROUND(SUM(chiffre_affaires), 0) AS ca_total "
                "FROM offre_marge_familles"
            )[0]
        except Exception as erreur:
            print(
                f"Photographie des KPI impossible sur {chemin} : {erreur}",
                file=sys.stderr,
            )
            return None
        finally:
            if memo_test is not None:
                os.environ["TEST_MODE"] = memo_test
            if memo_chemin is None:
                os.environ.pop("DUCKDB_PATH", None)
            else:
                os.environ["DUCKDB_PATH"] = memo_chemin

    nb_observations = int(rupture["nb_observations"] or 0)
    nb_ruptures = int(rupture["nb_ruptures"] or 0)
    return {
        "taux_rupture_pct": round(100.0 * nb_ruptures / nb_observations, 2)
        if nb_observations
        else 0.0,
        "nb_produits_rupture": int(couverture["nb_produits_rupture"] or 0),
        "nb_dormants": int(couverture["nb_dormants"] or 0),
        "couverture_mediane_jours": float(couverture["couverture_mediane"] or 0.0),
        "nb_produits": int(couverture["nb_produits"] or 0),
        "ca_total": float(ca["ca_total"] or 0.0),
        "produits": {
            ligne["id_produit"]: ligne["nom_produit"] for ligne in produits
        },
    }


def calculer_impact(avant: dict, apres: dict) -> dict:
    """Compare deux photographies de KPI et resume les deltas en francais.

    Retourne {"avant", "apres", "produits_apparus", "produits_disparus",
    "resume"} : le resume ne garde que les deltas non nuls (5 phrases au
    plus), ou une phrase unique quand rien n'a bouge.
    """
    produits_avant = avant.get("produits") or {}
    produits_apres = apres.get("produits") or {}
    apparus = [
        {"id_produit": id_produit, "nom_produit": produits_apres[id_produit]}
        for id_produit in sorted(set(produits_apres) - set(produits_avant))
    ]
    disparus = [
        {"id_produit": id_produit, "nom_produit": produits_avant[id_produit]}
        for id_produit in sorted(set(produits_avant) - set(produits_apres))
    ]

    phrases: list[str] = []
    if apparus:
        noms = ", ".join(p["nom_produit"] for p in apparus)
        phrases.append(
            f"1 nouveau produit dans l'entrepot : {noms}"
            if len(apparus) == 1
            else f"{len(apparus)} nouveaux produits dans l'entrepot : {noms}"
        )
    if disparus:
        noms = ", ".join(p["nom_produit"] for p in disparus)
        phrases.append(
            f"1 produit retire de l'entrepot : {noms}"
            if len(disparus) == 1
            else f"{len(disparus)} produits retires de l'entrepot : {noms}"
        )
    if avant["taux_rupture_pct"] != apres["taux_rupture_pct"]:
        phrases.append(
            f"Taux de rupture : {_nombre_fr(avant['taux_rupture_pct'], 2)} % "
            f"-> {_nombre_fr(apres['taux_rupture_pct'], 2)} %"
        )
    if avant["nb_produits_rupture"] != apres["nb_produits_rupture"]:
        phrases.append(
            f"Produits en rupture : {avant['nb_produits_rupture']} "
            f"-> {apres['nb_produits_rupture']}"
        )
    if avant["nb_dormants"] != apres["nb_dormants"]:
        phrases.append(
            f"Produits dormants : {avant['nb_dormants']} "
            f"-> {apres['nb_dormants']}"
        )
    if avant["couverture_mediane_jours"] != apres["couverture_mediane_jours"]:
        phrases.append(
            f"Couverture mediane : "
            f"{_nombre_fr(avant['couverture_mediane_jours'], 1)} j "
            f"-> {_nombre_fr(apres['couverture_mediane_jours'], 1)} j"
        )
    if avant["ca_total"] != apres["ca_total"]:
        phrases.append(
            f"Chiffre d'affaires total : {_nombre_fr(avant['ca_total'])} MGA "
            f"-> {_nombre_fr(apres['ca_total'])} MGA"
        )
    if not phrases:
        phrases = ["Aucun impact mesurable sur les KPI."]

    sans_produits = lambda photo: {  # noqa: E731 - petite projection locale
        cle: valeur for cle, valeur in photo.items() if cle != "produits"
    }
    return {
        "avant": sans_produits(avant),
        "apres": sans_produits(apres),
        "produits_apparus": apparus,
        "produits_disparus": disparus,
        "resume": phrases[:MAX_PHRASES_IMPACT],
    }


# ---------------------------------------------------------------------------
# Journal des saisies SupplyOps : marquage publie apres un build reussi
# ---------------------------------------------------------------------------

def lire_journal_ops() -> list[dict]:
    """Lit journal_ops.jsonl (une saisie par ligne), liste vide sinon."""
    chemin = chemin_journal_ops()
    if not os.path.exists(chemin):
        return []
    lignes: list[dict] = []
    with open(chemin, encoding="utf-8") as fichier:
        for brut in fichier:
            brut = brut.strip()
            if not brut:
                continue
            try:
                lignes.append(json.loads(brut))
            except json.JSONDecodeError:
                continue
    return lignes


def publier_journal_ops() -> list[str]:
    """Marque publie=true sur les saisies en attente, retourne leurs resumes.

    Reecriture atomique du fichier (temporaire puis os.replace) : les
    resumes retournes sont ceux que CE build vient de publier.
    """
    lignes = lire_journal_ops()
    resumes = [
        ligne.get("resume") or "" for ligne in lignes if not ligne.get("publie")
    ]
    if not resumes:
        return []
    for ligne in lignes:
        ligne["publie"] = True
    chemin = chemin_journal_ops()
    chemin_temporaire = chemin + ".tmp"
    with open(chemin_temporaire, "w", encoding="utf-8") as fichier:
        for ligne in lignes:
            fichier.write(json.dumps(ligne, ensure_ascii=False) + "\n")
    os.replace(chemin_temporaire, chemin)
    return resumes


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

        photographie_avant = photographier_kpis()

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
        entree_historique = {cle: etat_final[cle] for cle in etat_final}

        # Impact du build : deltas des KPI entre l'avant et l'apres.
        if statut == STATUT_SUCCES and photographie_avant is not None:
            photographie_apres = photographier_kpis()
            if photographie_apres is not None:
                entree_historique["impact"] = calculer_impact(
                    photographie_avant, photographie_apres
                )

        # Saisies SupplyOps publiees par CE build (declencheur applicatif).
        entree_historique["modifications"] = (
            publier_journal_ops()
            if statut == STATUT_SUCCES
            and declencheur in DECLENCHEURS_PUBLICATION
            else []
        )

        historique.insert(0, entree_historique)
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
