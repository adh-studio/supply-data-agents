"""Tests de la boucle de notification : journal des saisies, impact, API.

Tout est bouchonne : seeds CSV temporaires pour les ecritures SupplyOps,
bases DuckDB de fixture pour les photographies avant/apres, commande de
build factice pour le pipeline (jamais de vrai dbt), serveur cockpit sur
un port libre pour GET /api/notifications. Aucun reseau.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
import threading
import urllib.error
import urllib.request

import duckdb
import pytest

from cockpit import serveur as cockpit_serveur
from pipeline import moteur
from supplyops import serveur as ops_serveur

DOSSIER_FIXTURES_GARDIEN = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "gardien_qualite",
    "fixtures",
)
RUN_VERT = os.path.join(DOSSIER_FIXTURES_GARDIEN, "run_results_vert.json")

SEEDS_CRLF = {
    "produits.csv": (
        "id_produit,nom_produit,famille,id_fournisseur,prix_achat,prix_vente\r\n"
        "P0001,Casque sans fil,Audio,F001,90000,160000\r\n"
        "P0002,Lampe solaire,Eclairage LED,F001,40000,95000\r\n"
    ),
    "fournisseurs.csv": (
        "id_fournisseur,nom_fournisseur,pays,mode_fret,delai_contractuel_jours,note_rse\r\n"
        "F001,Shenzhen Electronics,Chine,AIR,12,B\r\n"
    ),
    "entrepots.csv": (
        "id_entrepot,nom_entrepot,ville,capacite_m3,type_entrepot\r\n"
        "E01,Depot central Ankorondrano,Antananarivo,1200,central\r\n"
    ),
    "commandes_achats.csv": (
        "id_commande,id_produit,id_fournisseur,id_entrepot,date_commande,"
        "quantite_commandee,statut\r\n"
        "CA0001,P0001,F001,E01,2026-10-01,20,en_cours\r\n"
    ),
    "receptions.csv": (
        "id_reception,id_commande,date_reception,quantite_recue\r\n"
    ),
    "stocks.csv": (
        "id_produit,id_entrepot,date_inventaire,quantite_disponible\r\n"
        "P0001,E01,2026-11-01,5\r\n"
    ),
    "ventes.csv": (
        "id_vente,date_vente,id_produit,id_entrepot,canal,quantite,prix_unitaire\r\n"
    ),
}

SCHEMA_YML = """\
version: 2

models:
  - name: stg_produits
    columns:
      - name: famille
        tests:
          - not_null
          - accepted_values:
              values:
                - "Audio"
                - "Eclairage LED"
"""

SCRIPT_FACTICE = textwrap.dedent(
    """\
    import os, shutil, sys

    source, code = sys.argv[1], int(sys.argv[2])
    os.makedirs("target", exist_ok=True)
    shutil.copy(source, os.path.join("target", "run_results.json"))
    # Simule un build qui reconstruit l'entrepot : la base "apres"
    # remplace supply.duckdb.
    if len(sys.argv) > 3:
        shutil.copy(sys.argv[3], "supply.duckdb")
    sys.exit(code)
    """
)


@pytest.fixture()
def repo_factice(tmp_path, monkeypatch):
    """Repo dbt factice : seeds CRLF, contrat minimal, journal vierge."""
    repo = tmp_path / "repo-dbt"
    seeds = repo / "seeds"
    seeds.mkdir(parents=True)
    for nom, contenu in SEEDS_CRLF.items():
        (seeds / nom).write_bytes(contenu.encode("utf-8"))
    staging = repo / "models" / "staging"
    staging.mkdir(parents=True)
    (staging / "schema.yml").write_text(SCHEMA_YML, encoding="utf-8")
    monkeypatch.setenv("DBT_DIR", str(repo))
    monkeypatch.setenv("PIPELINE_OUTBOX", str(tmp_path / "outbox"))
    monkeypatch.delenv("OPS_SEEDS_DIR", raising=False)
    monkeypatch.delenv("OPS_SCHEMA_PATH", raising=False)
    return repo


def _lire_journal(repo) -> list[dict]:
    chemin = repo / "journal_ops.jsonl"
    if not chemin.exists():
        return []
    return [
        json.loads(ligne)
        for ligne in chemin.read_text(encoding="utf-8").splitlines()
        if ligne.strip()
    ]


def appel(url, chemin, methode="GET", corps=None):
    """Appelle une API locale et retourne (code HTTP, objet JSON)."""
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


# ---------------------------------------------------------------------------
# 1. Journal des saisies : chaque ecriture SupplyOps ajoute une ligne
# ---------------------------------------------------------------------------

@pytest.fixture()
def serveur_ops(repo_factice):
    """Serveur SupplyOps sur un port libre, branche sur le repo factice."""
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


class TestJournalOps:
    def test_creation_produit_journalisee_avec_le_nom(
        self, serveur_ops, repo_factice
    ):
        code, _ = appel(
            serveur_ops, "/api/produits", "POST",
            {
                "nom_produit": "Webcam 2K autofocus",
                "famille": "Audio",
                "id_fournisseur": "F001",
                "prix_achat": 60000,
                "prix_vente": 95000,
            },
        )
        assert code == 201
        journal = _lire_journal(repo_factice)
        assert len(journal) == 1
        ligne = journal[0]
        assert ligne["type"] == "produit"
        assert ligne["action"] == "creation"
        assert ligne["publie"] is False
        assert ligne["horodatage"]
        assert ligne["resume"] == (
            "Produit cree : Webcam 2K autofocus (Audio, 95 000 MGA)"
        )

    def test_inventaire_et_vente_journalises(self, serveur_ops, repo_factice):
        appel(
            serveur_ops, "/api/inventaire", "POST",
            {"id_produit": "P0001", "id_entrepot": "E01", "quantite": 12},
        )
        appel(
            serveur_ops, "/api/ventes", "POST",
            {
                "id_produit": "P0002", "id_entrepot": "E01",
                "canal": "magasin", "quantite": 2,
            },
        )
        journal = _lire_journal(repo_factice)
        assert [l["type"] for l in journal] == ["inventaire", "vente"]
        assert journal[0]["resume"] == (
            "Inventaire : 12 unites de Casque sans fil "
            "au Depot central Ankorondrano"
        )
        assert journal[1]["resume"] == (
            "Vente : 2 x Lampe solaire (magasin, 95 000 MGA l'unite)"
        )
        assert all(l["publie"] is False for l in journal)

    def test_retrait_lot_et_reception_journalises(
        self, serveur_ops, repo_factice
    ):
        # Retrait de P0002 (ni vente ni lot) : action retrait avec le nom.
        code, _ = appel(serveur_ops, "/api/produits/P0002", "DELETE")
        assert code == 200
        # Lot puis reception sur P0001.
        code, corps = appel(
            serveur_ops, "/api/lots", "POST",
            {
                "id_fournisseur": "F001", "id_entrepot": "E01",
                "lignes": [{"id_produit": "P0001", "quantite": 30}],
            },
        )
        assert code == 201
        id_commande = corps["commandes"][0]["id_commande"]
        appel(serveur_ops, f"/api/lots/{id_commande}/reception", "POST", {})

        journal = _lire_journal(repo_factice)
        assert [l["action"] for l in journal] == [
            "retrait", "creation", "creation",
        ]
        assert journal[0]["resume"] == "Produit retire : Lampe solaire (P0002)"
        assert journal[1]["resume"] == (
            f"Lot d'import : 1 produit(s) chez Shenzhen Electronics "
            f"({id_commande})"
        )
        assert journal[2]["resume"] == (
            f"Reception : 30 unites de Casque sans fil "
            f"sur le lot {id_commande} (recue)"
        )

    def test_ecriture_refusee_non_journalisee(self, serveur_ops, repo_factice):
        code, _ = appel(
            serveur_ops, "/api/produits", "POST",
            {
                "nom_produit": "Produit interdit", "famille": "Papeterie",
                "id_fournisseur": "F001", "prix_achat": 10, "prix_vente": 20,
            },
        )
        assert code == 400
        assert _lire_journal(repo_factice) == []


# ---------------------------------------------------------------------------
# 2. Photographie des KPI et calcul d'impact entre deux bases
# ---------------------------------------------------------------------------

def _creer_entrepot(chemin, couverture, nb_observations, nb_ruptures, ca_total):
    """Petite base DuckDB avec les trois marts lus par la photographie.

    couverture : liste de (id_produit, nom_produit, couverture_jours,
    statut_couverture).
    """
    con = duckdb.connect(str(chemin))
    con.execute(
        "CREATE TABLE supply_couverture_stock ("
        "id_produit VARCHAR, nom_produit VARCHAR, "
        "couverture_jours DOUBLE, statut_couverture VARCHAR)"
    )
    con.executemany(
        "INSERT INTO supply_couverture_stock VALUES (?, ?, ?, ?)", couverture
    )
    con.execute(
        "CREATE TABLE supply_taux_rupture ("
        "nb_observations BIGINT, nb_ruptures BIGINT)"
    )
    con.execute(
        "INSERT INTO supply_taux_rupture VALUES (?, ?)",
        [nb_observations, nb_ruptures],
    )
    con.execute("CREATE TABLE offre_marge_familles (chiffre_affaires DOUBLE)")
    con.execute("INSERT INTO offre_marge_familles VALUES (?)", [ca_total])
    con.close()
    return chemin


@pytest.fixture()
def bases_avant_apres(tmp_path):
    """Deux photographies : un produit ajoute et une rupture resolue."""
    avant = _creer_entrepot(
        tmp_path / "avant.duckdb",
        [
            ("P0001", "Casque sans fil", 20.0, "sain"),
            ("P0002", "Lampe solaire", 2.0, "rupture"),
        ],
        nb_observations=200, nb_ruptures=13, ca_total=1_000_000.0,
    )
    apres = _creer_entrepot(
        tmp_path / "apres.duckdb",
        [
            ("P0001", "Casque sans fil", 20.0, "sain"),
            ("P0002", "Lampe solaire", 14.0, "sain"),
            ("P0003", "Webcam 2K autofocus", 30.0, "sain"),
        ],
        nb_observations=200, nb_ruptures=12, ca_total=1_250_000.0,
    )
    return avant, apres


class TestPhotographieEtImpact:
    def test_photographie_des_kpi(self, bases_avant_apres):
        avant, _ = bases_avant_apres
        photo = moteur.photographier_kpis(str(avant))
        assert photo == {
            "taux_rupture_pct": 6.5,
            "nb_produits_rupture": 1,
            "nb_dormants": 0,
            "couverture_mediane_jours": 11.0,
            "nb_produits": 2,
            "ca_total": 1_000_000.0,
            "produits": {
                "P0001": "Casque sans fil",
                "P0002": "Lampe solaire",
            },
        }

    def test_base_absente_retourne_none(self, tmp_path):
        assert moteur.photographier_kpis(str(tmp_path / "absente.duckdb")) is None

    def test_test_mode_restaure_apres_la_photographie(self, bases_avant_apres):
        avant, _ = bases_avant_apres
        assert os.environ.get("TEST_MODE") == "1"
        moteur.photographier_kpis(str(avant))
        assert os.environ.get("TEST_MODE") == "1"

    def test_impact_produit_ajoute_et_rupture_resolue(self, bases_avant_apres):
        avant, apres = bases_avant_apres
        impact = moteur.calculer_impact(
            moteur.photographier_kpis(str(avant)),
            moteur.photographier_kpis(str(apres)),
        )
        assert impact["produits_apparus"] == [
            {"id_produit": "P0003", "nom_produit": "Webcam 2K autofocus"}
        ]
        assert impact["produits_disparus"] == []
        assert impact["avant"]["nb_produits"] == 2
        assert impact["apres"]["nb_produits"] == 3
        assert "produits" not in impact["avant"]
        resume = impact["resume"]
        assert (
            "1 nouveau produit dans l'entrepot : Webcam 2K autofocus"
            in resume
        )
        assert "Taux de rupture : 6,50 % -> 6,00 %" in resume
        assert "Produits en rupture : 1 -> 0" in resume
        assert 2 <= len(resume) <= 5

    def test_aucun_delta_une_seule_phrase(self, bases_avant_apres):
        avant, _ = bases_avant_apres
        photo = moteur.photographier_kpis(str(avant))
        impact = moteur.calculer_impact(photo, photo)
        assert impact["resume"] == ["Aucun impact mesurable sur les KPI."]


# ---------------------------------------------------------------------------
# 3. Build : impact range dans l'historique, journal marque publie=true
# ---------------------------------------------------------------------------

def _poser_journal(repo, lignes):
    contenu = "".join(json.dumps(l, ensure_ascii=False) + "\n" for l in lignes)
    (repo / "journal_ops.jsonl").write_text(contenu, encoding="utf-8")


@pytest.fixture()
def repo_build(repo_factice, bases_avant_apres, tmp_path, monkeypatch):
    """Repo factice pret a builder : base avant en place, script factice."""
    avant, apres = bases_avant_apres
    import shutil

    shutil.copy(avant, repo_factice / "supply.duckdb")
    script = tmp_path / "faux_build.py"
    script.write_text(SCRIPT_FACTICE, encoding="utf-8")
    monkeypatch.setattr(
        moteur,
        "PIPELINE_COMMANDE",
        [sys.executable, str(script), RUN_VERT, "0", str(apres)],
    )
    return repo_factice


class TestBuildAvecImpact:
    def test_impact_et_modifications_dans_l_historique(self, repo_build):
        _poser_journal(repo_build, [
            {
                "horodatage": "2026-11-13T09:00:00", "type": "vente",
                "action": "creation", "resume": "Vente deja publiee",
                "publie": True,
            },
            {
                "horodatage": "2026-11-13T10:00:00", "type": "produit",
                "action": "creation",
                "resume": "Produit cree : Webcam 2K autofocus (Audio, 95 000 MGA)",
                "publie": False,
            },
            {
                "horodatage": "2026-11-13T10:01:00", "type": "inventaire",
                "action": "creation",
                "resume": "Inventaire : 12 unites de Webcam 2K autofocus "
                          "au Depot central Ankorondrano",
                "publie": False,
            },
        ])

        assert moteur.executer_build("supplyops") == 0

        etat = moteur.lire_etat()
        entree = etat["historique"][0]
        assert entree["statut"] == "succes"
        assert entree["modifications"] == [
            "Produit cree : Webcam 2K autofocus (Audio, 95 000 MGA)",
            "Inventaire : 12 unites de Webcam 2K autofocus "
            "au Depot central Ankorondrano",
        ]
        assert (
            "1 nouveau produit dans l'entrepot : Webcam 2K autofocus"
            in entree["impact"]["resume"]
        )
        assert entree["impact"]["avant"]["taux_rupture_pct"] == 6.5
        assert entree["impact"]["apres"]["taux_rupture_pct"] == 6.0
        # Toutes les lignes du journal sont desormais publiees.
        assert all(l["publie"] is True for l in _lire_journal(repo_build))

    def test_build_nocturne_ne_publie_pas_le_journal(self, repo_build):
        _poser_journal(repo_build, [
            {
                "horodatage": "2026-11-13T10:00:00", "type": "vente",
                "action": "creation", "resume": "Vente en attente",
                "publie": False,
            },
        ])
        assert moteur.executer_build("nocturne") == 0
        etat = moteur.lire_etat()
        assert etat["historique"][0]["modifications"] == []
        journal = _lire_journal(repo_build)
        assert journal[0]["publie"] is False

    def test_sans_base_avant_pas_d_impact(self, repo_build):
        os.remove(repo_build / "supply.duckdb")
        assert moteur.executer_build("supplyops") == 0
        assert "impact" not in moteur.lire_etat()["historique"][0]


# ---------------------------------------------------------------------------
# 4. API cockpit : GET /api/notifications fusionne et ordonne
# ---------------------------------------------------------------------------

ETAT_BUILD_FIXTURE = {
    "horodatage": "2026-11-13T10:00:00",
    "statut": "succes",
    "pass": 151, "fail": 0, "total": 151, "duree_s": 1.1,
    "declencheur": "supplyops",
    "historique": [
        {
            "horodatage": "2026-11-13T10:00:00", "statut": "succes",
            "pass": 151, "fail": 0, "total": 151, "duree_s": 1.1,
            "declencheur": "supplyops",
            "modifications": [
                "Produit cree : Webcam 2K autofocus (Audio, 95 000 MGA)",
            ],
            "impact": {
                "avant": {}, "apres": {},
                "produits_apparus": [], "produits_disparus": [],
                "resume": [
                    "1 nouveau produit dans l'entrepot : Webcam 2K autofocus",
                    "Taux de rupture : 6,50 % -> 6,00 %",
                ],
            },
        },
        {
            "horodatage": "2026-11-13T07:00:00", "statut": "succes",
            "pass": 151, "fail": 0, "total": 151, "duree_s": 1.0,
            "declencheur": "nocturne", "modifications": [],
        },
        {
            "horodatage": "2026-11-12T18:00:00", "statut": "echec",
            "pass": 149, "fail": 2, "total": 151, "duree_s": 1.2,
            "declencheur": "supplysense", "modifications": [],
        },
    ],
}

JOURNAL_FIXTURE = [
    {
        "horodatage": "2026-11-13T09:00:00", "type": "produit",
        "action": "creation", "resume": "Saisie deja publiee", "publie": True,
    },
    {
        "horodatage": "2026-11-13T11:00:00", "type": "inventaire",
        "action": "creation",
        "resume": "Inventaire : 4 unites de Casque sans fil "
                  "au Depot central Ankorondrano",
        "publie": False,
    },
    {
        "horodatage": "2026-11-13T11:05:00", "type": "vente",
        "action": "creation",
        "resume": "Vente : 1 x Lampe solaire (web, 95 000 MGA l'unite)",
        "publie": False,
    },
]


@pytest.fixture()
def cockpit(repo_factice, tmp_path, monkeypatch):
    """Serveur cockpit sur un port libre, branche sur le repo factice."""
    (repo_factice / "etat_build.json").write_text(
        json.dumps(ETAT_BUILD_FIXTURE, ensure_ascii=False), encoding="utf-8"
    )
    _poser_journal(repo_factice, JOURNAL_FIXTURE)
    monkeypatch.setenv("COCKPIT_RACINE", str(tmp_path / "racine"))
    serveur = cockpit_serveur.creer_serveur(0)
    thread = threading.Thread(target=serveur.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{serveur.server_address[1]}"
    finally:
        serveur.shutdown()
        serveur.server_close()


class TestApiNotifications:
    def test_fusion_ordonnee_du_plus_recent_au_plus_ancien(self, cockpit):
        code, corps = appel(cockpit, "/api/notifications")
        assert code == 200
        assert corps["ok"] is True
        notifications = corps["notifications"]
        assert [n["type"] for n in notifications] == [
            "attente", "publication", "publication",
        ]
        horodatages = [n["horodatage"] for n in notifications]
        assert horodatages == sorted(horodatages, reverse=True)
        # Le build nocturne n'est pas un evenement de publication.
        assert all(
            n.get("declencheur") != "nocturne" for n in notifications
        )

    def test_evenement_publication_complet(self, cockpit):
        _, corps = appel(cockpit, "/api/notifications")
        publication = corps["notifications"][1]
        assert publication["id"] == "publication-2026-11-13T10:00:00"
        assert publication["statut"] == "succes"
        assert publication["declencheur"] == "supplyops"
        assert publication["pass"] == 151
        assert publication["modifications"] == [
            "Produit cree : Webcam 2K autofocus (Audio, 95 000 MGA)",
        ]
        assert publication["impact"] == [
            "1 nouveau produit dans l'entrepot : Webcam 2K autofocus",
            "Taux de rupture : 6,50 % -> 6,00 %",
        ]
        echec = corps["notifications"][2]
        assert echec["statut"] == "echec"
        assert echec["fail"] == 2

    def test_evenement_attente_agrege_les_saisies(self, cockpit):
        _, corps = appel(cockpit, "/api/notifications")
        attente = corps["notifications"][0]
        assert attente["id"] == "attente-2026-11-13T11:05:00"
        assert attente["nb"] == 2
        assert len(attente["resumes"]) == 2
        assert "Casque sans fil" in attente["resumes"][0]
        assert corps["nb_en_attente"] == 2

    def test_badge_non_lus_coherent(self, cockpit):
        """Le front compte les non-lus avec horodatage > dernier vu."""
        _, corps = appel(cockpit, "/api/notifications")
        notifications = corps["notifications"]

        def non_lus(dernier_vu):
            return sum(1 for n in notifications if n["horodatage"] > dernier_vu)

        assert non_lus("") == 3
        assert non_lus("2026-11-13T10:00:00") == 1
        assert non_lus("2026-11-13T11:05:00") == 0
        # Les ids sont stables : deux lectures donnent les memes evenements.
        _, relecture = appel(cockpit, "/api/notifications")
        assert [n["id"] for n in relecture["notifications"]] == [
            n["id"] for n in notifications
        ]

    def test_ids_stables_derives_de_l_horodatage(self, cockpit):
        _, corps = appel(cockpit, "/api/notifications")
        for notification in corps["notifications"]:
            assert notification["id"].endswith(notification["horodatage"])
