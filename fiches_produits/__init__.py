"""Module fiches_produits : generation assistee de fiches produits e-commerce.

Pipeline : lecture des marts (offre_top_flop_produits, supply_couverture_stock),
generation d'une fiche par produit (mode LLM claude-sonnet-5 si cle disponible,
sinon gabarit deterministe), garde-fous systematiques apres generation, puis
depot en zone a_valider/ en attente d'une validation humaine explicite.

Rien n'est jamais publie sans le geste d'approbation :
python -m fiches_produits approuver <id>
"""

from fiches_produits.moteur import approuver, generer, lire_produits

__all__ = ["generer", "approuver", "lire_produits"]
