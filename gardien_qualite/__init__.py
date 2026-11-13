"""Gardien de qualite des donnees.

Quand un test dbt echoue pendant le run de nuit, ce module lit le
run_results.json, categorise les echecs, construit un diagnostic en
francais et prepare la notification. L'humain valide ensuite.

Usage en ligne de commande :
    python -m gardien_qualite.moteur chemin/vers/run_results.json
"""

__all__ = ["moteur"]
