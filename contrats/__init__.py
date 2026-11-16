"""Contrats de donnees vivants : validation des seeds CSV contre les
contrats ODCS v3 (standard Bitol) publies dans le repo dbt (contracts/).

Le module moteur charge les contrats, valide chaque seed (schema, regles
de qualite, integrite croisee, fraicheur SLA) et rend un verdict par
contrat. Le pipeline refuse tout build quand un contrat est viole.
"""
