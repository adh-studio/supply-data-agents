"""Pont vers Claude Code en mode headless (claude -p).

Permet d'utiliser l'abonnement Claude (plan Max) comme moteur de generation,
sans cle API ni facturation a l'usage : la commande `claude -p "..."` repond
en une passe sur le quota de l'abonnement.

Regles :
- Jamais actif pendant les tests (TEST_MODE=1), sauf si CLAUDE_CODE_FORCER=1
  est pose par un test qui bouchonne subprocess.
- Desactivable partout avec CLAUDE_CODE_DESACTIVE=1.
- Le modele est choisi par CLAUDE_CODE_MODELE (defaut : haiku, rapide et
  econome en quota) ; en cas de refus du modele, nouvel essai sans option.
"""

from __future__ import annotations

import os
import shutil
import subprocess

DELAI_SECONDES_DEFAUT = 120


class ClaudeCodeErreur(Exception):
    """Levee quand l'appel a la CLI claude echoue ou expire."""


def binaire() -> str | None:
    """Chemin de la CLI claude, ou None si absente."""
    return shutil.which("claude")


def disponible() -> bool:
    """La CLI claude peut-elle etre utilisee dans ce contexte ?"""
    if os.environ.get("CLAUDE_CODE_DESACTIVE") == "1":
        return False
    if os.environ.get("TEST_MODE") == "1" and os.environ.get("CLAUDE_CODE_FORCER") != "1":
        return False
    return binaire() is not None


def demander(prompt: str, delai: int = DELAI_SECONDES_DEFAUT) -> str:
    """Envoie un prompt a `claude -p` et retourne le texte de la reponse.

    Leve ClaudeCodeErreur si la CLI est absente, echoue ou expire.
    """
    chemin = binaire()
    if chemin is None:
        raise ClaudeCodeErreur("CLI claude introuvable dans le PATH.")

    modele = os.environ.get("CLAUDE_CODE_MODELE", "haiku")
    commande = [chemin, "-p", "--output-format", "text"]
    if modele:
        commande += ["--model", modele]
    commande.append(prompt)

    try:
        resultat = subprocess.run(
            commande, capture_output=True, text=True, timeout=delai
        )
    except subprocess.TimeoutExpired as erreur:
        raise ClaudeCodeErreur(f"claude -p a expire apres {delai} s.") from erreur

    if resultat.returncode != 0 and modele:
        # Le modele demande peut etre refuse selon l'abonnement : nouvel essai
        # avec le modele par defaut de la session.
        commande_sans_modele = [chemin, "-p", "--output-format", "text", prompt]
        try:
            resultat = subprocess.run(
                commande_sans_modele, capture_output=True, text=True, timeout=delai
            )
        except subprocess.TimeoutExpired as erreur:
            raise ClaudeCodeErreur(f"claude -p a expire apres {delai} s.") from erreur

    if resultat.returncode != 0:
        detail = (resultat.stderr or resultat.stdout or "").strip()[:300]
        raise ClaudeCodeErreur(f"claude -p a echoue (code {resultat.returncode}) : {detail}")

    texte = (resultat.stdout or "").strip()
    if not texte:
        raise ClaudeCodeErreur("claude -p a renvoye une reponse vide.")
    return texte
