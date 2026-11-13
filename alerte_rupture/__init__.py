"""Module alerte_rupture : alerte quotidienne sur les ruptures de stock.

Lit le mart supply_couverture_stock via lib.base, detecte les produits en
rupture (et en surstock en option), puis diffuse une alerte en francais sur
trois canaux : outbox locale, webhook generique, affichage CLI.
"""

from alerte_rupture.moteur import (
    construire_message,
    construire_payload,
    ecrire_outbox,
    envoyer_webhook,
    executer,
    quantite_reappro,
    recuperer_produits,
)

__all__ = [
    "construire_message",
    "construire_payload",
    "ecrire_outbox",
    "envoyer_webhook",
    "executer",
    "quantite_reappro",
    "recuperer_produits",
]
