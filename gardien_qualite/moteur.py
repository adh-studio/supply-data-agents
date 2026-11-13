"""Moteur du gardien de qualite : diagnostic des tests dbt en echec.

Entree : un run_results.json produit par dbt (chemin en argument).
Le moteur extrait les tests en echec, les categorise par regles a partir
du nom du test, et construit pour chacun un diagnostic en francais :
ce qui a casse, hypothese de cause, gravite, action proposee.

Si une base DuckDB est fournie (option --base), le moteur va chercher
jusqu'a 5 lignes fautives pour illustrer : requete compilee du test si
disponible, sinon echantillon de la table concernee.

Sorties :
- outbox/diagnostic-<horodatage>.md (rapport lisible pour l'humain) ;
- JSON sur la sortie standard (pour n8n) ;
- code de sortie 1 si au moins un echec bloquant, 0 sinon.

Partie LLM optionnelle : si ANTHROPIC_API_KEY est presente, l'hypothese
de cause est enrichie via le modele claude-sonnet-5 ; sinon les regles
deterministes suffisent. Aucun appel reseau sans cle.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

RACINE_PROJET = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTBOX_DEFAUT = os.path.join(RACINE_PROJET, "outbox")

# Les 5 marts connus de l'entrepot, pour retrouver la table concernee
# a partir du nom du test.
MARTS_CONNUS = (
    "supply_couverture_stock",
    "supply_taux_rupture",
    "supply_delais_fournisseurs",
    "offre_marge_familles",
    "offre_top_flop_produits",
)

CATEGORIE_CLE_DUPLIQUEE = "cle dupliquee"
CATEGORIE_VALEUR_NULLE = "valeur nulle"
CATEGORIE_HORS_BORNES = "valeur hors bornes"
CATEGORIE_RELATION_ORPHELINE = "relation orpheline"
CATEGORIE_SINGULIER_METIER = "test singulier metier"

GRAVITE_BLOQUANT = "bloquant"
GRAVITE_AVERTISSEMENT = "avertissement"

# Regles de categorisation : motifs presents dans le nom du test dbt.
_REGLES = (
    (("unique_", "dbt_utils_unique_combination",), CATEGORIE_CLE_DUPLIQUEE),
    (("not_null_",), CATEGORIE_VALEUR_NULLE),
    (
        (
            "accepted_values_",
            "accepted_range_",
            "_between_",
            "hors_bornes",
        ),
        CATEGORIE_HORS_BORNES,
    ),
    (("relationships_",), CATEGORIE_RELATION_ORPHELINE),
)

_HYPOTHESES = {
    CATEGORIE_CLE_DUPLIQUEE: (
        "Fichier source probablement mal forme : doublon introduit a "
        "l'ingestion (double export, rejeu de chargement) plutot que vraie "
        "anomalie metier."
    ),
    CATEGORIE_VALEUR_NULLE: (
        "Fichier source probablement mal forme : colonne vide ou decalage "
        "de colonnes dans l'export amont ; a confirmer avant de suspecter "
        "une anomalie metier."
    ),
    CATEGORIE_HORS_BORNES: (
        "Vraie anomalie metier probable : valeur saisie ou calculee hors du "
        "domaine attendu (verifier aussi une eventuelle erreur d'unite dans "
        "le fichier source)."
    ),
    CATEGORIE_RELATION_ORPHELINE: (
        "Fichier source probablement mal forme ou desynchronise : la cle "
        "pointe vers un referentiel qui ne la connait pas (chargement "
        "partiel ou ordre de chargement inverse)."
    ),
    CATEGORIE_SINGULIER_METIER: (
        "Vraie anomalie metier probable : la regle de gestion encodee dans "
        "le test n'est plus respectee par les donnees du jour."
    ),
}

_ACTIONS = {
    CATEGORIE_CLE_DUPLIQUEE: (
        "Bloquer la publication du mart, dedupliquer la source (garder la "
        "ligne la plus recente) et relancer le run dbt apres validation "
        "humaine."
    ),
    CATEGORIE_VALEUR_NULLE: (
        "Verifier le fichier source du jour (colonnes et separateurs), "
        "completer ou exclure les lignes incompletes, puis relancer le test."
    ),
    CATEGORIE_HORS_BORNES: (
        "Faire valider les valeurs par le metier (achats ou supply) ; "
        "corriger la saisie ou ajuster les bornes du test si la regle a "
        "evolue."
    ),
    CATEGORIE_RELATION_ORPHELINE: (
        "Bloquer la publication du mart, recharger le referentiel manquant "
        "ou corriger les cles orphelines, puis relancer le run dbt."
    ),
    CATEGORIE_SINGULIER_METIER: (
        "Transmettre le diagnostic au responsable metier pour arbitrage ; "
        "documenter la decision (correction des donnees ou evolution de la "
        "regle)."
    ),
}


def categoriser(nom_test: str) -> tuple[str, str]:
    """Retourne (categorie, gravite) a partir du nom d'un test dbt."""
    nom = (nom_test or "").lower()
    categorie = CATEGORIE_SINGULIER_METIER
    for motifs, cat in _REGLES:
        if any(motif in nom for motif in motifs):
            categorie = cat
            break
    if categorie in (CATEGORIE_CLE_DUPLIQUEE, CATEGORIE_RELATION_ORPHELINE):
        gravite = GRAVITE_BLOQUANT
    else:
        gravite = GRAVITE_AVERTISSEMENT
    return categorie, gravite


def _nom_court(unique_id: str) -> str:
    """Extrait le nom lisible du test depuis son unique_id dbt."""
    morceaux = (unique_id or "").split(".")
    # Format dbt : test.<projet>.<nom_du_test>.<hash>
    if len(morceaux) >= 3:
        return morceaux[2]
    return unique_id or "test inconnu"


def _table_concernee(nom_test: str, relation_name: str | None = None) -> str | None:
    """Retrouve le mart concerne par le test, si identifiable."""
    sources = [(nom_test or "").lower()]
    if relation_name:
        sources.append(relation_name.lower())
    for texte in sources:
        for mart in MARTS_CONNUS:
            if mart in texte:
                return mart
    return None


def _lignes_fautives(
    chemin_base: str,
    compiled_code: str | None,
    table: str | None,
    limite: int = 5,
) -> list[dict]:
    """Recupere jusqu'a `limite` lignes pour illustrer l'echec.

    Essaie d'abord la requete compilee du test ; si elle est absente,
    invalide ou ne retourne rien, retombe sur un echantillon de la table.
    """
    try:
        import duckdb
    except ImportError:  # pragma: no cover - duckdb fait partie du socle
        return []

    if not os.path.exists(chemin_base):
        return []

    lignes: list[dict] = []
    try:
        con = duckdb.connect(chemin_base, read_only=True)
    except Exception:
        return []
    try:
        if compiled_code and compiled_code.strip().split(None, 1)[0].lower() in (
            "select",
            "with",
        ):
            try:
                resultat = con.execute(
                    f"SELECT * FROM ({compiled_code.strip().rstrip(';')}) "
                    f"AS fautives LIMIT {limite}"
                )
                colonnes = [d[0] for d in resultat.description]
                lignes = [dict(zip(colonnes, l)) for l in resultat.fetchall()]
            except Exception:
                lignes = []
        if not lignes and table:
            try:
                resultat = con.execute(
                    f'SELECT * FROM "{table}" LIMIT {limite}'
                )
                colonnes = [d[0] for d in resultat.description]
                lignes = [dict(zip(colonnes, l)) for l in resultat.fetchall()]
            except Exception:
                lignes = []
    finally:
        con.close()
    return lignes


def _enrichir_hypothese_llm(diagnostics: list[dict]) -> None:
    """Enrichit l'hypothese de cause via l'API Anthropic si une cle existe.

    Sans ANTHROPIC_API_KEY, ne fait rien : le mode deterministe par regles
    reste la reference. Toute erreur reseau ou d'import est silencieuse.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return
    try:
        import anthropic

        client = anthropic.Anthropic()
        for diagnostic in diagnostics:
            consigne = (
                "Tu es un gardien de qualite des donnees d'une enseigne de "
                "decoration et d'ameublement. Un test dbt a echoue.\n"
                f"Test : {diagnostic['nom_test']}\n"
                f"Categorie : {diagnostic['categorie']}\n"
                f"Constat : {diagnostic['ce_qui_a_casse']}\n"
                f"Hypothese initiale : {diagnostic['hypothese_cause']}\n"
                "Affine cette hypothese de cause en 2 phrases maximum, en "
                "francais, en tranchant entre fichier source mal forme et "
                "vraie anomalie metier."
            )
            reponse = client.messages.create(
                model="claude-sonnet-5",
                max_tokens=300,
                messages=[{"role": "user", "content": consigne}],
            )
            texte = "".join(
                bloc.text for bloc in reponse.content if hasattr(bloc, "text")
            ).strip()
            if texte:
                diagnostic["hypothese_cause"] = texte
                diagnostic["hypothese_enrichie_llm"] = True
    except Exception:
        # Repli deterministe : les hypotheses par regles restent en place.
        return


def charger_run_results(chemin: str) -> dict:
    """Charge et valide sommairement un run_results.json de dbt."""
    with open(chemin, encoding="utf-8") as fichier:
        contenu = json.load(fichier)
    if "results" not in contenu or not isinstance(contenu["results"], list):
        raise ValueError(
            f"Fichier {chemin} invalide : cle 'results' absente ou mal formee."
        )
    return contenu


def analyser(chemin_run_results: str, chemin_base: str | None = None) -> dict:
    """Analyse un run_results.json et retourne le rapport complet.

    Le rapport contient la liste des diagnostics (vide si tout est vert),
    les compteurs et les metadonnees du run.
    """
    contenu = charger_run_results(chemin_run_results)
    metadonnees = contenu.get("metadata", {})

    diagnostics: list[dict] = []
    nb_tests = 0
    for resultat in contenu["results"]:
        unique_id = resultat.get("unique_id", "")
        if not unique_id.startswith("test."):
            continue
        nb_tests += 1
        statut = (resultat.get("status") or "").lower()
        if statut not in ("fail", "error"):
            continue

        nom_test = _nom_court(unique_id)
        categorie, gravite = categoriser(nom_test)
        table = _table_concernee(nom_test, resultat.get("relation_name"))
        nb_fautives = resultat.get("failures")

        constat_table = f" sur le mart {table}" if table else ""
        if nb_fautives:
            constat = (
                f"Le test {nom_test}{constat_table} a echoue : "
                f"{nb_fautives} ligne(s) en violation."
            )
        else:
            constat = (
                f"Le test {nom_test}{constat_table} a echoue "
                f"(statut dbt : {statut})."
            )
        message = resultat.get("message")
        if message:
            constat += f" Message dbt : {message}"

        lignes_exemples: list[dict] = []
        if chemin_base:
            lignes_exemples = _lignes_fautives(
                chemin_base,
                resultat.get("compiled_code"),
                table,
            )

        diagnostics.append(
            {
                "unique_id": unique_id,
                "nom_test": nom_test,
                "table": table,
                "categorie": categorie,
                "gravite": gravite,
                "nb_lignes_fautives": nb_fautives,
                "ce_qui_a_casse": constat,
                "hypothese_cause": _HYPOTHESES[categorie],
                "action_proposee": _ACTIONS[categorie],
                "lignes_exemples": lignes_exemples,
            }
        )

    _enrichir_hypothese_llm(diagnostics)

    nb_bloquants = sum(
        1 for d in diagnostics if d["gravite"] == GRAVITE_BLOQUANT
    )
    return {
        "genere_le": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": os.path.abspath(chemin_run_results),
        "dbt_version": metadonnees.get("dbt_version"),
        "invocation_id": metadonnees.get("invocation_id"),
        "nb_tests_examines": nb_tests,
        "nb_echecs": len(diagnostics),
        "nb_bloquants": nb_bloquants,
        "statut_global": "echec" if diagnostics else "ok",
        "diagnostics": diagnostics,
    }


def generer_markdown(rapport: dict) -> str:
    """Construit le rapport markdown destine a la validation humaine."""
    lignes = [
        "# Diagnostic qualite des donnees",
        "",
        f"Genere le : {rapport['genere_le']}",
        f"Source : {rapport['source']}",
        f"Tests examines : {rapport['nb_tests_examines']}",
        f"Echecs : {rapport['nb_echecs']} "
        f"(dont bloquants : {rapport['nb_bloquants']})",
        "",
    ]
    if not rapport["diagnostics"]:
        lignes += [
            "Aucun test en echec : rien a signaler, le run de nuit est sain.",
            "",
        ]
        return "\n".join(lignes)

    lignes += [
        "Chaque diagnostic ci-dessous attend une validation humaine avant "
        "toute action corrective.",
        "",
    ]
    for indice, diag in enumerate(rapport["diagnostics"], start=1):
        lignes += [
            f"## {indice}. {diag['nom_test']}",
            "",
            f"- Gravite : **{diag['gravite']}**",
            f"- Categorie : {diag['categorie']}",
            f"- Mart concerne : {diag['table'] or 'non identifie'}",
            f"- Ce qui a casse : {diag['ce_qui_a_casse']}",
            f"- Hypothese de cause : {diag['hypothese_cause']}",
            f"- Action proposee : {diag['action_proposee']}",
            "",
        ]
        if diag["lignes_exemples"]:
            colonnes = list(diag["lignes_exemples"][0].keys())
            lignes.append(
                "Exemples de lignes pour illustrer (5 au maximum) :"
            )
            lignes.append("")
            lignes.append("| " + " | ".join(colonnes) + " |")
            lignes.append("| " + " | ".join("---" for _ in colonnes) + " |")
            for ligne in diag["lignes_exemples"]:
                valeurs = [str(ligne.get(c, "")) for c in colonnes]
                lignes.append("| " + " | ".join(valeurs) + " |")
            lignes.append("")
    return "\n".join(lignes)


def ecrire_rapport(rapport: dict, dossier_outbox: str) -> str:
    """Ecrit le markdown dans l'outbox et retourne son chemin."""
    os.makedirs(dossier_outbox, exist_ok=True)
    horodatage = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    chemin = os.path.join(dossier_outbox, f"diagnostic-{horodatage}.md")
    with open(chemin, "w", encoding="utf-8") as fichier:
        fichier.write(generer_markdown(rapport))
    return chemin


def main(argv: list[str] | None = None) -> int:
    """Point d'entree ligne de commande du gardien de qualite."""
    analyseur = argparse.ArgumentParser(
        description=(
            "Diagnostique les tests dbt en echec d'un run_results.json et "
            "prepare la notification pour validation humaine."
        )
    )
    analyseur.add_argument(
        "run_results",
        help="Chemin du run_results.json produit par dbt.",
    )
    analyseur.add_argument(
        "--base",
        default=None,
        help=(
            "Chemin d'une base DuckDB pour illustrer les echecs avec "
            "jusqu'a 5 lignes fautives."
        ),
    )
    analyseur.add_argument(
        "--outbox",
        default=OUTBOX_DEFAUT,
        help="Dossier de sortie du rapport markdown (defaut : outbox/).",
    )
    options = analyseur.parse_args(argv)

    rapport = analyser(options.run_results, chemin_base=options.base)
    chemin_markdown = ecrire_rapport(rapport, options.outbox)
    rapport["rapport_markdown"] = chemin_markdown

    # Sortie JSON pour n8n sur stdout.
    print(json.dumps(rapport, ensure_ascii=False, default=str, indent=2))

    # Code non nul si au moins un echec bloquant : fait rougir la CI
    # ou declenche le scenario n8n de notification.
    return 1 if rapport["nb_bloquants"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
