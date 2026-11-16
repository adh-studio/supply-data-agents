"""Tests du bouton Tout valider : moteur approuver_tout + API du cockpit."""

import os

from fiches_produits import moteur as fiches
from tests.test_cockpit import appel, cockpit  # noqa: F401 (fixture pytest)


def _deposer_fiche(racine, id_produit, statut="en_attente"):
    dossier = os.path.join(racine, "a_valider" if statut == "en_attente" else "validees")
    os.makedirs(dossier, exist_ok=True)
    with open(os.path.join(dossier, f"{id_produit}.md"), "w", encoding="utf-8") as f:
        f.write(
            "---\n"
            f"id_produit: {id_produit}\n"
            f"nom_produit: Produit {id_produit}\n"
            f"statut: {statut}\n"
            "mode: gabarit\n"
            "genere_le: '2026-11-16'\n"
            "---\n"
            "Corps de la fiche.\n"
        )


def test_approuver_tout_valide_toutes_les_fiches_en_attente(tmp_path):
    racine = str(tmp_path)
    for idp in ("P0001", "P0002", "P0003"):
        _deposer_fiche(racine, idp)
    bilan = fiches.approuver_tout(racine=racine)
    assert bilan["nb"] == 3
    assert bilan["validees"] == ["P0001", "P0002", "P0003"]
    assert bilan["echecs"] == []
    for idp in ("P0001", "P0002", "P0003"):
        assert os.path.exists(os.path.join(racine, "validees", f"{idp}.md"))
        assert not os.path.exists(os.path.join(racine, "a_valider", f"{idp}.md"))
    contenu = open(os.path.join(racine, "validees", "P0001.md"), encoding="utf-8").read()
    assert "statut: validee" in contenu
    assert "approuvee_le:" in contenu


def test_approuver_tout_sans_fiche_en_attente(tmp_path):
    bilan = fiches.approuver_tout(racine=str(tmp_path))
    assert bilan["nb"] == 0
    assert bilan["validees"] == []


def test_api_approuver_tout(cockpit):
    """L'endpoint valide tout et publie un evenement dans les notifications."""

    racine = cockpit["racine"]
    for idp in ("P0010", "P0011"):
        _deposer_fiche(racine, idp)

    code, corps = appel(cockpit, "/api/fiches/approuver-tout", methode="POST", corps={})
    assert code == 200
    assert corps["ok"] is True
    assert corps["nb"] == 2
    assert os.path.exists(os.path.join(racine, "validees", "P0010.md"))

    code, notifs = appel(cockpit, "/api/notifications")
    assert code == 200
    types = [e.get("type") for e in notifs.get("notifications", [])]
    assert "fiches_validees" in types
