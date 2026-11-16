"""Moteur de generation des fiches produits.

Lit les produits dans les marts offre_top_flop_produits et
supply_couverture_stock, genere une fiche par produit (titre, description de
80 a 120 mots, 3 points forts, mention de matiere ou dimension plausible tiree
du nom), applique les garde-fous, puis depose les fiches conformes en
a_valider/ avec un en-tete YAML (statut: en_attente).

Deux modes de generation :
- LLM (claude-sonnet-5 via la librairie anthropic) si ANTHROPIC_API_KEY est
  presente, avec le ton de marque de fiches_produits/ton_de_marque.md ;
- gabarit deterministe integre sinon (aucun appel reseau).

La publication reste un geste humain : approuver(id) bascule la fiche vers
validees/ et passe le statut a validee. Rien d'autre ne publie jamais.
"""

from __future__ import annotations

import datetime
import json
import os
import re

import yaml

from fiches_produits.garde_fous import (
    MOTS_DESCRIPTION_MIN,
    compter_mots,
    verifier_fiche,
)
from lib.base import requete

RACINE_PROJET = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOSSIER_MODULE = os.path.dirname(os.path.abspath(__file__))
CHEMIN_TON_DE_MARQUE = os.path.join(DOSSIER_MODULE, "ton_de_marque.md")

NOM_DOSSIER_A_VALIDER = "a_valider"
NOM_DOSSIER_VALIDEES = "validees"
NOM_DOSSIER_REJETEES = "rejetees"
NOM_DOSSIER_ARCHIVEES = "archivees"

MODELE_LLM = "claude-sonnet-5"

# Matieres plausibles reconnaissables dans le nom du produit.
_MATIERES = {
    "lin": "lin lave",
    "velours": "velours",
    "rotin": "rotin tresse",
    "manguier": "bois de manguier",
    "chene": "chene massif",
    "bambou": "bambou",
    "gres": "gres artisanal",
    "jute": "jute naturelle",
    "cannage": "cannage traditionnel",
    "ceramique": "ceramique",
    "maille": "maille douce",
    "berbere": "laine tissee",
}

# Dimensions ou formats reconnaissables dans le nom du produit.
_REGEX_DIMENSIONS = (
    re.compile(r"\b(\d{2,3}\s?x\s?\d{2,3})\b"),
    re.compile(r"\b(\d{2,3}\s?cm)\b"),
    re.compile(r"\b(\d\s?places?)\b"),
    re.compile(r"\b(\d\s?portes?)\b"),
)

# Phrases d'appoint deterministes si la description est trop courte.
_PHRASES_APPOINT = (
    "Chaque piece est controlee avant expedition pour arriver chez vous dans "
    "de bonnes conditions.",
    "Il s'installe en quelques minutes et se deplace facilement au gre de "
    "vos envies.",
    "Pensez a l'associer a des textiles naturels pour une ambiance douce et "
    "accueillante.",
)


# ---------------------------------------------------------------------------
# Lecture des produits dans l'entrepot
# ---------------------------------------------------------------------------

def lire_produits(limite: int = 3) -> list[dict]:
    """Lit les produits a documenter, classement top en premier."""
    if not isinstance(limite, int) or limite < 1:
        raise ValueError("La limite doit etre un entier positif.")
    lignes = requete(
        f"""
        SELECT
            t.id_produit,
            t.nom_produit,
            t.famille,
            t.segment,
            MAX(c.statut_couverture) AS statut_couverture
        FROM offre_top_flop_produits t
        LEFT JOIN supply_couverture_stock c
            ON t.id_produit = c.id_produit
        GROUP BY t.id_produit, t.nom_produit, t.famille, t.segment, t.rang_ca
        ORDER BY CASE WHEN t.segment = 'top' THEN 0 ELSE 1 END, t.rang_ca ASC
        LIMIT {limite}
        """
    )
    return lignes


def lire_referentiel() -> list[dict]:
    """Lit TOUS les produits du referentiel via le mart offre_top_flop_produits.

    Le mart contient l'integralite du catalogue (y compris les produits sans
    vente), ce qui en fait le referentiel de couverture des fiches.
    """
    return requete(
        """
        SELECT id_produit, nom_produit, famille, segment, rang_ca
        FROM offre_top_flop_produits
        ORDER BY id_produit
        """
    )


def _ids_dossier(racine: str, nom_dossier: str) -> set[str]:
    """Identifiants produits presents dans un dossier de fiches (*.md)."""
    dossier = os.path.join(racine, nom_dossier)
    if not os.path.isdir(dossier):
        return set()
    return {
        os.path.splitext(nom)[0]
        for nom in os.listdir(dossier)
        if nom.endswith(".md")
    }


def statut_fiche(id_produit: str, racine: str = RACINE_PROJET) -> str:
    """Statut de fiche le plus avance d'un produit.

    validee > en_attente > rejetee > aucune (un produit peut avoir plusieurs
    etats historiques, par exemple une fiche validee puis un nouveau brouillon).
    """
    for nom_dossier, statut in (
        (NOM_DOSSIER_VALIDEES, "validee"),
        (NOM_DOSSIER_A_VALIDER, "en_attente"),
        (NOM_DOSSIER_REJETEES, "rejetee"),
    ):
        if os.path.exists(os.path.join(racine, nom_dossier, f"{id_produit}.md")):
            return statut
    return "aucune"


def lister_couverture(racine: str = RACINE_PROJET) -> list[dict]:
    """Pour CHAQUE produit du referentiel, son statut de fiche.

    Retourne une ligne par produit : id_produit, nom_produit, famille,
    segment et statut (validee / en_attente / rejetee / aucune, le plus
    avance l'emportant).
    """
    presents = {
        "validee": _ids_dossier(racine, NOM_DOSSIER_VALIDEES),
        "en_attente": _ids_dossier(racine, NOM_DOSSIER_A_VALIDER),
        "rejetee": _ids_dossier(racine, NOM_DOSSIER_REJETEES),
    }
    lignes = []
    for produit in lire_referentiel():
        statut = "aucune"
        for candidat in ("validee", "en_attente", "rejetee"):
            if produit["id_produit"] in presents[candidat]:
                statut = candidat
                break
        lignes.append(
            {
                "id_produit": produit["id_produit"],
                "nom_produit": produit["nom_produit"],
                "famille": produit["famille"],
                "segment": produit["segment"],
                "statut": statut,
            }
        )
    return lignes


# ---------------------------------------------------------------------------
# Extraction matiere / dimension plausibles depuis le nom
# ---------------------------------------------------------------------------

def extraire_matiere(nom_produit: str) -> str | None:
    """Retourne une matiere plausible tiree du nom du produit, ou None."""
    nom_minuscule = (nom_produit or "").lower()
    for indice, matiere in _MATIERES.items():
        if indice in nom_minuscule:
            return matiere
    return None


def extraire_dimension(nom_produit: str) -> str | None:
    """Retourne une dimension ou un format tire du nom du produit, ou None."""
    for regex in _REGEX_DIMENSIONS:
        correspondance = regex.search(nom_produit or "")
        if correspondance:
            return correspondance.group(1)
    return None


# ---------------------------------------------------------------------------
# Mode gabarit deterministe (aucun appel reseau)
# ---------------------------------------------------------------------------

def generer_fiche_gabarit(produit: dict) -> dict:
    """Genere une fiche deterministe conforme au ton de marque."""
    nom = produit["nom_produit"]
    famille = produit.get("famille") or "deco"
    matiere = extraire_matiere(nom)
    dimension = extraire_dimension(nom)

    if matiere:
        titre = f"{nom}, la chaleur du {matiere} chez vous"
    else:
        titre = f"{nom}, une piece a vivre au quotidien"
    if len(titre) > 90:
        titre = f"{nom}, l'esprit deco"

    phrases = [
        f"{nom} apporte a votre interieur une presence chaleureuse, sans "
        "jamais en faire trop.",
    ]
    if matiere:
        phrases.append(
            f"Sa matiere, le {matiere}, a ete retenue pour sa solidite et "
            "son toucher agreable au fil des saisons."
        )
    else:
        phrases.append(
            "Ses materiaux ont ete retenus pour leur solidite et leur "
            "toucher agreable au fil des saisons."
        )
    if dimension:
        phrases.append(
            f"Son format {dimension} lui permet de trouver sa place dans un "
            "grand sejour comme dans une piece plus intime."
        )
    else:
        phrases.append(
            "Ses proportions equilibrees lui permettent de trouver sa "
            "place dans un grand sejour comme dans une piece plus intime."
        )
    phrases.append(
        f"Dans la famille {famille}, ce modele se distingue par une ligne "
        "sobre qui s'accorde avec des tons neutres comme avec des couleurs "
        "plus franches."
    )
    phrases.append(
        "L'entretien reste simple : un chiffon doux et un geste regulier "
        "suffisent a le garder beau longtemps."
    )

    description = " ".join(phrases)
    for appoint in _PHRASES_APPOINT:
        if compter_mots(description) >= MOTS_DESCRIPTION_MIN:
            break
        description = f"{description} {appoint}"

    points_forts = [
        f"Matiere authentique : {matiere}" if matiere
        else "Materiaux choisis avec soin",
        f"Format pratique : {dimension}" if dimension
        else "Proportions pensees pour la vie quotidienne",
        "Entretien facile au quotidien",
    ]

    return {
        "titre": titre,
        "description": description,
        "points_forts": points_forts,
    }


# ---------------------------------------------------------------------------
# Mode LLM (claude-sonnet-5), uniquement si ANTHROPIC_API_KEY est presente
# ---------------------------------------------------------------------------

def _lire_ton_de_marque() -> str:
    """Lit le document de ton de marque du module."""
    with open(CHEMIN_TON_DE_MARQUE, encoding="utf-8") as fichier:
        return fichier.read()


def _appeler_llm(prompt: str) -> str:
    """Appelle claude-sonnet-5 et retourne le texte brut de la reponse.

    Isole pour etre facilement remplace par un bouchon dans les tests :
    aucun test ne doit toucher le reseau.
    """
    import anthropic

    client = anthropic.Anthropic()
    reponse = client.messages.create(
        model=MODELE_LLM,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return reponse.content[0].text


def generer_fiche_llm(produit: dict) -> dict:
    """Genere une fiche via le LLM, au format JSON strict."""
    matiere = extraire_matiere(produit["nom_produit"])
    dimension = extraire_dimension(produit["nom_produit"])
    prompt = (
        "Tu rediges une fiche produit e-commerce pour une enseigne de "
        "decoration et d'ameublement. Respecte strictement ce ton de "
        "marque :\n\n"
        f"{_lire_ton_de_marque()}\n\n"
        "Produit :\n"
        f"- nom : {produit['nom_produit']}\n"
        f"- famille : {produit.get('famille', '')}\n"
        f"- matiere reperee dans le nom : {matiere or 'aucune'}\n"
        f"- dimension reperee dans le nom : {dimension or 'aucune'}\n\n"
        "Reponds UNIQUEMENT avec un objet JSON de la forme :\n"
        '{"titre": "...", "description": "...", "points_forts": '
        '["...", "...", "..."]}\n'
        "La description fait entre 80 et 120 mots. Exactement 3 points "
        "forts. Aucun prix, aucun pourcentage, aucun superlatif vide."
    )
    brut = _appeler_llm(prompt).strip()
    # Tolere un eventuel bloc de code autour du JSON.
    brut = re.sub(r"^```(?:json)?\s*|\s*```$", "", brut)
    fiche = json.loads(brut)
    return {
        "titre": fiche.get("titre", ""),
        "description": fiche.get("description", ""),
        "points_forts": list(fiche.get("points_forts", [])),
    }


def _construire_prompt(produit: dict) -> str:
    """Prompt de redaction partage par les modes llm et claude_code."""
    matiere = extraire_matiere(produit["nom_produit"])
    dimension = extraire_dimension(produit["nom_produit"])
    return (
        "Tu rediges une fiche produit e-commerce pour une enseigne de "
        "decoration et d'ameublement. Respecte strictement ce ton de "
        "marque :\n\n"
        f"{_lire_ton_de_marque()}\n\n"
        "Produit :\n"
        f"- nom : {produit['nom_produit']}\n"
        f"- famille : {produit.get('famille', '')}\n"
        f"- matiere reperee dans le nom : {matiere or 'aucune'}\n"
        f"- dimension reperee dans le nom : {dimension or 'aucune'}\n\n"
        "Reponds UNIQUEMENT avec un objet JSON de la forme :\n"
        '{"titre": "...", "description": "...", "points_forts": '
        '["...", "...", "..."]}\n'
        "La description fait entre 80 et 120 mots. Exactement 3 points "
        "forts. Aucun prix, aucun pourcentage, aucun superlatif vide."
    )


def _extraire_fiche_json(brut: str) -> dict:
    """Parse la reponse JSON d'un modele, balises de code tolerees."""
    brut = re.sub(r"^```(?:json)?\s*|\s*```$", "", brut.strip())
    fiche = json.loads(brut)
    return {
        "titre": fiche.get("titre", ""),
        "description": fiche.get("description", ""),
        "points_forts": list(fiche.get("points_forts", [])),
    }


def generer_fiche_claude_code(produit: dict) -> dict:
    """Genere une fiche via la CLI claude (abonnement, sans cle API)."""
    from lib import claude_code

    return _extraire_fiche_json(claude_code.demander(_construire_prompt(produit)))


def generer_fiche(produit: dict) -> tuple[dict, str]:
    """Genere une fiche et retourne (fiche, mode utilise).

    Ordre des moteurs : llm si la cle API est presente, sinon la CLI claude
    (abonnement) si disponible, sinon le gabarit deterministe. Tout echec
    retombe sur le gabarit ; les garde-fous s'appliquent apres, quel que
    soit le moteur.
    """
    from lib import claude_code

    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return generer_fiche_llm(produit), "llm"
        except Exception:
            return generer_fiche_gabarit(produit), "gabarit"
    if claude_code.disponible():
        try:
            return generer_fiche_claude_code(produit), "claude_code"
        except Exception:
            return generer_fiche_gabarit(produit), "gabarit"
    return generer_fiche_gabarit(produit), "gabarit"


# ---------------------------------------------------------------------------
# Flux de validation humaine : a_valider/ puis validees/
# ---------------------------------------------------------------------------

def _dossier(racine: str, nom: str) -> str:
    chemin = os.path.join(racine, nom)
    os.makedirs(chemin, exist_ok=True)
    return chemin


def _ecrire_fiche(chemin: str, entete: dict, fiche: dict) -> None:
    """Ecrit une fiche en Markdown avec son en-tete YAML."""
    bloc_yaml = yaml.safe_dump(entete, allow_unicode=True, sort_keys=False)
    points = "\n".join(f"- {p}" for p in fiche["points_forts"])
    contenu = (
        f"---\n{bloc_yaml}---\n\n"
        f"# {fiche['titre']}\n\n"
        f"{fiche['description']}\n\n"
        f"## Points forts\n\n{points}\n"
    )
    with open(chemin, "w", encoding="utf-8") as fichier:
        fichier.write(contenu)


def lire_fiche(chemin: str) -> tuple[dict, str]:
    """Lit une fiche Markdown et retourne (en-tete YAML, corps)."""
    with open(chemin, encoding="utf-8") as fichier:
        contenu = fichier.read()
    correspondance = re.match(r"^---\n(.*?)\n---\n(.*)$", contenu, re.DOTALL)
    if not correspondance:
        raise ValueError(f"Fiche sans en-tete YAML valide : {chemin}")
    entete = yaml.safe_load(correspondance.group(1))
    return entete, correspondance.group(2)


def _generer_et_deposer(produit: dict, racine: str, generateur=None) -> dict:
    """Genere la fiche d'UN produit, garde-fous compris, depot en a_valider/.

    generateur, si fourni, remplace la chaine de moteurs par defaut : il
    recoit le produit et retourne (fiche, mode).

    Retourne le meme compte rendu unitaire que generer() :
    - statut "en_attente" avec le chemin du fichier depose, ou
    - statut "rejetee" avec la liste des motifs (aucun fichier ecrit).
    """
    dossier_attente = _dossier(racine, NOM_DOSSIER_A_VALIDER)
    fiche, mode = (generateur or generer_fiche)(produit)
    motifs = verifier_fiche(fiche)
    if motifs:
        return {
            "id_produit": produit["id_produit"],
            "statut": "rejetee",
            "mode": mode,
            "motifs": motifs,
            "chemin": None,
        }
    chemin = os.path.join(dossier_attente, f"{produit['id_produit']}.md")
    entete = {
        "id_produit": produit["id_produit"],
        "nom_produit": produit["nom_produit"],
        "statut": "en_attente",
        "mode": mode,
        "genere_le": datetime.date.today().isoformat(),
    }
    _ecrire_fiche(chemin, entete, fiche)
    return {
        "id_produit": produit["id_produit"],
        "statut": "en_attente",
        "mode": mode,
        "motifs": [],
        "chemin": chemin,
    }


def generer(limite: int = 3, racine: str = RACINE_PROJET) -> list[dict]:
    """Genere les fiches, applique les garde-fous, depose en a_valider/.

    Retourne un compte rendu par produit :
    - statut "en_attente" avec le chemin du fichier depose, ou
    - statut "rejetee" avec la liste des motifs (aucun fichier ecrit).
    """
    return [
        _generer_et_deposer(produit, racine) for produit in lire_produits(limite)
    ]


class FicheExistanteErreur(Exception):
    """Levee quand une fiche en attente ou validee existe deja."""


def _gabarit_en_filet(produit: dict) -> tuple[dict, str]:
    """Generateur de repli : gabarit deterministe, toujours conforme."""
    return generer_fiche_gabarit(produit), "gabarit"


def _deposer_avec_filet(produit: dict, racine: str) -> dict:
    """Depose la fiche d'un produit avec le gabarit en filet de securite.

    La chaine de moteurs retombe deja sur le gabarit quand un moteur IA
    echoue (exception) ; ce filet couvre l'autre cas : un moteur IA qui
    repond mais dont le brouillon est refuse par les garde-fous. Le gabarit
    deterministe prend alors le relais, et son brouillon repasse par les
    memes garde-fous avant depot.
    """
    resultat = _generer_et_deposer(produit, racine)
    if resultat["statut"] == "rejetee" and resultat["mode"] != "gabarit":
        motifs_ia = resultat["motifs"]
        resultat = _generer_et_deposer(produit, racine, generateur=_gabarit_en_filet)
        resultat["motifs_moteur_ia"] = motifs_ia
    return resultat


def _archiver_fiche(id_produit: str, racine: str) -> list[str]:
    """Archive les fiches existantes (a_valider/ et validees/) d'un produit.

    Les fichiers sont deplaces vers archivees/ avec un suffixe horodate,
    pour garder l'historique sans bloquer la regeneration. Retourne les
    chemins archives.
    """
    dossier_archives = _dossier(racine, NOM_DOSSIER_ARCHIVEES)
    horodatage = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    archives = []
    for nom_dossier in (NOM_DOSSIER_A_VALIDER, NOM_DOSSIER_VALIDEES):
        chemin = os.path.join(racine, nom_dossier, f"{id_produit}.md")
        if os.path.exists(chemin):
            cible = os.path.join(
                dossier_archives, f"{id_produit}-{nom_dossier}-{horodatage}.md"
            )
            os.replace(chemin, cible)
            archives.append(cible)
    return archives


def generer_pour(
    id_produit: str, racine: str = RACINE_PROJET, remplacer: bool = False
) -> dict:
    """Genere la fiche d'un produit precis du referentiel.

    - id inconnu du referentiel : ValueError avec un message clair ;
    - fiche deja en attente ou validee : FicheExistanteErreur, sauf si
      remplacer=True (l'ancienne fiche est alors archivee en archivees/).
    Une fiche rejetee ne bloque jamais la regeneration.
    """
    produits = [
        p for p in lire_referentiel() if p["id_produit"] == id_produit
    ]
    if not produits:
        raise ValueError(
            f"Produit inconnu du referentiel : {id_produit}. "
            "Voir python -m fiches_produits couverture pour la liste."
        )
    statut = statut_fiche(id_produit, racine)
    archives: list[str] = []
    if statut in ("validee", "en_attente"):
        if not remplacer:
            raise FicheExistanteErreur(
                f"Une fiche {statut.replace('_', ' ')} existe deja pour "
                f"{id_produit} : generation refusee. Utilisez --remplacer "
                "pour archiver l'ancienne et regenerer."
            )
        archives = _archiver_fiche(id_produit, racine)
    resultat = dict(_deposer_avec_filet(produits[0], racine))
    resultat["archives"] = archives
    return resultat


def generer_manquantes(
    racine: str = RACINE_PROJET,
    inclure_rejetees: bool = False,
    rappel=None,
) -> dict:
    """Genere une fiche pour tous les produits du referentiel sans fiche.

    Cibles : statut "aucune" (et "rejetee" si inclure_rejetees=True). La
    generation est sequentielle et continue malgre les echecs individuels
    (exception d'un moteur ou refus des garde-fous). Si le brouillon d'un
    moteur IA est refuse par les garde-fous, le gabarit deterministe prend
    le relais (filet de securite). rappel, si fourni, est appele avant
    chaque produit avec (fait, total, produit) pour suivre la progression.

    Retourne un bilan : {"generees", "echouees", "ignorees", "resultats"}.
    """
    cibles_statuts = {"aucune"} | ({"rejetee"} if inclure_rejetees else set())
    couverture = lister_couverture(racine)
    cibles = [p for p in couverture if p["statut"] in cibles_statuts]
    ignorees = len(couverture) - len(cibles)

    generees, echouees, resultats = 0, 0, []
    for rang, produit in enumerate(cibles):
        if rappel is not None:
            rappel(rang, len(cibles), produit)
        try:
            resultat = _deposer_avec_filet(produit, racine)
        except Exception as erreur:  # un echec n'arrete jamais la serie
            resultat = {
                "id_produit": produit["id_produit"],
                "statut": "echec",
                "mode": None,
                "motifs": [f"{type(erreur).__name__}: {erreur}"],
                "chemin": None,
            }
        if resultat["statut"] == "en_attente":
            generees += 1
        else:
            echouees += 1
        resultats.append(resultat)
    if rappel is not None:
        rappel(len(cibles), len(cibles), None)
    return {
        "generees": generees,
        "echouees": echouees,
        "ignorees": ignorees,
        "resultats": resultats,
    }


def approuver(id_produit: str, racine: str = RACINE_PROJET) -> str:
    """Validation humaine : bascule une fiche de a_valider/ vers validees/.

    Change le statut en "validee", date l'approbation, deplace le fichier,
    et retourne le chemin de la fiche validee.
    """
    chemin_attente = os.path.join(
        racine, NOM_DOSSIER_A_VALIDER, f"{id_produit}.md"
    )
    if not os.path.exists(chemin_attente):
        raise FileNotFoundError(
            f"Aucune fiche en attente pour {id_produit} : {chemin_attente}"
        )
    entete, corps = lire_fiche(chemin_attente)
    entete["statut"] = "validee"
    entete["approuvee_le"] = datetime.date.today().isoformat()

    dossier_validees = _dossier(racine, NOM_DOSSIER_VALIDEES)
    chemin_validee = os.path.join(dossier_validees, f"{id_produit}.md")
    bloc_yaml = yaml.safe_dump(entete, allow_unicode=True, sort_keys=False)
    with open(chemin_validee, "w", encoding="utf-8") as fichier:
        fichier.write(f"---\n{bloc_yaml}---\n{corps}")
    os.remove(chemin_attente)
    return chemin_validee


def approuver_tout(racine: str = RACINE_PROJET) -> dict:
    """Validation humaine en masse : approuve toutes les fiches en attente.

    Reste un geste humain : cette fonction n'est appelee que par un clic
    explicite (bouton Tout valider, confirme en deux temps dans le cockpit).
    Retourne {"nb": n, "validees": [ids], "echecs": [{id_produit, erreur}]}.
    """
    ids = sorted(_ids_dossier(racine, NOM_DOSSIER_A_VALIDER))
    validees: list[str] = []
    echecs: list[dict] = []
    for id_produit in ids:
        try:
            approuver(id_produit, racine=racine)
            validees.append(id_produit)
        except Exception as erreur:
            echecs.append(
                {"id_produit": id_produit, "erreur": f"{type(erreur).__name__}: {erreur}"}
            )
    return {"nb": len(validees), "validees": validees, "echecs": echecs}


def rejeter(id_produit: str, motif: str, racine: str = RACINE_PROJET) -> str:
    """Rejet humain : bascule une fiche de a_valider/ vers rejetees/.

    Change le statut en "rejetee", enregistre le motif et la date du rejet
    dans l'en-tete YAML, deplace le fichier, et retourne le chemin de la
    fiche rejetee.
    """
    motif = (motif or "").strip()
    if not motif:
        raise ValueError("Un motif de rejet est obligatoire.")
    chemin_attente = os.path.join(
        racine, NOM_DOSSIER_A_VALIDER, f"{id_produit}.md"
    )
    if not os.path.exists(chemin_attente):
        raise FileNotFoundError(
            f"Aucune fiche en attente pour {id_produit} : {chemin_attente}"
        )
    entete, corps = lire_fiche(chemin_attente)
    entete["statut"] = "rejetee"
    entete["motif_rejet"] = motif
    entete["rejetee_le"] = datetime.date.today().isoformat()

    dossier_rejetees = _dossier(racine, NOM_DOSSIER_REJETEES)
    chemin_rejetee = os.path.join(dossier_rejetees, f"{id_produit}.md")
    bloc_yaml = yaml.safe_dump(entete, allow_unicode=True, sort_keys=False)
    with open(chemin_rejetee, "w", encoding="utf-8") as fichier:
        fichier.write(f"---\n{bloc_yaml}---\n{corps}")
    os.remove(chemin_attente)
    return chemin_rejetee
