"""Moteur de l'alerte rupture.

Pipeline : lecture du mart supply_couverture_stock, selection des produits
en statut "rupture" (et "surstock" en option), construction d'un message
Slack en francais, puis diffusion sur trois canaux :

1. outbox locale : outbox/alerte-<horodatage>.md (toujours ecrite) ;
2. webhook generique si ALERTE_WEBHOOK_URL est defini (POST JSON, timeout
   court, 2 reessais, echec signale sans crash : surveillance anti-echec) ;
3. affichage CLI via python -m alerte_rupture (texte ou --json pour n8n).
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import time

import requests

from lib import base

JOURS_REAPPRO = 30
"""Horizon de reappro suggere : 30 jours de vente moyenne."""

DELAI_WEBHOOK_SECONDES = 5
"""Timeout court applique a chaque tentative d'envoi webhook."""

REESSAIS_WEBHOOK = 2
"""Nombre de reessais apres la premiere tentative (3 envois au total)."""

VARIABLE_WEBHOOK = "ALERTE_WEBHOOK_URL"


def _racine_projet() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def dossier_outbox() -> str:
    """Dossier de depot des alertes, surchargable par ALERTE_OUTBOX."""
    return os.environ.get(
        "ALERTE_OUTBOX", os.path.join(_racine_projet(), "outbox")
    )


def quantite_reappro(vente_moyenne_jour: float) -> int:
    """Quantite de reappro suggeree : vente moyenne jour x 30 jours, arrondie."""
    return round(float(vente_moyenne_jour) * JOURS_REAPPRO)


def recuperer_produits(inclure_surstock: bool = False) -> list[dict]:
    """Selectionne les produits en rupture (et surstock en option) du mart.

    Retourne une liste de dicts enrichis de la quantite de reappro suggeree,
    tries par statut puis par vente moyenne decroissante (les plus gros
    vendeurs en rupture d'abord).
    """
    statuts = "('rupture', 'surstock')" if inclure_surstock else "('rupture')"
    lignes = base.requete(
        "SELECT id_produit, nom_produit, famille, id_entrepot, nom_entrepot, "
        "quantite_disponible, vente_moyenne_jour, couverture_jours, "
        "statut_couverture "
        "FROM supply_couverture_stock "
        f"WHERE statut_couverture IN {statuts} "
        "ORDER BY statut_couverture, vente_moyenne_jour DESC"
    )
    for ligne in lignes:
        if ligne["statut_couverture"] == "rupture":
            ligne["quantite_reappro"] = quantite_reappro(
                ligne["vente_moyenne_jour"]
            )
        else:
            ligne["quantite_reappro"] = 0
    return lignes


def construire_message(produits: list[dict], date: datetime.date | None = None) -> str:
    """Construit le message d'alerte en francais, formate pour Slack.

    Blocs markdown : titre, tableau produits/entrepots/couverture, action
    recommandee avec la quantite de reappro suggeree par produit.
    """
    date = date or datetime.date.today()
    date_txt = date.strftime("%d/%m/%Y")
    ruptures = [p for p in produits if p["statut_couverture"] == "rupture"]
    surstocks = [p for p in produits if p["statut_couverture"] == "surstock"]

    if not ruptures and not surstocks:
        return (
            f":white_check_mark: *Couverture de stock - {date_txt}*\n\n"
            "Aucune rupture detectee aujourd'hui. Rien a signaler."
        )

    blocs: list[str] = []
    blocs.append(
        f":rotating_light: *Alerte rupture de stock - {date_txt}*\n"
        f"{len(ruptures)} produit(s) en rupture"
        + (f", {len(surstocks)} en surstock" if surstocks else "")
        + "."
    )

    if ruptures:
        lignes_tableau = [
            "| Produit | Entrepot | Vente moy./jour | Couverture (j) | Reappro suggeree |",
            "|---|---|---|---|---|",
        ]
        for p in ruptures:
            lignes_tableau.append(
                f"| {p['nom_produit']} | {p['nom_entrepot']} "
                f"| {p['vente_moyenne_jour']:.1f} "
                f"| {(p['couverture_jours'] or 0.0):.1f} "
                f"| {p['quantite_reappro']} unites |"
            )
        blocs.append("\n".join(lignes_tableau))

        actions = [
            "*Action recommandee* : lancer des commandes fournisseurs pour "
            f"couvrir {JOURS_REAPPRO} jours de vente :"
        ]
        for p in ruptures:
            actions.append(
                f"- {p['nom_produit']} ({p['nom_entrepot']}) : "
                f"commander {p['quantite_reappro']} unites"
            )
        blocs.append("\n".join(actions))

    if surstocks:
        lignes_surstock = [
            "*Surstocks a surveiller* (destockage ou mise en avant commerciale) :"
        ]
        for p in surstocks:
            lignes_surstock.append(
                f"- {p['nom_produit']} ({p['nom_entrepot']}) : "
                f"{(p['couverture_jours'] or 0.0):.0f} jours de couverture"
            )
        blocs.append("\n".join(lignes_surstock))

    return "\n\n".join(blocs)


def construire_payload(produits: list[dict], message: str) -> dict:
    """Payload machine (pour --json et le webhook) : ruptures + message."""
    ruptures = [p for p in produits if p["statut_couverture"] == "rupture"]
    surstocks = [p for p in produits if p["statut_couverture"] == "surstock"]
    return {
        "date": datetime.date.today().isoformat(),
        "nb_ruptures": len(ruptures),
        "nb_surstocks": len(surstocks),
        "ruptures": ruptures,
        "surstocks": surstocks,
        "message": message,
    }


def ecrire_outbox(message: str, dossier: str | None = None) -> str:
    """Ecrit l'alerte dans l'outbox locale et retourne le chemin du fichier."""
    dossier = dossier or dossier_outbox()
    os.makedirs(dossier, exist_ok=True)
    horodatage = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    chemin = os.path.join(dossier, f"alerte-{horodatage}.md")
    with open(chemin, "w", encoding="utf-8") as fichier:
        fichier.write(message + "\n")
    return chemin


def envoyer_webhook(
    payload: dict,
    url: str | None = None,
    reessais: int = REESSAIS_WEBHOOK,
    delai_entre_essais: float = 1.0,
) -> bool:
    """POST JSON vers le webhook, avec reessais et echec signale sans crash.

    Retourne True si un envoi a reussi, False sinon. Aucune exception ne
    remonte : la surveillance anti-echec se fait par le code retour et les
    messages sur stderr.
    """
    url = url or os.environ.get(VARIABLE_WEBHOOK)
    if not url:
        return False

    tentatives = 1 + max(0, int(reessais))
    for numero in range(1, tentatives + 1):
        try:
            reponse = requests.post(
                url, json=payload, timeout=DELAI_WEBHOOK_SECONDES
            )
            if 200 <= reponse.status_code < 300:
                return True
            print(
                f"Webhook : tentative {numero}/{tentatives} refusee "
                f"(HTTP {reponse.status_code}).",
                file=sys.stderr,
            )
        except requests.RequestException as erreur:
            print(
                f"Webhook : tentative {numero}/{tentatives} en echec "
                f"({erreur}).",
                file=sys.stderr,
            )
        if numero < tentatives:
            time.sleep(delai_entre_essais)

    print(
        "Webhook : tous les envois ont echoue. Alerte conservee dans "
        "l'outbox locale.",
        file=sys.stderr,
    )
    return False


def executer(
    inclure_surstock: bool = False,
    sortie_json: bool = False,
    delai_entre_essais: float = 1.0,
) -> dict:
    """Pipeline complet : lecture, message, outbox, webhook, affichage CLI.

    Retourne le payload enrichi des informations de diffusion.
    """
    produits = recuperer_produits(inclure_surstock=inclure_surstock)
    message = construire_message(produits)
    payload = construire_payload(produits, message)

    chemin_outbox = ecrire_outbox(message)
    payload["fichier_outbox"] = chemin_outbox

    url_webhook = os.environ.get(VARIABLE_WEBHOOK)
    if url_webhook:
        payload["webhook_envoye"] = envoyer_webhook(
            payload, url=url_webhook, delai_entre_essais=delai_entre_essais
        )
    else:
        payload["webhook_envoye"] = None

    if sortie_json:
        print(json.dumps(payload, ensure_ascii=False, default=str))
    else:
        print(message)
        print(f"\nAlerte ecrite dans : {chemin_outbox}", file=sys.stderr)

    return payload
