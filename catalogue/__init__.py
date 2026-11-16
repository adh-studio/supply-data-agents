"""Catalogue de data products vivant : les marts declares cote producteur.

Le module moteur charge les declarations YAML du repo dbt (catalogue/),
les valide contre leur JSON Schema, verifie les references croisees
(marts de l'entrepot, contrats ODCS, consommateurs connus) et calcule
l'etat vivant de chaque data product : fraicheur reelle contre SLO,
volumes, statut du contrat lie et score de maturite.
"""
