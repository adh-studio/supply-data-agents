"""Interface en ligne de commande du module fiches_produits.

Usage :
    python -m fiches_produits generer --limite 3
    python -m fiches_produits generer --produit P0043 [--remplacer]
    python -m fiches_produits generer --tous [--inclure-rejetees]
    python -m fiches_produits couverture
    python -m fiches_produits approuver P001
    python -m fiches_produits rejeter P001 --motif "description trop vague"
"""

from __future__ import annotations

import argparse
import sys

from fiches_produits.moteur import (
    FicheExistanteErreur,
    approuver,
    generer,
    generer_manquantes,
    generer_pour,
    lister_couverture,
    rejeter,
)


def _afficher_resultat(resultat: dict) -> None:
    """Affiche le compte rendu unitaire d'une generation."""
    if resultat["statut"] == "en_attente":
        print(
            f"[EN ATTENTE] {resultat['id_produit']} "
            f"(mode {resultat['mode']}) -> {resultat['chemin']}"
        )
        for chemin in resultat.get("archives") or []:
            print(f"  ancienne fiche archivee : {chemin}")
    else:
        motifs = " ; ".join(resultat["motifs"])
        etiquette = "ECHEC" if resultat["statut"] == "echec" else "REJETEE"
        mode = f" (mode {resultat['mode']})" if resultat.get("mode") else ""
        print(f"[{etiquette}] {resultat['id_produit']}{mode} : {motifs}")


def _commande_generer(options) -> int:
    if options.produit:
        try:
            resultat = generer_pour(options.produit, remplacer=options.remplacer)
        except (ValueError, FicheExistanteErreur) as erreur:
            print(str(erreur), file=sys.stderr)
            return 1
        _afficher_resultat(resultat)
        return 0 if resultat["statut"] == "en_attente" else 1

    if options.tous:
        def rappel(fait, total, produit):
            if produit is not None:
                print(
                    f"[{fait + 1}/{total}] {produit['id_produit']} "
                    f"{produit['nom_produit']}...",
                    flush=True,
                )

        bilan = generer_manquantes(
            inclure_rejetees=options.inclure_rejetees, rappel=rappel
        )
        for resultat in bilan["resultats"]:
            _afficher_resultat(resultat)
        print(
            f"Bilan : {bilan['generees']} generee(s), "
            f"{bilan['echouees']} echec(s), "
            f"{bilan['ignorees']} produit(s) deja couvert(s). "
            "Validation humaine requise avant toute publication."
        )
        return 0

    resultats = generer(limite=options.limite)
    for resultat in resultats:
        _afficher_resultat(resultat)
    nb_attente = sum(1 for r in resultats if r["statut"] == "en_attente")
    print(
        f"{nb_attente} fiche(s) deposee(s) en zone a_valider/. "
        "Validation humaine requise avant toute publication."
    )
    return 0


def _commande_couverture() -> int:
    lignes = lister_couverture()
    libelles = {
        "validee": "validee",
        "en_attente": "en attente",
        "rejetee": "rejetee",
        "aucune": "aucune",
    }
    largeur_nom = max((len(l["nom_produit"]) for l in lignes), default=10)
    largeur_fam = max((len(l["famille"] or "") for l in lignes), default=7)
    print(
        f"{'ID':<8} {'PRODUIT':<{largeur_nom}} {'FAMILLE':<{largeur_fam}} STATUT"
    )
    for ligne in lignes:
        print(
            f"{ligne['id_produit']:<8} {ligne['nom_produit']:<{largeur_nom}} "
            f"{(ligne['famille'] or ''):<{largeur_fam}} "
            f"{libelles.get(ligne['statut'], ligne['statut'])}"
        )
    couverts = sum(1 for l in lignes if l["statut"] != "aucune")
    print(
        f"\n{couverts} / {len(lignes)} produit(s) ont une fiche "
        f"({sum(1 for l in lignes if l['statut'] == 'validee')} validee(s), "
        f"{sum(1 for l in lignes if l['statut'] == 'en_attente')} en attente, "
        f"{sum(1 for l in lignes if l['statut'] == 'rejetee')} rejetee(s))."
    )
    return 0


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
    cibles = commande_generer.add_mutually_exclusive_group()
    cibles.add_argument(
        "--limite",
        type=int,
        default=3,
        help="Nombre de produits a documenter (defaut : 3).",
    )
    cibles.add_argument(
        "--produit",
        help="Genere la fiche d'un produit precis, par exemple P0043.",
    )
    cibles.add_argument(
        "--tous",
        action="store_true",
        help="Genere les fiches de tous les produits sans fiche.",
    )
    commande_generer.add_argument(
        "--remplacer",
        action="store_true",
        help=(
            "Avec --produit : archive la fiche en attente ou validee "
            "existante avant de regenerer."
        ),
    )
    commande_generer.add_argument(
        "--inclure-rejetees",
        action="store_true",
        help="Avec --tous : regenere aussi les produits au statut rejetee.",
    )

    sous_commandes.add_parser(
        "couverture",
        help="Tableau de couverture : statut de fiche de chaque produit.",
    )

    commande_approuver = sous_commandes.add_parser(
        "approuver",
        help="Valide une fiche : la bascule de a_valider/ vers validees/.",
    )
    commande_approuver.add_argument(
        "id_produit", help="Identifiant du produit, par exemple P001."
    )

    commande_rejeter = sous_commandes.add_parser(
        "rejeter",
        help="Rejette une fiche : la bascule de a_valider/ vers rejetees/.",
    )
    commande_rejeter.add_argument(
        "id_produit", help="Identifiant du produit, par exemple P001."
    )
    commande_rejeter.add_argument(
        "--motif", required=True, help="Motif du rejet (obligatoire)."
    )

    options = analyseur.parse_args(arguments)

    if options.commande == "generer":
        return _commande_generer(options)

    if options.commande == "couverture":
        return _commande_couverture()

    if options.commande == "approuver":
        try:
            chemin = approuver(options.id_produit)
        except FileNotFoundError as erreur:
            print(str(erreur), file=sys.stderr)
            return 1
        print(f"Fiche approuvee : {chemin}")
        return 0

    if options.commande == "rejeter":
        try:
            chemin = rejeter(options.id_produit, options.motif)
        except (FileNotFoundError, ValueError) as erreur:
            print(str(erreur), file=sys.stderr)
            return 1
        print(f"Fiche rejetee : {chemin}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(principal())
