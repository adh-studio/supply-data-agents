"""CLI du catalogue de data products : python -m catalogue.

Evalue les declarations du repo dbt (DBT_DIR, defaut
../supply-dbt-warehouse) : validation JSON Schema, references croisees
(marts, contrats, consommateurs) et etat vivant (fraicheur reelle contre
SLO, volumes, contrat, maturite). Code de sortie 1 des qu'un data
product est invalide, 0 sinon.
"""

from __future__ import annotations

import sys

from catalogue import moteur

ETIQUETTES = {
    moteur.STATUT_SAIN: "SAIN",
    moteur.STATUT_DEGRADE: "DEGRADE",
    moteur.STATUT_INVALIDE: "INVALIDE",
}


def principal() -> int:
    verdict = moteur.evaluer_tout()
    print(f"Catalogue de data products : {verdict['dossier']}")
    if not verdict["nb_data_products"]:
        print("Aucun data product declare : rien a evaluer.")
        return 0

    for data_product in verdict["data_products"]:
        etiquette = ETIQUETTES.get(data_product["statut"], data_product["statut"])
        owner = data_product.get("owner") or {}
        maturite = data_product["maturite"]
        print(
            f"\n[{etiquette}] {data_product['nom']} "
            f"v{data_product['version']} (domaine {data_product['domaine']}, "
            f"owner : {owner.get('nom', '?')})"
        )
        for port in data_product["ports_de_sortie"]:
            lignes = port["nb_lignes"]
            volume = f"{lignes} ligne(s)" if lignes is not None else "volume inconnu"
            print(f"  port {port['table']} : {volume}")
        fraicheur = data_product["fraicheur"]
        marque = "ok " if fraicheur["statut"] == "a_jour" else "KO "
        print(f"  {marque} fraicheur : {fraicheur['detail']}")
        contrat = data_product["contrat"]
        marque = "ok " if contrat.get("statut") == "conforme" else "KO "
        print(
            f"  {marque} contrat : {contrat.get('reference')} "
            f"({contrat.get('statut') or 'introuvable'})"
        )
        print(f"  maturite : {maturite['score']} / {maturite['sur']}")
        for probleme in data_product["problemes"]:
            print(f"  KO  {probleme}")

    print(
        f"\n{verdict['nb_domaines']} domaine(s), "
        f"{verdict['nb_data_products']} data product(s), "
        f"{verdict['nb_sains']} sain(s), "
        f"{verdict['nb_invalides']} invalide(s)."
    )
    if verdict["nb_invalides"]:
        print("Verdict : catalogue invalide, declarations a corriger.")
        return 1
    if verdict["tous_sains"]:
        print("Verdict : tous les data products sont sains.")
    else:
        print("Verdict : catalogue valide, mais au moins un data product degrade.")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
