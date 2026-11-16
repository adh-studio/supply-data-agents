"""CLI des contrats de donnees : python -m contrats.

Valide les seeds du repo dbt (DBT_DIR, defaut ../supply-dbt-warehouse)
contre les contrats ODCS publies dans contracts/. Affichage lisible par
contrat (regles, fraicheur), code de sortie 0 si tout est conforme,
1 des qu'une regle ou un SLA est viole.
"""

from __future__ import annotations

import sys

from contrats import moteur


def principal() -> int:
    verdict = moteur.valider_tout()
    print(f"Contrats de donnees ODCS : {verdict['dossier']}")
    if not verdict["nb_contrats"]:
        print("Aucun contrat publie : rien a valider.")
        return 0

    for contrat in verdict["contrats"]:
        etat = "CONFORME" if contrat["statut"] == moteur.STATUT_CONFORME else "VIOLE"
        print(
            f"\n[{etat}] {contrat['nom']} v{contrat['version']} "
            f"(owner : {contrat['owner']})"
        )
        for regle in contrat["regles"]:
            marque = "ok " if regle["statut"] == moteur.STATUT_CONFORME else "KO "
            ligne = f"  {marque} {regle['objet']}.{regle['nom']}"
            if regle["detail"]:
                ligne += f" : {regle['detail']}"
            print(ligne)
        fraicheur = contrat.get("fraicheur")
        if fraicheur:
            marque = (
                "ok " if fraicheur["statut"] == moteur.STATUT_CONFORME else "KO "
            )
            print(f"  {marque} SLA fraicheur : {fraicheur['detail']}")

    print(
        f"\n{verdict['nb_contrats']} contrat(s), {verdict['nb_regles']} "
        f"regle(s), {verdict['nb_violations']} violation(s)."
    )
    if verdict["conforme"]:
        print("Verdict : tous les contrats sont respectes.")
        return 0
    print("Verdict : contrats violes, publication a refuser.")
    return 1


if __name__ == "__main__":
    sys.exit(principal())
