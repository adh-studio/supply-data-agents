"""Rapport hebdomadaire supply et offre pour les Directions Metiers.

La synthese du lundi matin : les KPI de la semaine calcules depuis les
5 marts de l'entrepot analytique, puis deux sorties generees :

- rapport.md : markdown pret a coller dans un email ou un message Slack ;
- docs/rapport-hebdo.html : page HTML autonome (CSS et SVG inline),
  lisible par un dirigeant en 2 minutes.

Lancement manuel :

    python -m rapport_hebdo
    python -m rapport_hebdo --sortie /chemin/du/dossier

Exemples de planification (chaque lundi a 7 h 30)
-------------------------------------------------

1. cron (Linux / macOS) :

    30 7 * * 1 cd /opt/supply-data-agents && \
        DUCKDB_PATH=/data/supply.duckdb .venv/bin/python -m rapport_hebdo

2. n8n : un noeud "Schedule Trigger" (Cron : 30 7 * * 1) suivi d'un noeud
   "Execute Command" qui lance `python -m rapport_hebdo`, puis d'un noeud
   Slack ou Email qui envoie le contenu de rapport.md.

3. launchd (macOS) : un fichier
   ~/Library/LaunchAgents/fr.supply.rapport-hebdo.plist avec
   StartCalendarInterval (Weekday = 1, Hour = 7, Minute = 30) et
   ProgramArguments pointant vers `python -m rapport_hebdo`.
"""

from rapport_hebdo.moteur import calculer_kpi, generer_rapport

__all__ = ["calculer_kpi", "generer_rapport"]
