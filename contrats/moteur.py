"""Moteur des contrats de donnees : ODCS v3 (Bitol) applique aux seeds.

Les contrats vivent cote producteur, dans le repo dbt (contracts/
*.odcs.yaml, chemin DBT_DIR, defaut ../supply-dbt-warehouse). Chaque
contrat decrit un ou plusieurs objets (schema), leurs colonnes, leurs
regles de qualite et son SLA de fraicheur. Ce moteur valide les seeds
CSV contre leur contrat, sans rien ecrire :

- schema : colonnes presentes (ni manquante ni inattendue), types
  logiques (string, integer, date), champs obligatoires non vides ;
- unicite : cle primaire simple ou composite (primaryKey) ;
- regles de qualite : entrees quality de type custom, moteur
  "supplysense", dont l'implementation est un petit dictionnaire de
  controle interprete ici (seuil, valeurs autorisees, comparaison de
  colonnes, reference croisee, dates, coherence fret / delai, familles
  lues dans models/staging/schema.yml) ;
- fraicheur : slaProperties property=freshness, verifiee sur la date la
  plus recente du fichier, avec l'age reel en jours.

Verdict par contrat : {nom, version, owner, statut conforme / viole,
regles: [{nom, description, statut, detail}], fraicheur: {...}}.
Le pipeline appelle valider_tout() AVANT chaque dbt build et refuse le
build des qu'une regle est violee (statut echec_contrats).
"""

from __future__ import annotations

import csv
import datetime
import io
import os

import yaml

RACINE_PROJET = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DBT_DIR_DEFAUT = os.path.join(RACINE_PROJET, "..", "supply-dbt-warehouse")
"""Repo dbt par defaut, surchargable par la variable d'environnement DBT_DIR."""

NOM_DOSSIER_CONTRATS = "contracts"
SUFFIXE_CONTRAT = ".odcs.yaml"

STATUT_CONFORME = "conforme"
STATUT_VIOLE = "viole"

MAX_EXEMPLES = 3
"""Nombre maximal de lignes citees dans le detail d'une violation."""

OPERATEURS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
}


class ErreurContrat(Exception):
    """Contrat illisible ou implementation de controle inconnue."""


# ---------------------------------------------------------------------------
# Chemins et chargement
# ---------------------------------------------------------------------------

def dossier_dbt() -> str:
    """Chemin absolu du repo dbt (DBT_DIR, defaut ../supply-dbt-warehouse)."""
    return os.path.abspath(os.environ.get("DBT_DIR", DBT_DIR_DEFAUT))


def dossier_contrats(repertoire_dbt: str | None = None) -> str:
    """Dossier contracts/ du repo dbt."""
    return os.path.join(repertoire_dbt or dossier_dbt(), NOM_DOSSIER_CONTRATS)


def charger_contrats(repertoire_dbt: str | None = None) -> list[dict]:
    """Charge les contrats ODCS du repo dbt, tries par nom de fichier.

    Retourne une liste de contrats (le YAML parse, enrichi de _fichier).
    Dossier absent : liste vide (aucun contrat publie, rien a valider).
    Contrat illisible ou invalide : ErreurContrat avec le fichier en cause.
    """
    dossier = dossier_contrats(repertoire_dbt)
    if not os.path.isdir(dossier):
        return []
    contrats: list[dict] = []
    for nom in sorted(os.listdir(dossier)):
        if not nom.endswith(SUFFIXE_CONTRAT):
            continue
        chemin = os.path.join(dossier, nom)
        try:
            with open(chemin, encoding="utf-8") as fichier:
                contrat = yaml.safe_load(fichier)
        except (OSError, yaml.YAMLError) as erreur:
            raise ErreurContrat(f"Contrat illisible : {chemin} ({erreur}).")
        if not isinstance(contrat, dict) or contrat.get("kind") != "DataContract":
            raise ErreurContrat(
                f"Contrat invalide : {chemin} (kind DataContract attendu)."
            )
        contrat["_fichier"] = nom
        contrats.append(contrat)
    return contrats


def _lire_csv(repertoire_dbt: str, chemin_physique: str) -> tuple[list[str], list[dict]]:
    """Lit un CSV du repo dbt : (colonnes d'entete, lignes en dicts)."""
    chemin = os.path.join(repertoire_dbt, chemin_physique)
    if not os.path.exists(chemin):
        raise FileNotFoundError(f"Seed introuvable : {chemin}.")
    with open(chemin, "rb") as fichier:
        octets = fichier.read()
    lecteur = csv.reader(io.StringIO(octets.decode("utf-8")))
    lignes_brutes = [ligne for ligne in lecteur if ligne]
    if not lignes_brutes:
        return [], []
    colonnes = lignes_brutes[0]
    lignes = [
        {colonnes[i]: (valeurs[i] if i < len(valeurs) else "") for i in range(len(colonnes))}
        for valeurs in lignes_brutes[1:]
    ]
    return colonnes, lignes


def _table_seed(repertoire_dbt: str, nom_table: str, cache: dict) -> list[dict]:
    """Lignes d'un seed reference par son nom de table (avec cache)."""
    if nom_table not in cache:
        try:
            _, lignes = _lire_csv(
                repertoire_dbt, os.path.join("seeds", nom_table + ".csv")
            )
        except FileNotFoundError:
            lignes = []
        cache[nom_table] = lignes
    return cache[nom_table]


def owner_du_contrat(contrat: dict) -> str:
    """Proprietaire du contrat : membre team de role owner (Nom - Equipe)."""
    for membre in contrat.get("team") or []:
        if (membre.get("role") or "").lower() == "owner":
            nom = membre.get("name") or membre.get("username") or "inconnu"
            equipe = membre.get("description")
            return f"{nom} - {equipe}" if equipe else nom
    return "non renseigne"


# ---------------------------------------------------------------------------
# Controles elementaires
# ---------------------------------------------------------------------------

def _exemples(violations: list[str]) -> str:
    """Resume une liste de violations : n lignes, 3 exemples au plus."""
    nombre = len(violations)
    extraits = ", ".join(violations[:MAX_EXEMPLES])
    suite = ", ..." if nombre > MAX_EXEMPLES else ""
    return f"{nombre} ligne(s) en violation : {extraits}{suite}"


def _entier(valeur) -> int | None:
    try:
        return int(str(valeur).strip())
    except (TypeError, ValueError):
        return None


def _date(valeur) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(valeur).strip())
    except (TypeError, ValueError):
        return None


def _identite_ligne(ligne: dict, colonnes_cle: list[str]) -> str:
    """Identite lisible d'une ligne : la cle primaire, sinon la 1re colonne."""
    if colonnes_cle:
        return "/".join(str(ligne.get(c, "")) for c in colonnes_cle)
    valeurs = list(ligne.values())
    return str(valeurs[0]) if valeurs else "?"


def familles_schema_dbt(
    repertoire_dbt: str, modele: str, colonne: str
) -> list[str]:
    """Valeurs accepted_values d'une colonne d'un modele de schema.yml."""
    chemin = os.path.join(repertoire_dbt, "models", "staging", "schema.yml")
    if not os.path.exists(chemin):
        raise ErreurContrat(f"Contrat dbt schema.yml introuvable : {chemin}.")
    with open(chemin, encoding="utf-8") as fichier:
        schema = yaml.safe_load(fichier)
    for bloc in schema.get("models") or []:
        if bloc.get("name") != modele:
            continue
        for col in bloc.get("columns") or []:
            if col.get("name") != colonne:
                continue
            for test in col.get("tests") or []:
                if isinstance(test, dict) and "accepted_values" in test:
                    return [
                        str(v) for v in test["accepted_values"].get("values") or []
                    ]
    raise ErreurContrat(
        f"accepted_values introuvable pour {modele}.{colonne} dans schema.yml."
    )


def _executer_controle(
    implementation: dict,
    lignes: list[dict],
    colonnes_cle: list[str],
    repertoire_dbt: str,
    cache: dict,
) -> list[str]:
    """Execute un controle custom supplysense, retourne les violations."""
    controle = implementation.get("controle")
    violations: list[str] = []

    if controle == "seuil":
        colonne = implementation["colonne"]
        comparer = OPERATEURS[implementation["operateur"]]
        seuil = int(implementation["valeur"])
        for ligne in lignes:
            nombre = _entier(ligne.get(colonne))
            if nombre is None or not comparer(nombre, seuil):
                violations.append(
                    f"{_identite_ligne(ligne, colonnes_cle)} "
                    f"({colonne} = {ligne.get(colonne)!r})"
                )
        return violations

    if controle == "valeurs_autorisees":
        colonne = implementation["colonne"]
        valeurs = {str(v) for v in implementation.get("valeurs") or []}
        for ligne in lignes:
            if str(ligne.get(colonne, "")) not in valeurs:
                violations.append(
                    f"{_identite_ligne(ligne, colonnes_cle)} "
                    f"({colonne} = {ligne.get(colonne)!r})"
                )
        return violations

    if controle == "valeurs_schema_dbt":
        colonne = implementation["colonne"]
        valeurs = set(
            familles_schema_dbt(
                repertoire_dbt,
                implementation["modele"],
                implementation.get("colonne_schema", colonne),
            )
        )
        for ligne in lignes:
            if str(ligne.get(colonne, "")) not in valeurs:
                violations.append(
                    f"{_identite_ligne(ligne, colonnes_cle)} "
                    f"({colonne} = {ligne.get(colonne)!r})"
                )
        return violations

    if controle == "comparaison_colonnes":
        gauche = implementation["colonne_gauche"]
        droite = implementation["colonne_droite"]
        comparer = OPERATEURS[implementation["operateur"]]
        for ligne in lignes:
            a, b = _entier(ligne.get(gauche)), _entier(ligne.get(droite))
            if a is None or b is None or not comparer(a, b):
                violations.append(
                    f"{_identite_ligne(ligne, colonnes_cle)} "
                    f"({gauche} = {ligne.get(gauche)!r}, "
                    f"{droite} = {ligne.get(droite)!r})"
                )
        return violations

    if controle == "reference":
        colonne = implementation["colonne"]
        cible = implementation["colonne_cible"]
        existants = {
            str(l.get(cible, ""))
            for l in _table_seed(repertoire_dbt, implementation["table"], cache)
        }
        for ligne in lignes:
            if str(ligne.get(colonne, "")) not in existants:
                violations.append(
                    f"{_identite_ligne(ligne, colonnes_cle)} "
                    f"({colonne} = {ligne.get(colonne)!r} absent de "
                    f"{implementation['table']}.csv)"
                )
        return violations

    if controle == "date_non_future":
        colonne = implementation["colonne"]
        aujourdhui = datetime.date.today()
        for ligne in lignes:
            date = _date(ligne.get(colonne))
            if date is None or date > aujourdhui:
                violations.append(
                    f"{_identite_ligne(ligne, colonnes_cle)} "
                    f"({colonne} = {ligne.get(colonne)!r})"
                )
        return violations

    if controle == "comparaison_jointure":
        colonne = implementation["colonne"]
        comparer = OPERATEURS[implementation["operateur"]]
        cle, cle_cible = implementation["cle"], implementation["cle_cible"]
        colonne_cible = implementation["colonne_cible"]
        index = {
            str(l.get(cle_cible, "")): l
            for l in _table_seed(repertoire_dbt, implementation["table"], cache)
        }
        for ligne in lignes:
            cible = index.get(str(ligne.get(cle, "")))
            if cible is None:
                continue  # l'absence de reference releve du controle reference
            a, b = _date(ligne.get(colonne)), _date(cible.get(colonne_cible))
            if a is None or b is None or not comparer(a, b):
                violations.append(
                    f"{_identite_ligne(ligne, colonnes_cle)} "
                    f"({colonne} = {ligne.get(colonne)!r} contre "
                    f"{colonne_cible} = {cible.get(colonne_cible)!r})"
                )
        return violations

    if controle == "coherence_fret_delai":
        cle = implementation["cle"]
        colonne_mode = implementation["colonne_mode"]
        colonne_delai = implementation["colonne_delai"]
        bornes = implementation.get("bornes") or {}
        fournisseurs = {
            str(l.get(cle, "")): l
            for l in _table_seed(
                repertoire_dbt, implementation["table_fournisseurs"], cache
            )
        }
        deja_vus: set[str] = set()
        for ligne in lignes:
            id_fournisseur = str(ligne.get(cle, ""))
            fournisseur = fournisseurs.get(id_fournisseur)
            if fournisseur is None or id_fournisseur in deja_vus:
                continue
            deja_vus.add(id_fournisseur)
            mode = str(fournisseur.get(colonne_mode, ""))
            delai = _entier(fournisseur.get(colonne_delai))
            plage = bornes.get(mode)
            if plage is None or delai is None or not (
                int(plage[0]) <= delai <= int(plage[1])
            ):
                violations.append(
                    f"{id_fournisseur} ({colonne_mode} = {mode!r}, "
                    f"{colonne_delai} = {fournisseur.get(colonne_delai)!r})"
                )
        return violations

    raise ErreurContrat(f"Controle inconnu dans un contrat : {controle!r}.")


# ---------------------------------------------------------------------------
# Validation d'un contrat
# ---------------------------------------------------------------------------

def _regle(nom: str, description: str, violations: list[str] | None,
           objet: str) -> dict:
    """Construit l'entree de resultat d'une regle."""
    conformite = not violations
    return {
        "nom": nom,
        "objet": objet,
        "description": " ".join(str(description or "").split()),
        "statut": STATUT_CONFORME if conformite else STATUT_VIOLE,
        "detail": "" if conformite else _exemples(violations),
    }


def _valider_objet(
    objet: dict, repertoire_dbt: str, cache: dict
) -> tuple[list[dict], dict | None]:
    """Valide un objet de schema : regles produites + lignes lues."""
    nom_objet = objet.get("name") or "objet"
    proprietes = objet.get("properties") or []
    attendues = [p["name"] for p in proprietes]
    regles: list[dict] = []

    try:
        colonnes, lignes = _lire_csv(repertoire_dbt, objet.get("physicalName"))
    except FileNotFoundError as erreur:
        regles.append(
            _regle(
                f"schema_{nom_objet}",
                f"Le fichier {objet.get('physicalName')} existe et porte les "
                f"{len(attendues)} colonnes du contrat.",
                [str(erreur)],
                nom_objet,
            )
        )
        return regles, None

    # 1. Schema : colonnes, champs obligatoires, types logiques.
    problemes: list[str] = []
    manquantes = [c for c in attendues if c not in colonnes]
    inattendues = [c for c in colonnes if c not in attendues]
    if manquantes:
        problemes.append("colonne(s) manquante(s) : " + ", ".join(manquantes))
    if inattendues:
        problemes.append("colonne(s) inattendue(s) : " + ", ".join(inattendues))

    colonnes_cle = [
        p["name"]
        for p in sorted(
            (p for p in proprietes if p.get("primaryKey")),
            key=lambda p: p.get("primaryKeyPosition") or 1,
        )
    ]
    for propriete in proprietes:
        nom_colonne = propriete["name"]
        if nom_colonne in manquantes:
            continue
        type_logique = propriete.get("logicalType") or "string"
        for ligne in lignes:
            valeur = ligne.get(nom_colonne, "")
            identite = _identite_ligne(ligne, colonnes_cle)
            if propriete.get("required") and not str(valeur).strip():
                problemes.append(f"{identite} ({nom_colonne} vide)")
            elif type_logique == "integer" and str(valeur).strip() and _entier(valeur) is None:
                problemes.append(
                    f"{identite} ({nom_colonne} = {valeur!r} non entier)"
                )
            elif type_logique == "date" and str(valeur).strip() and _date(valeur) is None:
                problemes.append(
                    f"{identite} ({nom_colonne} = {valeur!r} non ISO)"
                )
    regles.append(
        _regle(
            f"schema_{nom_objet}",
            f"Les {len(attendues)} colonnes du contrat sont presentes, "
            f"typees et renseignees ({objet.get('physicalName')}).",
            problemes,
            nom_objet,
        )
    )

    # 2. Unicite de la cle primaire (simple ou composite).
    if colonnes_cle and not manquantes:
        vus: set[tuple] = set()
        doublons: list[str] = []
        for ligne in lignes:
            cle = tuple(str(ligne.get(c, "")) for c in colonnes_cle)
            if cle in vus:
                doublons.append("/".join(cle))
            vus.add(cle)
        regles.append(
            _regle(
                f"unicite_{nom_objet}",
                "Unicite de la cle (" + ", ".join(colonnes_cle) + ").",
                doublons,
                nom_objet,
            )
        )

    # 3. Regles de qualite du contrat (colonnes puis objet).
    entrees_qualite: list[dict] = []
    for propriete in proprietes:
        entrees_qualite.extend(propriete.get("quality") or [])
    entrees_qualite.extend(objet.get("quality") or [])
    for entree in entrees_qualite:
        implementation = entree.get("implementation") or {}
        colonne_visee = implementation.get("colonne")
        if colonne_visee and colonne_visee in manquantes:
            violations = [f"colonne {colonne_visee} absente du seed"]
        else:
            violations = _executer_controle(
                implementation, lignes, colonnes_cle, repertoire_dbt, cache
            )
        regles.append(
            _regle(
                entree.get("name") or implementation.get("controle") or "regle",
                entree.get("description") or "",
                violations,
                nom_objet,
            )
        )

    return regles, {"colonnes": colonnes, "lignes": lignes}


def _valider_fraicheur(contrat: dict, repertoire_dbt: str) -> dict | None:
    """Verifie le SLA de fraicheur (slaProperties property=freshness)."""
    for sla in contrat.get("slaProperties") or []:
        if (sla.get("property") or "").lower() != "freshness":
            continue
        element = str(sla.get("element") or "")
        if "." not in element:
            continue
        nom_objet, colonne = element.split(".", 1)
        limite = int(sla.get("value") or 0)
        objets = {o.get("name"): o for o in contrat.get("schema") or []}
        objet = objets.get(nom_objet)
        if objet is None:
            continue
        try:
            _, lignes = _lire_csv(repertoire_dbt, objet.get("physicalName"))
        except FileNotFoundError:
            lignes = []
        dates = [d for d in (_date(l.get(colonne)) for l in lignes) if d]
        if not dates:
            return {
                "element": element,
                "limite_jours": limite,
                "derniere_date": None,
                "age_jours": None,
                "statut": STATUT_VIOLE,
                "detail": f"aucune date lisible dans {colonne}",
            }
        derniere = max(dates)
        age = (datetime.date.today() - derniere).days
        conformite = age <= limite
        return {
            "element": element,
            "limite_jours": limite,
            "derniere_date": derniere.isoformat(),
            "age_jours": age,
            "statut": STATUT_CONFORME if conformite else STATUT_VIOLE,
            "detail": (
                f"dernier inventaire {derniere.isoformat()}, "
                f"il y a {age} jour(s) (limite {limite} jours)"
                if nom_objet == "stocks"
                else f"derniere date {derniere.isoformat()}, "
                f"il y a {age} jour(s) (limite {limite} jours)"
            ),
        }
    return None


def valider_contrat(contrat: dict, repertoire_dbt: str | None = None) -> dict:
    """Valide un contrat ODCS contre ses seeds. Retourne le verdict."""
    repertoire = repertoire_dbt or dossier_dbt()
    cache: dict = {}
    regles: list[dict] = []
    for objet in contrat.get("schema") or []:
        regles_objet, _ = _valider_objet(objet, repertoire, cache)
        regles.extend(regles_objet)
    fraicheur = _valider_fraicheur(contrat, repertoire)

    violations = [r for r in regles if r["statut"] == STATUT_VIOLE]
    fraicheur_violee = fraicheur is not None and fraicheur["statut"] == STATUT_VIOLE
    return {
        "id": contrat.get("id"),
        "nom": contrat.get("name") or contrat.get("id"),
        "fichier": contrat.get("_fichier"),
        "version": str(contrat.get("version") or ""),
        "owner": owner_du_contrat(contrat),
        "seeds": [
            o.get("physicalName") for o in contrat.get("schema") or []
        ],
        "statut": (
            STATUT_VIOLE if violations or fraicheur_violee else STATUT_CONFORME
        ),
        "nb_regles": len(regles),
        "nb_violations": len(violations) + (1 if fraicheur_violee else 0),
        "regles": regles,
        "fraicheur": fraicheur,
    }


def valider_tout(repertoire_dbt: str | None = None) -> dict:
    """Valide tous les contrats publies. Verdict global + detail par contrat."""
    repertoire = repertoire_dbt or dossier_dbt()
    resultats = [
        valider_contrat(contrat, repertoire)
        for contrat in charger_contrats(repertoire)
    ]
    nb_violations = sum(r["nb_violations"] for r in resultats)
    return {
        "verifie_le": datetime.datetime.now().isoformat(timespec="seconds"),
        "dossier": dossier_contrats(repertoire),
        "nb_contrats": len(resultats),
        "nb_regles": sum(r["nb_regles"] for r in resultats),
        "nb_violations": nb_violations,
        "conforme": nb_violations == 0,
        "contrats": resultats,
    }


def regles_violees(verdict: dict) -> list[str]:
    """Phrases en francais des regles violees d'un verdict valider_tout."""
    phrases: list[str] = []
    for contrat in verdict.get("contrats") or []:
        for regle in contrat.get("regles") or []:
            if regle["statut"] == STATUT_VIOLE:
                phrases.append(
                    f"{contrat['nom']} : regle {regle['nom']} violee "
                    f"({regle['detail']})"
                )
        fraicheur = contrat.get("fraicheur")
        if fraicheur and fraicheur["statut"] == STATUT_VIOLE:
            phrases.append(
                f"{contrat['nom']} : SLA de fraicheur depasse "
                f"({fraicheur['detail']})"
            )
    return phrases
