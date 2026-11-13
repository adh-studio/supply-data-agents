"""Interface en ligne de commande du module fiches_produits.

Usage :
    python -m fiches_produits generer --limite 3
    python -m fiches_produits approuver P001
"""

from __future__ import annotations

import argparse
import sys

from fiches_produits.moteur import approuver, generer


def principal(arguments: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(
        prog="fiches_produits",
        description=(
            "Generation de fiches produits avec garde-fous et validation "
            "humaine. Rien n'est publie sans approbation explicite."
        ),
    )
    sous_commandes = analyseur.add_subparsers(dest="commande", required=True)

    commande_generer = sous_commandes.add_parser(
        "generer", help="Genere des fiches et les depose en a_valider/."
    )
    commande_generer.add_argument(
        "--limite",
        type=int,
        default=3,
        help="Nombre de produits a documenter (defaut : 3).",
    )

    commande_approuver = sous_commandes.add_parser(
        "approuver",
        help="Valide une fiche : la bascule de a_valider/ vers validees/.",
    )
    commande_approuver.add_argument(
        "id_produit", help="Identifiant du produit, par exemple P001."
    )

    options = analyseur.parse_args(arguments)

    if options.commande == "generer":
        resultats = generer(limite=options.limite)
        for resultat in resultats:
            if resultat["statut"] == "rejetee":
                motifs = " ; ".join(resultat["motifs"])
                print(
                    f"[REJETEE] {resultat['id_produit']} "
                    f"(mode {resultat['mode']}) : {motifs}"
                )
            else:
                print(
                    f"[EN ATTENTE] {resultat['id_produit']} "
                    f"(mode {resultat['mode']}) -> {resultat['chemin']}"
                )
        nb_attente = sum(1 for r in resultats if r["statut"] == "en_attente")
        print(
            f"{nb_attente} fiche(s) deposee(s) en zone a_valider/. "
            "Validation humaine requise avant toute publication."
        )
        return 0

    if options.commande == "approuver":
        try:
            chemin = approuver(options.id_produit)
        except FileNotFoundError as erreur:
            print(str(erreur), file=sys.stderr)
            return 1
        print(f"Fiche approuvee : {chemin}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(principal())
