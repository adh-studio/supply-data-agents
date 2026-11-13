"""Agent question/reponse self-service sur l'entrepot analytique.

Poser une question metier en francais, obtenir une reponse chiffree,
sans jamais toucher les donnees brutes : chaque requete passe par la
garde de securite lib.base.requete (SELECT uniquement, marts autorises).
"""

from agent_qa.moteur import QuestionNonCompriseErreur, repondre

__all__ = ["repondre", "QuestionNonCompriseErreur"]
