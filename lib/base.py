"""Acces securise a l'entrepot analytique DuckDB.

Deux fonctions publiques :

- connexion() : ouvre la base en LECTURE SEULE. En mode test (TEST_MODE=1),
  ouvre la base fixture fixtures/entrepot_test.duckdb ; sinon ouvre le chemin
  pointe par DUCKDB_PATH (defaut : ../supply-dbt-warehouse/supply.duckdb).

- requete(sql) : execute une requete apres passage des gardes de securite :
  SELECT uniquement (CTE WITH ... SELECT acceptee), et seules les tables des
  5 marts autorises peuvent etre referencees. Toute autre requete leve
  RequeteInterditeErreur.
"""

from __future__ import annotations

import os
import re

import duckdb

RACINE_PROJET = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHEMIN_FIXTURE = os.path.join(RACINE_PROJET, "fixtures", "entrepot_test.duckdb")

CHEMIN_DEFAUT = os.path.join(
    RACINE_PROJET, "..", "supply-dbt-warehouse", "supply.duckdb"
)

MARTS_AUTORISES = frozenset(
    {
        "supply_couverture_stock",
        "supply_taux_rupture",
        "supply_delais_fournisseurs",
        "offre_marge_familles",
        "offre_top_flop_produits",
    }
)

# Mots-cles qui trahissent une tentative d'ecriture ou d'action hors lecture.
_MOTS_CLES_INTERDITS = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|attach|detach|"
    r"copy|export|import|install|load|pragma|set|call|vacuum|checkpoint|grant)\b",
    re.IGNORECASE,
)

# Identifiants captures apres FROM ou JOIN (tables referencees).
_TABLES_REFERENCEES = re.compile(
    r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_.\"]*)",
    re.IGNORECASE,
)


class RequeteInterditeErreur(Exception):
    """Levee quand une requete viole les gardes de securite."""


def chemin_base() -> str:
    """Retourne le chemin de la base a ouvrir selon l'environnement."""
    if os.environ.get("TEST_MODE") == "1":
        return CHEMIN_FIXTURE
    return os.environ.get("DUCKDB_PATH", CHEMIN_DEFAUT)


def connexion() -> duckdb.DuckDBPyConnection:
    """Ouvre la base DuckDB en lecture seule et retourne la connexion."""
    chemin = chemin_base()
    if not os.path.exists(chemin):
        raise FileNotFoundError(
            f"Base DuckDB introuvable : {chemin}. "
            "Definissez DUCKDB_PATH ou construisez la fixture avec "
            "python fixtures/construire_base.py"
        )
    return duckdb.connect(chemin, read_only=True)


def _retirer_litteraux(sql: str) -> str:
    """Retire les chaines litterales pour ne pas analyser leur contenu."""
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


def _verifier(sql: str) -> None:
    """Applique les gardes : SELECT uniquement, marts autorises uniquement."""
    if not isinstance(sql, str) or not sql.strip():
        raise RequeteInterditeErreur("Requete vide ou invalide.")

    nettoyee = _retirer_litteraux(sql).strip()

    if ";" in nettoyee.rstrip(";"):
        raise RequeteInterditeErreur(
            "Une seule instruction par requete est autorisee."
        )

    premier_mot = nettoyee.split(None, 1)[0].lower()
    if premier_mot not in ("select", "with"):
        raise RequeteInterditeErreur(
            f"Seules les requetes SELECT sont autorisees (recu : {premier_mot.upper()})."
        )

    interdit = _MOTS_CLES_INTERDITS.search(nettoyee)
    if interdit:
        raise RequeteInterditeErreur(
            f"Mot-cle interdit dans la requete : {interdit.group(0).upper()}."
        )

    # Les CTE declarees dans la requete sont des alias legitimes.
    alias_cte = {
        m.group(1).lower()
        for m in re.finditer(
            r"\b(?:with|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(",
            nettoyee,
            re.IGNORECASE,
        )
    }

    for correspondance in _TABLES_REFERENCEES.finditer(nettoyee):
        table = correspondance.group(1).strip('"').lower()
        # Sous-requete : FROM ( SELECT ... ) capture rien ici, mais on ignore
        # les fonctions de table eventuelles par prudence.
        if table.startswith("("):
            continue
        nom_simple = table.split(".")[-1]
        if nom_simple in alias_cte:
            continue
        if nom_simple not in MARTS_AUTORISES:
            raise RequeteInterditeErreur(
                f"Table non autorisee : {table}. "
                f"Tables permises : {', '.join(sorted(MARTS_AUTORISES))}."
            )


def requete(sql: str) -> list[dict]:
    """Execute une requete SELECT gardee et retourne une liste de dicts."""
    _verifier(sql)
    con = connexion()
    try:
        resultat = con.execute(sql)
        colonnes = [description[0] for description in resultat.description]
        lignes = resultat.fetchall()
    finally:
        con.close()
    return [dict(zip(colonnes, ligne)) for ligne in lignes]
