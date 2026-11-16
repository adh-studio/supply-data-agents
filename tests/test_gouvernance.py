"""Tests de la gouvernance : amendement de contrat en self-service.

Tout se joue sur un repo dbt factice temporaire : COPIES des fichiers
(schema.yml staging + marts, contrat ODCS, seeds CSV), jamais les vrais
fichiers du repo dbt. L'amendement doit mettre a jour TOUTES les listes
accepted_values de famille, le contrat (version + changelog) et le
journal des saisies, en preservant le format des fichiers.
"""

from __future__ import annotations

import datetime
import json
import os
import threading
import urllib.error
import urllib.request

import pytest
import yaml

from contrats import moteur as moteur_contrats
from cockpit import serveur as cockpit_serveur
from supplyops import gouvernance, magasin
from supplyops import serveur as ops_serveur

AUJOURDHUI = datetime.date.today().isoformat()

SEEDS_CRLF = {
    "produits.csv": (
        "id_produit,nom_produit,famille,id_fournisseur,prix_achat,prix_vente\r\n"
        "P0001,Console de test,Consoles et gaming,F001,2000000,2900000\r\n"
        "P0002,Casque de test,Audio,F001,90000,160000\r\n"
        "P0003,Barre de son,Audio,F001,250000,420000\r\n"
    ),
    "fournisseurs.csv": (
        "id_fournisseur,nom_fournisseur,pays,mode_fret,delai_contractuel_jours,note_rse\r\n"
        "F001,Shenzhen Electronics,Chine,AIR,12,B\r\n"
    ),
}

SCHEMA_STAGING = """\
version: 2

models:
  - name: stg_produits
    description: >
      Vue de staging du catalogue produits.
    columns:
      - name: id_produit
        description: Identifiant unique du produit.
        tests:
          - unique
          - not_null
      - name: famille
        description: Famille marchande de rattachement.
        tests:
          - not_null
          - accepted_values:
              values:
                - "Consoles et gaming"
                - "Audio"
      - name: prix_achat
        description: Cout de revient unitaire.
        tests:
          - not_null
"""

SCHEMA_MARTS_OFFRE = """\
version: 2

models:
  - name: offre_marge_familles
    columns:
      - name: famille
        description: Famille marchande.
        tests:
          - unique
          - not_null
          - accepted_values:
              values:
                - "Consoles et gaming"
                - "Audio"
      - name: taux_marge
        tests:
          - not_null

  - name: offre_top_flop_produits
    columns:
      - name: famille
        description: Famille marchande du produit (sans accepted_values).
        tests:
          - not_null
      - name: segment
        tests:
          - accepted_values:
              values: ["top", "flop"]
"""

CONTRAT_ODCS = """\
# Contrat de donnees ODCS de test : referentiel produits.
apiVersion: v3.0.2
kind: DataContract
id: referentiel-produits
name: Referentiel produits
version: 1.0.0
status: active

schema:
  - name: produits
    physicalName: seeds/produits.csv
    logicalType: object
    physicalType: csv
    properties:
      - name: id_produit
        logicalType: string
        required: true
        unique: true
        primaryKey: true
        primaryKeyPosition: 1
      - name: nom_produit
        logicalType: string
        required: true
      - name: famille
        logicalType: string
        required: true
        quality:
          - name: famille_du_contrat
            description: La famille appartient a la liste autorisee du contrat dbt.
            dimension: conformity
            type: custom
            engine: supplysense
            implementation:
              controle: valeurs_schema_dbt
              colonne: famille
              modele: stg_produits
              colonne_schema: famille
      - name: id_fournisseur
        logicalType: string
        required: true
      - name: prix_achat
        logicalType: integer
        required: true
      - name: prix_vente
        logicalType: integer
        required: true

team:
  - username: heni
    name: Heni
    role: owner
    description: Direction

customProperties:
  - property: source_saisie
    value: SupplyOps (port 4310), ecriture controlee des seeds
"""


@pytest.fixture()
def repo_gouvernance(tmp_path, monkeypatch):
    """Repo dbt factice complet : seeds, schema staging + marts, contrat."""
    repo = tmp_path / "repo-dbt"
    seeds = repo / "seeds"
    seeds.mkdir(parents=True)
    for nom, contenu in SEEDS_CRLF.items():
        (seeds / nom).write_bytes(contenu.encode("utf-8"))
    staging = repo / "models" / "staging"
    staging.mkdir(parents=True)
    (staging / "schema.yml").write_text(SCHEMA_STAGING, encoding="utf-8")
    marts = repo / "models" / "marts" / "offre"
    marts.mkdir(parents=True)
    (marts / "schema.yml").write_text(SCHEMA_MARTS_OFFRE, encoding="utf-8")
    contrats = repo / "contracts"
    contrats.mkdir()
    (contrats / "referentiel_produits.odcs.yaml").write_text(
        CONTRAT_ODCS, encoding="utf-8"
    )
    monkeypatch.setenv("DBT_DIR", str(repo))
    monkeypatch.delenv("OPS_SEEDS_DIR", raising=False)
    monkeypatch.delenv("OPS_SCHEMA_PATH", raising=False)
    return repo


def _texte(chemin) -> str:
    with open(chemin, encoding="utf-8") as fichier:
        return fichier.read()


# ---------------------------------------------------------------------------
# Insertion textuelle dans les listes accepted_values
# ---------------------------------------------------------------------------

class TestInsertionFamille:
    def test_inserer_preserve_le_format(self, repo_gouvernance):
        texte, nb = gouvernance.inserer_famille(SCHEMA_STAGING, "Drones")
        assert nb == 1
        assert '                - "Audio"\n                - "Drones"\n' in texte
        # Rien d'autre n'a bouge : seul l'ajout distingue les deux textes.
        assert texte.replace('                - "Drones"\n', "") == SCHEMA_STAGING

    def test_inserer_ignore_les_autres_colonnes(self):
        texte, nb = gouvernance.inserer_famille(SCHEMA_MARTS_OFFRE, "Drones")
        assert nb == 1  # une seule liste famille, segment non touche
        assert '"Drones"' in texte
        assert 'values: ["top", "flop"]' in texte

    def test_decouverte_des_fichiers(self, repo_gouvernance):
        fichiers = gouvernance.fichiers_accepted_values()
        relatifs = [f["relatif"] for f in fichiers]
        assert relatifs == [
            os.path.join("models", "marts", "offre", "schema.yml"),
            os.path.join("models", "staging", "schema.yml"),
        ]
        assert all(f["nb_listes"] == 1 for f in fichiers)


# ---------------------------------------------------------------------------
# Amendement complet : schema.yml partout + contrat + changelog
# ---------------------------------------------------------------------------

class TestAmendement:
    def test_amendement_met_tout_a_jour(self, repo_gouvernance):
        resultat = gouvernance.amender_famille("Drones")
        assert resultat["famille"] == "Drones"
        assert resultat["version_avant"] == "1.0.0"
        assert resultat["version_apres"] == "1.1.0"
        assert resultat["nb_listes_modifiees"] == 2

        # Toutes les listes accepted_values portent la famille.
        for relatif in (
            ("models", "staging", "schema.yml"),
            ("models", "marts", "offre", "schema.yml"),
        ):
            contenu = yaml.safe_load(
                _texte(os.path.join(repo_gouvernance, *relatif))
            )
            assert gouvernance._nb_listes_famille(contenu) >= 1
            assert "Drones" in _texte(os.path.join(repo_gouvernance, *relatif))

        # SupplyOps accepte desormais la famille (lecture dynamique).
        assert "Drones" in magasin.familles_autorisees()

        # Le contrat est passe en 1.1.0 avec une entree de changelog.
        contrat = yaml.safe_load(
            _texte(repo_gouvernance / "contracts" / "referentiel_produits.odcs.yaml")
        )
        assert str(contrat["version"]) == "1.1.0"
        changelog = [
            e["value"]
            for e in contrat["customProperties"]
            if e["property"] == "changelog"
        ]
        assert len(changelog) == 1
        assert changelog[0]["version"] == "1.1.0"
        assert changelog[0]["auteur"] == "SupplyOps"
        assert changelog[0]["date"] == AUJOURDHUI
        assert "Drones" in changelog[0]["description"]
        # Le commentaire d'entete du contrat est preserve.
        assert _texte(
            repo_gouvernance / "contracts" / "referentiel_produits.odcs.yaml"
        ).startswith("# Contrat de donnees ODCS de test")

    def test_le_validateur_expose_le_changelog(self, repo_gouvernance):
        gouvernance.amender_famille("Drones")
        verdict = moteur_contrats.valider_tout(str(repo_gouvernance))
        contrat = verdict["contrats"][0]
        assert contrat["version"] == "1.1.0"
        assert contrat["changelog"][0]["description"] == "Ajout de la famille Drones"
        # La regle famille_du_contrat lit le schema amende : un produit
        # Drones serait conforme (rien n'est viole ici).
        assert contrat["statut"] == "conforme"

    def test_deux_amendements_successifs(self, repo_gouvernance):
        gouvernance.amender_famille("Drones")
        resultat = gouvernance.amender_famille("Informatique")
        assert resultat["version_avant"] == "1.1.0"
        assert resultat["version_apres"] == "1.2.0"
        assert magasin.familles_autorisees() == [
            "Consoles et gaming", "Audio", "Drones", "Informatique",
        ]
        contrat = yaml.safe_load(
            _texte(repo_gouvernance / "contracts" / "referentiel_produits.odcs.yaml")
        )
        changelog = [
            e["value"]
            for e in contrat["customProperties"]
            if e["property"] == "changelog"
        ]
        assert len(changelog) == 2

    def test_doublon_refuse(self, repo_gouvernance):
        with pytest.raises(gouvernance.ErreurGouvernance, match="existe deja"):
            gouvernance.amender_famille("Audio")
        # Insensible a la casse.
        with pytest.raises(gouvernance.ErreurGouvernance, match="existe deja"):
            gouvernance.amender_famille("audio")
        # Rien n'a ete ecrit.
        assert "1.0.0" in _texte(
            repo_gouvernance / "contracts" / "referentiel_produits.odcs.yaml"
        )

    def test_nom_invalide_refuse(self, repo_gouvernance):
        for mauvais in ("", "   ", 'Drones "pirates"', "a" * 41, "<script>"):
            with pytest.raises(gouvernance.ErreurGouvernance):
                gouvernance.amender_famille(mauvais)
        assert "Drones" not in _texte(
            repo_gouvernance / "models" / "staging" / "schema.yml"
        )

    def test_verrou_build_refuse(self, repo_gouvernance):
        (repo_gouvernance / ".pipeline.lock").write_text("{}", encoding="utf-8")
        with pytest.raises(gouvernance.ErreurVerrouBuild, match="build"):
            gouvernance.amender_famille("Drones")

    def test_etat_familles(self, repo_gouvernance):
        etat = gouvernance.etat_familles()
        assert etat["familles"] == [
            {"nom": "Consoles et gaming", "nb_produits": 1},
            {"nom": "Audio", "nb_produits": 2},
        ]
        assert etat["contrat"]["version"] == "1.0.0"
        assert etat["version_apres_amendement"] == "1.1.0"
        assert etat["nb_listes_dbt"] == 2
        assert etat["build_en_cours"] is False


# ---------------------------------------------------------------------------
# API SupplyOps : GET et POST /api/gouvernance/familles
# ---------------------------------------------------------------------------

@pytest.fixture()
def serveur_ops(repo_gouvernance):
    with ops_serveur._verrou_publication:
        ops_serveur._publication["en_cours"] = False
        ops_serveur._publication["dernier_code"] = None
    instance = ops_serveur.creer_serveur(0)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{instance.server_address[1]}"
    finally:
        instance.shutdown()
        instance.server_close()


def appel(url, chemin, methode="GET", corps=None):
    requete = urllib.request.Request(
        url + chemin,
        method=methode,
        data=json.dumps(corps).encode("utf-8") if corps is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(requete, timeout=10) as reponse:
            return reponse.status, json.loads(reponse.read().decode("utf-8"))
    except urllib.error.HTTPError as erreur:
        return erreur.code, json.loads(erreur.read().decode("utf-8"))


class TestApiGouvernance:
    def test_get_expose_l_etat(self, serveur_ops):
        code, corps = appel(serveur_ops, "/api/gouvernance/familles")
        assert code == 200
        assert [f["nom"] for f in corps["familles"]] == [
            "Consoles et gaming", "Audio",
        ]
        assert corps["contrat"]["version"] == "1.0.0"
        assert corps["version_apres_amendement"] == "1.1.0"
        assert len(corps["contrats"]) == 1
        contrat = corps["contrats"][0]
        assert contrat["nom"] == "Referentiel produits"
        assert contrat["owner"] == "Heni - Direction"
        assert contrat["nb_regles"] >= 3
        assert "4300" in corps["url_contrats_supplysense"]

    def test_post_sans_confirmation_refuse(self, serveur_ops, repo_gouvernance):
        code, corps = appel(
            serveur_ops, "/api/gouvernance/familles", "POST",
            {"famille": "Drones"},
        )
        assert code == 400
        assert "Confirmation obligatoire" in corps["erreur"]
        assert "Drones" not in _texte(
            repo_gouvernance / "models" / "staging" / "schema.yml"
        )

    def test_post_amende_et_journalise(self, serveur_ops, repo_gouvernance):
        code, corps = appel(
            serveur_ops, "/api/gouvernance/familles", "POST",
            {"famille": "Drones", "confirme": True},
        )
        assert code == 201
        assert corps["version_apres"] == "1.1.0"
        assert sorted(corps["fichiers_dbt"]) == [
            os.path.join("models", "marts", "offre", "schema.yml"),
            os.path.join("models", "staging", "schema.yml"),
        ]

        # L'evenement est journalise pour la cloche SupplySense.
        with open(repo_gouvernance / "journal_ops.jsonl", encoding="utf-8") as f:
            lignes = [json.loads(l) for l in f if l.strip()]
        assert lignes[-1]["type"] == "contrat"
        assert lignes[-1]["action"] == "amendement"
        assert lignes[-1]["resume"] == (
            "Contrat referentiel_produits amende v1.1.0 : famille Drones ajoutee"
        )
        assert lignes[-1]["publie"] is False

        # La creation d'un produit Drones est maintenant acceptee.
        code, corps = appel(
            serveur_ops, "/api/produits", "POST",
            {
                "nom_produit": "Drone camera 4K",
                "famille": "Drones",
                "id_fournisseur": "F001",
                "prix_achat": 890000,
                "prix_vente": 1590000,
            },
        )
        assert code == 201

    def test_post_doublon_et_nom_invalide(self, serveur_ops):
        code, corps = appel(
            serveur_ops, "/api/gouvernance/familles", "POST",
            {"famille": "Audio", "confirme": True},
        )
        assert code == 400
        assert "existe deja" in corps["erreur"]

        code, corps = appel(
            serveur_ops, "/api/gouvernance/familles", "POST",
            {"famille": "   ", "confirme": True},
        )
        assert code == 400
        assert "obligatoire" in corps["erreur"]

    def test_post_refuse_pendant_un_build(self, serveur_ops, repo_gouvernance):
        (repo_gouvernance / ".pipeline.lock").write_text("{}", encoding="utf-8")
        code, corps = appel(
            serveur_ops, "/api/gouvernance/familles", "POST",
            {"famille": "Drones", "confirme": True},
        )
        assert code == 409
        assert "build" in corps["erreur"]


# ---------------------------------------------------------------------------
# Notification de l'amendement cote SupplySense
# ---------------------------------------------------------------------------

class TestNotificationAmendement:
    def test_l_amendement_devient_un_evenement_contrat(
        self, serveur_ops, repo_gouvernance
    ):
        appel(
            serveur_ops, "/api/gouvernance/familles", "POST",
            {"famille": "Drones", "confirme": True},
        )
        notifications = cockpit_serveur.calculer_notifications()
        contrats = [
            n for n in notifications["notifications"] if n["type"] == "contrat"
        ]
        assert len(contrats) == 1
        assert contrats[0]["action"] == "amendement"
        assert "famille Drones ajoutee" in contrats[0]["resume"]
        # L'amendement n'est pas une saisie en attente de publication.
        assert notifications["nb_en_attente"] == 0
