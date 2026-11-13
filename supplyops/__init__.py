"""SupplyOps : plateforme de saisie operationnelle des seeds CSV.

L'OPERATIONNEL ecrit (SupplyOps), l'ANALYTIQUE lit (SupplySense) : ce
module est le seul autorise a ecrire dans les seeds du repo dbt, avec
preservation stricte du CRLF, validation metier avant toute ecriture et
ecriture atomique (fichier temporaire puis rename). Demarrage du serveur :
python -m supplyops (port 4310).
"""
