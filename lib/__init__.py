"""Librairie commune des agents supply : acces en lecture seule a l'entrepot."""

from lib.base import MARTS_AUTORISES, RequeteInterditeErreur, connexion, requete

__all__ = ["MARTS_AUTORISES", "RequeteInterditeErreur", "connexion", "requete"]
