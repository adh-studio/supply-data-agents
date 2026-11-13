"""Module pipeline : orchestration du build dbt et etat partage.

Ce module est le seul point d'entree pour reconstruire l'entrepot :
il lance dbt build dans le repo dbt (variable d'environnement DBT_DIR),
parse target/run_results.json, tient a jour etat_build.json (etat partage
entre SupplyOps et SupplySense) et, en cas d'echec, delegue le diagnostic
au gardien de qualite.
"""

from pipeline.moteur import executer_build, lire_etat

__all__ = ["executer_build", "lire_etat"]
