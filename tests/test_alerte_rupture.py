"""Tests du module alerte_rupture contre la base fixture.

Aucun appel reseau : le webhook est entierement mocke.
"""

import json
import os
from unittest import mock

import requests

from alerte_rupture import moteur

CHEMIN_N8N = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "n8n",
    "alerte_rupture.json",
)

PRODUITS_EN_RUPTURE = {
    "Canape 3 places Oslo gris",
    "Lampadaire arc Milo noir",
    "Tapis berbere Atlas 160x230",
}


def _reponse(code: int) -> mock.Mock:
    reponse = mock.Mock()
    reponse.status_code = code
    return reponse


class TestDetectionRuptures:
    def test_trois_ruptures_dans_la_fixture(self):
        produits = moteur.recuperer_produits()
        assert len(produits) == 3
        assert {p["nom_produit"] for p in produits} == PRODUITS_EN_RUPTURE
        assert all(p["statut_couverture"] == "rupture" for p in produits)

    def test_surstock_inclus_en_option(self):
        produits = moteur.recuperer_produits(inclure_surstock=True)
        statuts = {p["statut_couverture"] for p in produits}
        assert statuts == {"rupture", "surstock"}
        surstocks = [p for p in produits if p["statut_couverture"] == "surstock"]
        assert len(surstocks) == 3
        # Les dormants ne doivent jamais remonter dans l'alerte.
        assert all(p["statut_couverture"] != "dormant" for p in produits)


class TestQuantiteReappro:
    def test_calcul_vente_moyenne_fois_trente_arrondi(self):
        assert moteur.quantite_reappro(4.2) == 126
        assert moteur.quantite_reappro(3.8) == 114
        assert moteur.quantite_reappro(2.9) == 87
        assert moteur.quantite_reappro(0.0) == 0

    def test_quantites_portees_par_les_produits(self):
        produits = moteur.recuperer_produits()
        quantites = {
            p["nom_produit"]: p["quantite_reappro"] for p in produits
        }
        assert quantites["Canape 3 places Oslo gris"] == 126
        assert quantites["Lampadaire arc Milo noir"] == 114
        assert quantites["Tapis berbere Atlas 160x230"] == 87


class TestMessage:
    def test_message_contient_les_noms_de_produits(self):
        produits = moteur.recuperer_produits()
        message = moteur.construire_message(produits)
        for nom in PRODUITS_EN_RUPTURE:
            assert nom in message

    def test_message_contient_titre_tableau_et_action(self):
        produits = moteur.recuperer_produits()
        message = moteur.construire_message(produits)
        assert "Alerte rupture de stock" in message
        assert "| Produit | Entrepot |" in message
        assert "Action recommandee" in message
        assert "126 unites" in message

    def test_message_sans_rupture_rassurant(self):
        message = moteur.construire_message([])
        assert "Aucune rupture" in message


class TestOutbox:
    def test_fichier_ecrit_dans_le_dossier(self, tmp_path):
        chemin = moteur.ecrire_outbox("contenu d'alerte", dossier=str(tmp_path))
        assert os.path.exists(chemin)
        assert os.path.basename(chemin).startswith("alerte-")
        assert chemin.endswith(".md")
        with open(chemin, encoding="utf-8") as fichier:
            assert "contenu d'alerte" in fichier.read()


class TestWebhook:
    def test_sans_url_retourne_false_sans_appel(self):
        with mock.patch.object(moteur.requests, "post") as poste:
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("ALERTE_WEBHOOK_URL", None)
                resultat = moteur.envoyer_webhook({"a": 1})
        assert resultat is False
        poste.assert_not_called()

    def test_succes_au_premier_essai(self):
        with mock.patch.object(
            moteur.requests, "post", return_value=_reponse(200)
        ) as poste:
            resultat = moteur.envoyer_webhook(
                {"a": 1}, url="https://exemple.fr/hook", delai_entre_essais=0
            )
        assert resultat is True
        assert poste.call_count == 1
        _, kwargs = poste.call_args
        assert kwargs["json"] == {"a": 1}
        assert kwargs["timeout"] == moteur.DELAI_WEBHOOK_SECONDES

    def test_echec_total_apres_trois_tentatives_sans_crash(self, capsys):
        with mock.patch.object(
            moteur.requests,
            "post",
            side_effect=requests.ConnectionError("hote injoignable"),
        ) as poste:
            resultat = moteur.envoyer_webhook(
                {"a": 1}, url="https://exemple.fr/hook", delai_entre_essais=0
            )
        assert resultat is False
        assert poste.call_count == 3  # 1 tentative + 2 reessais
        erreurs = capsys.readouterr().err
        assert "tous les envois ont echoue" in erreurs

    def test_reussite_apres_un_reessai(self):
        with mock.patch.object(
            moteur.requests,
            "post",
            side_effect=[
                requests.Timeout("delai depasse"),
                _reponse(500),
                _reponse(200),
            ],
        ) as poste:
            resultat = moteur.envoyer_webhook(
                {"a": 1}, url="https://exemple.fr/hook", delai_entre_essais=0
            )
        assert resultat is True
        assert poste.call_count == 3


class TestPipelineComplet:
    def test_executer_json_et_webhook_mocke(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("ALERTE_OUTBOX", str(tmp_path))
        monkeypatch.setenv("ALERTE_WEBHOOK_URL", "https://exemple.fr/hook")
        with mock.patch.object(
            moteur.requests, "post", return_value=_reponse(200)
        ) as poste:
            payload = moteur.executer(sortie_json=True, delai_entre_essais=0)

        assert payload["nb_ruptures"] == 3
        assert payload["webhook_envoye"] is True
        assert os.path.exists(payload["fichier_outbox"])
        assert poste.call_count == 1

        # La sortie --json doit etre du JSON machine valide pour n8n.
        sortie = capsys.readouterr().out
        decode = json.loads(sortie)
        assert decode["nb_ruptures"] == 3
        assert "Canape 3 places Oslo gris" in decode["message"]

    def test_executer_sans_webhook(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ALERTE_OUTBOX", str(tmp_path))
        monkeypatch.delenv("ALERTE_WEBHOOK_URL", raising=False)
        with mock.patch.object(moteur.requests, "post") as poste:
            payload = moteur.executer()
        assert payload["webhook_envoye"] is None
        poste.assert_not_called()


class TestWorkflowN8n:
    def test_json_valide(self):
        with open(CHEMIN_N8N, encoding="utf-8") as fichier:
            flux = json.load(fichier)
        assert isinstance(flux["nodes"], list)
        assert isinstance(flux["connections"], dict)

    def test_nodes_attendus_presents(self):
        with open(CHEMIN_N8N, encoding="utf-8") as fichier:
            flux = json.load(fichier)
        types = {noeud["type"] for noeud in flux["nodes"]}
        assert "n8n-nodes-base.scheduleTrigger" in types
        assert "n8n-nodes-base.executeCommand" in types
        assert "n8n-nodes-base.if" in types
        assert "n8n-nodes-base.slack" in types
        commandes = [
            noeud["parameters"].get("command", "")
            for noeud in flux["nodes"]
            if noeud["type"] == "n8n-nodes-base.executeCommand"
        ]
        assert any("python -m alerte_rupture --json" in c for c in commandes)
