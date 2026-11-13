"""Moteur question -> SQL -> execution -> reponse en francais.

Deux modes de fonctionnement :

1. Mode LLM : si la variable d'environnement ANTHROPIC_API_KEY est presente
   (ou si un client est injecte explicitement), la question est traduite en
   SQL par le modele claude-sonnet-5. La requete generee passe OBLIGATOIREMENT
   par la garde lib.base.requete (SELECT unique, marts autorises) avant toute
   execution, puis la reponse est reformulee en francais avec les chiffres.

2. Mode repli deterministe : sans cle, une bibliotheque de questions types
   reconnues par mots-cles fournit le SQL pre-ecrit et le gabarit de reponse.

Chaque question traitee est journalisee dans journal_qa.jsonl (audit).
"""

from __future__ import annotations

import datetime
import json
import os
import re
import unicodedata

from lib import base

MODELE_LLM = "claude-sonnet-5"

CHEMIN_JOURNAL_DEFAUT = os.path.join(base.RACINE_PROJET, "journal_qa.jsonl")

SCHEMA_MARTS = """Tables disponibles (DuckDB, lecture seule) :

1. supply_couverture_stock(id_produit, nom_produit, famille, id_entrepot,
   stock_disponible, vente_moyenne_jour, couverture_jours,
   statut_couverture)  -- statut_couverture : rupture / sain / surstock / dormant

2. supply_taux_rupture(famille, id_entrepot, date_jour,
   nb_references_actives, nb_references_rupture, taux_rupture_pct)

3. supply_delais_fournisseurs(id_fournisseur, nom_fournisseur,
   famille_principale, nb_commandes_periode, nb_commandes_a_temps,
   nb_commandes_completes, delai_moyen_jours, delai_contractuel_jours,
   taux_otif, date_derniere_reception)

4. offre_marge_familles(famille, quantite_vendue, chiffre_affaires,
   taux_marge, rotation_periode)

5. offre_top_flop_produits(id_produit, nom_produit, famille,
   quantite_vendue, chiffre_affaires, taux_marge, rang, type_classement)
   -- type_classement : top / flop ; rang : 1 = premier du classement
"""

PROMPT_SYSTEME_SQL = (
    "Tu es un traducteur de questions metier en SQL pour une enseigne de "
    "decoration et d'ameublement.\n\n"
    + SCHEMA_MARTS
    + "\nRegles STRICTES :\n"
    "- Reponds UNIQUEMENT par une requete SQL, sans explication ni balise.\n"
    "- Une SEULE instruction SELECT (les CTE WITH ... SELECT sont permises).\n"
    "- Interdiction absolue de toute ecriture : INSERT, UPDATE, DELETE, "
    "DROP, CREATE, ALTER, etc.\n"
    "- Seules les 5 tables listees ci-dessus peuvent etre referencees.\n"
    "- Dialecte DuckDB."
)

PROMPT_SYSTEME_REPONSE = (
    "Tu es un analyste data d'une enseigne de decoration et d'ameublement. "
    "On te donne une question metier et le resultat chiffre de la requete "
    "correspondante. Reponds en francais, en une ou deux phrases claires, "
    "en citant les chiffres exacts. Pas de tableau, pas de code."
)


class QuestionNonCompriseErreur(Exception):
    """Levee quand aucune question type ne correspond en mode repli."""


def _normaliser(texte: str) -> str:
    """Minuscules sans accents, pour une detection robuste des mots-cles."""
    decompose = unicodedata.normalize("NFD", texte.lower())
    return "".join(c for c in decompose if unicodedata.category(c) != "Mn")


# ---------------------------------------------------------------------------
# Mode repli deterministe : bibliotheque de questions types
# ---------------------------------------------------------------------------


def _fmt_pire_rotation(lignes: list[dict]) -> str:
    l = lignes[0]
    return (
        f"La famille avec la pire rotation est {l['famille']} "
        f"avec une rotation de {l['rotation_periode']} sur la periode."
    )


def _fmt_meilleures_marges(lignes: list[dict]) -> str:
    detail = ", ".join(
        f"{l['famille']} ({l['taux_marge']} %)" for l in lignes
    )
    return (
        f"Les {len(lignes)} meilleures marges par famille sont : {detail}."
    )


def _fmt_produits_rupture(lignes: list[dict]) -> str:
    if not lignes:
        return "Aucun produit n'est en rupture actuellement."
    detail = ", ".join(
        f"{l['nom_produit']} (entrepot {l['id_entrepot']})" for l in lignes
    )
    return f"{len(lignes)} produits sont en rupture : {detail}."


def _fmt_couverture_moyenne(lignes: list[dict]) -> str:
    return (
        f"La couverture de stock moyenne est de "
        f"{lignes[0]['couverture_moyenne']} jours sur l'ensemble des produits."
    )


def _fmt_top_produits(lignes: list[dict]) -> str:
    detail = ", ".join(
        f"{l['rang']}. {l['nom_produit']} ({l['chiffre_affaires']:.0f} euros)"
        for l in lignes
    )
    return f"Les {len(lignes)} meilleurs produits par classement sont : {detail}."


def _fmt_flop_produits(lignes: list[dict]) -> str:
    detail = ", ".join(
        f"{l['rang']}. {l['nom_produit']} ({l['quantite_vendue']} ventes)"
        for l in lignes
    )
    return f"Les {len(lignes)} produits les moins performants sont : {detail}."


def _fmt_otif(lignes: list[dict]) -> str:
    detail = ", ".join(
        f"{l['nom_fournisseur']} ({l['taux_otif']} %)" for l in lignes
    )
    return (
        f"Les fournisseurs au taux OTIF le plus faible sont : {detail}. "
        "Ce sont les premiers a challenger sur les delais."
    )


def _fmt_ca_famille(lignes: list[dict]) -> str:
    premiere = lignes[0]
    detail = ", ".join(
        f"{l['famille']} ({l['chiffre_affaires']:.0f} euros)" for l in lignes
    )
    return (
        f"Le chiffre d'affaires par famille est domine par "
        f"{premiere['famille']} avec {premiere['chiffre_affaires']:.0f} euros. "
        f"Top {len(lignes)} : {detail}."
    )


def _det_pire_rotation(q: str) -> bool:
    return "rotation" in q and any(
        mot in q for mot in ("pire", "faible", "mauvaise", "basse")
    )


def _det_meilleures_marges(q: str) -> bool:
    return "marge" in q


def _det_produits_rupture(q: str) -> bool:
    return "rupture" in q


def _det_couverture_moyenne(q: str) -> bool:
    return "couverture" in q


def _det_top_produits(q: str) -> bool:
    return "produit" in q and any(
        mot in q for mot in ("top", "meilleur", "vendus", "stars")
    )


def _det_flop_produits(q: str) -> bool:
    return "flop" in q or (
        "produit" in q and any(mot in q for mot in ("pire", "moins", "mauvais"))
    )


def _det_otif(q: str) -> bool:
    return "otif" in q or "fournisseur" in q or "delai" in q


def _det_ca_famille(q: str) -> bool:
    return ("chiffre" in q or re.search(r"\bca\b", q)) and "famille" in q


# Ordre de detection : du plus specifique au plus general.
QUESTIONS_TYPES: list[tuple[str, object, str, object]] = [
    (
        "couverture_moyenne",
        _det_couverture_moyenne,
        "SELECT ROUND(AVG(couverture_jours), 1) AS couverture_moyenne "
        "FROM supply_couverture_stock",
        _fmt_couverture_moyenne,
    ),
    (
        "produits_rupture",
        _det_produits_rupture,
        "SELECT nom_produit, id_entrepot FROM supply_couverture_stock "
        "WHERE statut_couverture = 'rupture' ORDER BY id_produit",
        _fmt_produits_rupture,
    ),
    (
        "pire_rotation",
        _det_pire_rotation,
        "SELECT famille, rotation_periode FROM offre_marge_familles "
        "ORDER BY rotation_periode ASC LIMIT 1",
        _fmt_pire_rotation,
    ),
    (
        "meilleures_marges",
        _det_meilleures_marges,
        "SELECT famille, taux_marge FROM offre_marge_familles "
        "ORDER BY taux_marge DESC LIMIT 3",
        _fmt_meilleures_marges,
    ),
    (
        "flop_produits",
        _det_flop_produits,
        "SELECT rang, nom_produit, quantite_vendue FROM offre_top_flop_produits "
        "WHERE type_classement = 'flop' ORDER BY rang LIMIT 5",
        _fmt_flop_produits,
    ),
    (
        "top_produits",
        _det_top_produits,
        "SELECT rang, nom_produit, chiffre_affaires FROM offre_top_flop_produits "
        "WHERE type_classement = 'top' ORDER BY rang LIMIT 5",
        _fmt_top_produits,
    ),
    (
        "otif_fournisseurs",
        _det_otif,
        "SELECT nom_fournisseur, taux_otif FROM supply_delais_fournisseurs "
        "ORDER BY taux_otif ASC LIMIT 3",
        _fmt_otif,
    ),
    (
        "ca_par_famille",
        _det_ca_famille,
        "SELECT famille, chiffre_affaires FROM offre_marge_familles "
        "ORDER BY chiffre_affaires DESC LIMIT 3",
        _fmt_ca_famille,
    ),
]


def _repondre_repli(question: str) -> dict:
    """Mode deterministe : question type reconnue par mots-cles."""
    q = _normaliser(question)
    for nom, detecteur, sql, formateur in QUESTIONS_TYPES:
        if detecteur(q):
            lignes = base.requete(sql)
            return {
                "mode": "repli",
                "question_type": nom,
                "sql": sql,
                "lignes": lignes,
                "reponse": formateur(lignes),
            }
    exemples = ", ".join(nom for nom, _, _, _ in QUESTIONS_TYPES)
    raise QuestionNonCompriseErreur(
        "Question non reconnue en mode repli. Questions types disponibles : "
        f"{exemples}."
    )


# ---------------------------------------------------------------------------
# Mode LLM
# ---------------------------------------------------------------------------


def _extraire_texte(message) -> str:
    """Concatene les blocs texte d'une reponse du client anthropic."""
    morceaux = []
    for bloc in message.content:
        texte = getattr(bloc, "text", None)
        if texte:
            morceaux.append(texte)
    return "\n".join(morceaux).strip()


def _nettoyer_sql(texte: str) -> str:
    """Retire les eventuelles balises de code autour du SQL genere."""
    texte = texte.strip()
    correspondance = re.search(r"```(?:sql)?\s*(.*?)```", texte, re.DOTALL)
    if correspondance:
        texte = correspondance.group(1)
    return texte.strip().rstrip(";").strip()


def _repondre_llm(question: str, client) -> dict:
    """Mode LLM : generation SQL, garde obligatoire, reformulation."""
    generation = client.messages.create(
        model=MODELE_LLM,
        max_tokens=600,
        system=PROMPT_SYSTEME_SQL,
        messages=[{"role": "user", "content": question}],
    )
    sql = _nettoyer_sql(_extraire_texte(generation))

    # Point de passage oblige : la garde SELECT only + allowlist.
    lignes = base.requete(sql)

    reformulation = client.messages.create(
        model=MODELE_LLM,
        max_tokens=400,
        system=PROMPT_SYSTEME_REPONSE,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Question : {question}\n"
                    f"Resultat (JSON) : {json.dumps(lignes, default=str, ensure_ascii=False)}"
                ),
            }
        ],
    )
    reponse = _extraire_texte(reformulation)

    return {
        "mode": "llm",
        "question_type": None,
        "sql": sql,
        "lignes": lignes,
        "reponse": reponse,
    }


def _creer_client():
    """Instancie le client anthropic (import differe pour le mode repli)."""
    import anthropic

    return anthropic.Anthropic()


# ---------------------------------------------------------------------------
# Journal d'audit
# ---------------------------------------------------------------------------


def chemin_journal() -> str:
    """Chemin du journal, surchargable par la variable AGENT_QA_JOURNAL."""
    return os.environ.get("AGENT_QA_JOURNAL", CHEMIN_JOURNAL_DEFAUT)


def _journaliser(entree: dict) -> None:
    """Ajoute une ligne JSON au journal des questions/reponses."""
    with open(chemin_journal(), "a", encoding="utf-8") as fichier:
        fichier.write(json.dumps(entree, ensure_ascii=False, default=str) + "\n")


# ---------------------------------------------------------------------------
# Point d'entree public
# ---------------------------------------------------------------------------


def repondre(question: str, utilisateur: str = "anonyme", client=None) -> dict:
    """Repond a une question metier en francais.

    Retourne un dict avec : mode (llm ou repli), sql, lignes, reponse.
    Journalise chaque question traitee (y compris les refus de la garde).
    """
    if not question or not question.strip():
        raise ValueError("La question ne peut pas etre vide.")

    mode_llm = client is not None or bool(os.environ.get("ANTHROPIC_API_KEY"))

    horodatage = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        if mode_llm:
            if client is None:
                client = _creer_client()
            resultat = _repondre_llm(question, client)
        else:
            resultat = _repondre_repli(question)
    except Exception as erreur:
        _journaliser(
            {
                "horodatage": horodatage,
                "utilisateur": utilisateur,
                "question": question,
                "mode": "llm" if mode_llm else "repli",
                "statut": "erreur",
                "erreur": f"{type(erreur).__name__}: {erreur}",
            }
        )
        raise

    _journaliser(
        {
            "horodatage": horodatage,
            "utilisateur": utilisateur,
            "question": question,
            "mode": resultat["mode"],
            "statut": "ok",
            "sql": resultat["sql"],
            "reponse": resultat["reponse"],
        }
    )
    return resultat
