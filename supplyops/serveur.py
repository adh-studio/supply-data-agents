"""Serveur SupplyOps : saisie operationnelle, stdlib uniquement (port 4310).

Philosophie : l'OPERATIONNEL ecrit, l'ANALYTIQUE lit. Ce serveur est le
seul point d'ecriture des seeds CSV du repo dbt, via supplyops.magasin
(validations strictes, CRLF preserve, ecriture atomique). Il ne lit
JAMAIS l'entrepot DuckDB : la lecture analytique est le role du cockpit
SupplySense (port 4300).

API REST JSON :
  GET    /api/etat                    modifications en attente + dernier build
  GET    /api/referentiels            familles du contrat, fournisseurs, entrepots
  GET    /api/produits                catalogue produits
  POST   /api/produits                creation d'un produit
  POST   /api/produits/<id>           modification (nom, famille, fournisseur, prix)
  DELETE /api/produits/<id>           retrait (refuse si le produit a des ventes)
  GET    /api/inventaire              inventaire courant par produit x entrepot
  POST   /api/inventaire              photographie de stock du jour
  GET    /api/ventes                  journal des dernieres ventes
  POST   /api/ventes                  saisie de vente rapide
  GET    /api/lots                    lots d'import et receptions
  POST   /api/lots                    creation d'un lot (1 commande par produit)
  POST   /api/lots/<id>/reception     enregistrement d'une arrivee
  POST   /api/publier                 lance le build dbt (409 si deja en cours)

Chaque ecriture reussie incremente le compteur de modifications en
attente (supplyops_etat.json, a cote des seeds). POST /api/publier
delegue a pipeline.moteur.executer_build("supplyops") dans un thread et
remet le compteur a zero en cas de succes.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pipeline import moteur as moteur_pipeline
from supplyops import magasin

PORT_DEFAUT = 4310

URL_SUPPLYSENSE = "http://127.0.0.1:4300"

DOSSIER_SUPPLYOPS = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PUBLIC = os.path.join(DOSSIER_SUPPLYOPS, "public")

NOM_ETAT_OPS = "supplyops_etat.json"
TAILLE_JOURNAL = 50

FONCTION_BUILD = None
"""Fonction de build, surchargable par les tests.

Si None (defaut), pipeline.moteur.executer_build est utilisee : les tests
la remplacent pour ne jamais lancer un vrai dbt build.
"""

MIME = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "text/javascript",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

_REGEX_PRODUIT = re.compile(r"^/api/produits/([A-Za-z0-9_-]+)$")
_REGEX_RECEPTION = re.compile(r"^/api/lots/([A-Za-z0-9_-]+)/reception$")

_verrou_publication = threading.Lock()
_publication = {"en_cours": False, "dernier_code": None}


# ---------------------------------------------------------------------------
# Etat SupplyOps : modifications en attente de publication
# ---------------------------------------------------------------------------

def chemin_etat_ops() -> str:
    """supplyops_etat.json, a cote du dossier seeds (donc dans le repo dbt)."""
    return os.path.join(
        os.path.dirname(magasin.dossier_seeds()), NOM_ETAT_OPS
    )


def lire_etat_ops() -> dict:
    chemin = chemin_etat_ops()
    if os.path.exists(chemin):
        try:
            with open(chemin, encoding="utf-8") as fichier:
                return json.load(fichier)
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "modifications_en_attente": 0,
        "journal": [],
        "derniere_publication": None,
    }


def _ecrire_etat_ops(etat: dict) -> None:
    chemin = chemin_etat_ops()
    chemin_temporaire = chemin + ".tmp"
    with open(chemin_temporaire, "w", encoding="utf-8") as fichier:
        json.dump(etat, fichier, ensure_ascii=False, indent=2)
        fichier.write("\n")
    os.replace(chemin_temporaire, chemin)


def marquer_modification(action: str, detail: str) -> dict:
    """Apres chaque ecriture reussie : compteur + journal des saisies."""
    etat = lire_etat_ops()
    etat["modifications_en_attente"] = (
        int(etat.get("modifications_en_attente") or 0) + 1
    )
    journal = list(etat.get("journal") or [])
    journal.insert(
        0,
        {
            "horodatage": datetime.datetime.now().isoformat(timespec="seconds"),
            "action": action,
            "detail": detail,
        },
    )
    etat["journal"] = journal[:TAILLE_JOURNAL]
    _ecrire_etat_ops(etat)
    return etat


def _remettre_compteur_a_zero() -> None:
    etat = lire_etat_ops()
    etat["modifications_en_attente"] = 0
    etat["derniere_publication"] = datetime.datetime.now().isoformat(
        timespec="seconds"
    )
    _ecrire_etat_ops(etat)


# ---------------------------------------------------------------------------
# Publication : build dbt en thread, un seul a la fois
# ---------------------------------------------------------------------------

def _fonction_build():
    return FONCTION_BUILD or moteur_pipeline.executer_build


def _executer_publication() -> None:
    try:
        code = _fonction_build()("supplyops")
    except Exception:
        code = 1
    if code == 0:
        _remettre_compteur_a_zero()
    with _verrou_publication:
        _publication["dernier_code"] = code
        _publication["en_cours"] = False


def lancer_publication() -> tuple[int, dict]:
    """Demarre le build en thread. Retourne (code HTTP, corps JSON)."""
    with _verrou_publication:
        if _publication["en_cours"]:
            return 409, {
                "ok": False,
                "erreur": "Une publication est deja en cours.",
            }
        _publication["en_cours"] = True
        _publication["dernier_code"] = None
    fil = threading.Thread(target=_executer_publication, daemon=True)
    fil.start()
    return 202, {"ok": True, "statut": "publication_lancee"}


def etat_complet() -> dict:
    ops = lire_etat_ops()
    with _verrou_publication:
        en_cours = _publication["en_cours"]
        dernier_code = _publication["dernier_code"]
    return {
        "ok": True,
        "application": "SupplyOps",
        "ecriture_controlee": True,
        "seeds": magasin.dossier_seeds(),
        "modifications_en_attente": ops.get("modifications_en_attente", 0),
        "journal": ops.get("journal", []),
        "derniere_publication": ops.get("derniere_publication"),
        "publication_en_cours": en_cours,
        "dernier_code_publication": dernier_code,
        "build": moteur_pipeline.lire_etat(),
        "url_supplysense": URL_SUPPLYSENSE,
    }


# ---------------------------------------------------------------------------
# Vues enrichies (noms lisibles pour la SPA)
# ---------------------------------------------------------------------------

def _index(nom_table: str, colonne: str) -> dict[str, dict]:
    return {ligne[colonne]: ligne for ligne in magasin.lire_table(nom_table)}


def vue_produits() -> list[dict]:
    fournisseurs = _index("fournisseurs", "id_fournisseur")
    ventes_par_produit: dict[str, int] = {}
    for vente in magasin.lire_table("ventes"):
        ventes_par_produit[vente["id_produit"]] = (
            ventes_par_produit.get(vente["id_produit"], 0) + 1
        )
    produits = []
    for produit in magasin.lister_produits():
        fournisseur = fournisseurs.get(produit["id_fournisseur"], {})
        achat, vente = int(produit["prix_achat"]), int(produit["prix_vente"])
        produits.append(
            {
                **produit,
                "nom_fournisseur": fournisseur.get(
                    "nom_fournisseur", produit["id_fournisseur"]
                ),
                "marge_unitaire": vente - achat,
                "taux_marge_pct": round(100.0 * (vente - achat) / vente, 1)
                if vente
                else 0.0,
                "nb_ventes": ventes_par_produit.get(produit["id_produit"], 0),
            }
        )
    return produits


def vue_inventaire() -> list[dict]:
    produits = _index("produits", "id_produit")
    entrepots = _index("entrepots", "id_entrepot")
    lignes = []
    for ligne in magasin.inventaire_courant():
        produit = produits.get(ligne["id_produit"], {})
        entrepot = entrepots.get(ligne["id_entrepot"], {})
        lignes.append(
            {
                **ligne,
                "nom_produit": produit.get("nom_produit", ligne["id_produit"]),
                "famille": produit.get("famille", ""),
                "nom_entrepot": entrepot.get(
                    "nom_entrepot", ligne["id_entrepot"]
                ),
            }
        )
    return lignes


def vue_ventes(limite: int = 30) -> list[dict]:
    produits = _index("produits", "id_produit")
    entrepots = _index("entrepots", "id_entrepot")
    lignes = []
    for vente in magasin.dernieres_ventes(limite):
        produit = produits.get(vente["id_produit"], {})
        entrepot = entrepots.get(vente["id_entrepot"], {})
        lignes.append(
            {
                **vente,
                "nom_produit": produit.get("nom_produit", vente["id_produit"]),
                "nom_entrepot": entrepot.get(
                    "nom_entrepot", vente["id_entrepot"]
                ),
                "montant_ligne": int(vente["quantite"])
                * int(vente["prix_unitaire"]),
            }
        )
    return lignes


# ---------------------------------------------------------------------------
# Handler HTTP
# ---------------------------------------------------------------------------

class SupplyOps(BaseHTTPRequestHandler):
    server_version = "SupplyOps/1.0"

    def log_message(self, format, *args):  # noqa: A002 - signature imposee
        if os.environ.get("OPS_VERBOSE"):
            super().log_message(format, *args)

    def _json(self, code: int, objet: dict) -> None:
        corps = json.dumps(objet, ensure_ascii=False, default=str).encode(
            "utf-8"
        )
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def _corps(self) -> dict:
        try:
            longueur = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            longueur = 0
        if longueur <= 0 or longueur > 1_000_000:
            return {}
        try:
            return json.loads(
                self.rfile.read(longueur).decode("utf-8") or "{}"
            )
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _fichier(self, chemin: str) -> None:
        extension = os.path.splitext(chemin)[1].lower()
        with open(chemin, "rb") as fichier:
            corps = fichier.read()
        self.send_response(200)
        self.send_header(
            "Content-Type",
            MIME.get(extension, "text/plain") + "; charset=utf-8",
        )
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def _statique(self, chemin: str) -> None:
        if chemin == "/":
            chemin = "/index.html"
        cible = os.path.normpath(
            os.path.join(DOSSIER_PUBLIC, chemin.lstrip("/"))
        )
        if not (
            cible.startswith(DOSSIER_PUBLIC + os.sep) or cible == DOSSIER_PUBLIC
        ):
            return self._json(404, {"ok": False, "erreur": "introuvable"})
        if os.path.isfile(cible):
            return self._fichier(cible)
        return self._json(404, {"ok": False, "erreur": "introuvable"})

    # -- GET -----------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - API http.server
        chemin = self.path.split("?", 1)[0]
        try:
            if chemin == "/api/etat":
                return self._json(200, etat_complet())
            if chemin == "/api/referentiels":
                return self._json(
                    200,
                    {
                        "ok": True,
                        "familles": magasin.familles_autorisees(),
                        "canaux": list(magasin.CANAUX_VALIDES),
                        "fournisseurs": magasin.lister_fournisseurs(),
                        "entrepots": magasin.lister_entrepots(),
                    },
                )
            if chemin == "/api/produits":
                return self._json(200, {"ok": True, "produits": vue_produits()})
            if chemin == "/api/inventaire":
                return self._json(
                    200, {"ok": True, "inventaire": vue_inventaire()}
                )
            if chemin == "/api/ventes":
                return self._json(200, {"ok": True, "ventes": vue_ventes()})
            if chemin == "/api/lots":
                return self._json(
                    200, {"ok": True, "lots": magasin.lister_lots()}
                )
            if chemin.startswith("/api/"):
                return self._json(404, {"ok": False, "erreur": "route inconnue"})
            return self._statique(chemin)
        except magasin.ErreurValidation as erreur:
            return self._json(400, {"ok": False, "erreur": str(erreur)})
        except FileNotFoundError as erreur:
            return self._json(404, {"ok": False, "erreur": str(erreur)})
        except Exception as erreur:  # garde-fou : jamais de 500 silencieux
            return self._json(
                500,
                {"ok": False, "erreur": f"{type(erreur).__name__}: {erreur}"},
            )

    # -- POST ----------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802 - API http.server
        chemin = self.path.split("?", 1)[0]
        donnees = self._corps()
        try:
            if chemin == "/api/produits":
                resultat = magasin.creer_produit(
                    donnees.get("nom_produit"),
                    donnees.get("famille"),
                    donnees.get("id_fournisseur"),
                    donnees.get("prix_achat"),
                    donnees.get("prix_vente"),
                )
                marquer_modification(
                    "produit_cree",
                    f"{resultat['produit']['id_produit']} "
                    f"{resultat['produit']['nom_produit']}",
                )
                return self._json(201, {"ok": True, **resultat})

            correspondance = _REGEX_PRODUIT.match(chemin)
            if correspondance:
                id_produit = correspondance.group(1)
                champs = {
                    cle: valeur
                    for cle, valeur in donnees.items()
                    if valeur is not None
                }
                resultat = magasin.modifier_produit(id_produit, champs)
                marquer_modification(
                    "produit_modifie",
                    f"{id_produit} ({', '.join(sorted(champs))})",
                )
                return self._json(200, {"ok": True, **resultat})

            if chemin == "/api/inventaire":
                resultat = magasin.saisir_inventaire(
                    donnees.get("id_produit"),
                    donnees.get("id_entrepot"),
                    donnees.get("quantite"),
                    donnees.get("date_inventaire"),
                )
                ligne = resultat["ligne"]
                marquer_modification(
                    "inventaire_saisi",
                    f"{ligne['id_produit']} x {ligne['id_entrepot']} "
                    f"= {ligne['quantite_disponible']} u.",
                )
                return self._json(201, {"ok": True, **resultat})

            if chemin == "/api/ventes":
                resultat = magasin.saisir_vente(
                    donnees.get("id_produit"),
                    donnees.get("id_entrepot"),
                    donnees.get("canal"),
                    donnees.get("quantite"),
                    donnees.get("prix_unitaire"),
                    donnees.get("date_vente"),
                )
                vente = resultat["vente"]
                marquer_modification(
                    "vente_saisie",
                    f"{vente['id_vente']} : {vente['quantite']} x "
                    f"{vente['id_produit']} ({vente['canal']})",
                )
                return self._json(201, {"ok": True, **resultat})

            if chemin == "/api/lots":
                resultat = magasin.creer_lot(
                    donnees.get("id_fournisseur"),
                    donnees.get("id_entrepot"),
                    donnees.get("lignes"),
                    donnees.get("date_commande"),
                )
                ids = ", ".join(
                    commande["id_commande"]
                    for commande in resultat["commandes"]
                )
                marquer_modification(
                    "lot_cree",
                    f"{ids} chez {donnees.get('id_fournisseur')}",
                )
                return self._json(201, {"ok": True, **resultat})

            correspondance = _REGEX_RECEPTION.match(chemin)
            if correspondance:
                id_commande = correspondance.group(1)
                resultat = magasin.enregistrer_reception(
                    id_commande,
                    donnees.get("date_reception"),
                    donnees.get("quantite_recue"),
                )
                marquer_modification(
                    "reception_enregistree",
                    f"{resultat['reception']['id_reception']} sur "
                    f"{id_commande} ({resultat['statut_lot']})",
                )
                return self._json(201, {"ok": True, **resultat})

            if chemin == "/api/publier":
                code, corps = lancer_publication()
                return self._json(code, corps)

            return self._json(404, {"ok": False, "erreur": "route inconnue"})
        except magasin.ErreurValidation as erreur:
            return self._json(400, {"ok": False, "erreur": str(erreur)})
        except Exception as erreur:
            return self._json(
                500,
                {"ok": False, "erreur": f"{type(erreur).__name__}: {erreur}"},
            )

    # -- DELETE --------------------------------------------------------------

    def do_DELETE(self) -> None:  # noqa: N802 - API http.server
        chemin = self.path.split("?", 1)[0]
        try:
            correspondance = _REGEX_PRODUIT.match(chemin)
            if correspondance:
                id_produit = correspondance.group(1)
                resultat = magasin.retirer_produit(id_produit)
                marquer_modification("produit_retire", id_produit)
                return self._json(200, {"ok": True, **resultat})
            return self._json(404, {"ok": False, "erreur": "route inconnue"})
        except magasin.ErreurValidation as erreur:
            return self._json(400, {"ok": False, "erreur": str(erreur)})
        except Exception as erreur:
            return self._json(
                500,
                {"ok": False, "erreur": f"{type(erreur).__name__}: {erreur}"},
            )


# ---------------------------------------------------------------------------
# Demarrage
# ---------------------------------------------------------------------------

def creer_serveur(port: int = 0) -> ThreadingHTTPServer:
    """Cree le serveur sur 127.0.0.1 (port 0 = port libre, pour les tests)."""
    serveur = ThreadingHTTPServer(("127.0.0.1", port), SupplyOps)
    serveur.daemon_threads = True
    return serveur


def demarrer(port: int = PORT_DEFAUT) -> None:
    """Demarre SupplyOps et affiche l'URL."""
    serveur = creer_serveur(port)
    print(
        f"SupplyOps (saisie, ecriture controlee, seeds : "
        f"{magasin.dossier_seeds()}) "
        f"-> http://127.0.0.1:{serveur.server_address[1]}"
    )
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        serveur.server_close()
