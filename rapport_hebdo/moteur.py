"""Moteur du rapport hebdomadaire : calcul des KPI et generation des sorties.

Les KPI sont calcules depuis les marts de l'entrepot (via lib.base.requete,
donc en lecture seule et sur les seules tables autorisees) :

- taux de rupture global et par entrepot (dernier jour connu) ;
- top 3 des familles par taux de marge ;
- pire rotation de la periode ;
- OTIF moyen des fournisseurs et fournisseurs les plus en retard ;
- produits dormants (stock qui ne tourne plus).

La partie narrative ("ce qu'il faut retenir cette semaine", 3 puces) passe
par le LLM si ANTHROPIC_API_KEY est presente, sinon par un gabarit
deterministe construit sur des regles metier.
"""

from __future__ import annotations

import html as html_lib
import os
from datetime import datetime

from lib.base import requete

# Seuils metier utilises par le gabarit deterministe.
SEUIL_ALERTE_RUPTURE_PCT = 5.0
SEUIL_ALERTE_OTIF_PCT = 85.0

MODELE_LLM = "claude-sonnet-5"


# ---------------------------------------------------------------------------
# Calcul des KPI
# ---------------------------------------------------------------------------

def calculer_kpi() -> dict:
    """Calcule tous les KPI de la semaine et les retourne dans un dict."""
    return {
        "rupture": _kpi_rupture(),
        "top_familles_marge": _kpi_top_familles_marge(),
        "pire_rotation": _kpi_pire_rotation(),
        "otif": _kpi_otif(),
        "dormants": _kpi_dormants(),
    }


def _kpi_rupture() -> dict:
    """Taux de rupture global et par entrepot sur le dernier jour connu."""
    lignes = requete(
        """
        SELECT
            id_entrepot,
            SUM(nb_references_actives) AS nb_references_actives,
            SUM(nb_references_rupture) AS nb_references_rupture
        FROM supply_taux_rupture
        WHERE date_jour = (SELECT MAX(date_jour) FROM supply_taux_rupture)
        GROUP BY id_entrepot
        ORDER BY id_entrepot
        """
    )
    par_entrepot = []
    total_actives = 0
    total_ruptures = 0
    for ligne in lignes:
        actives = int(ligne["nb_references_actives"])
        ruptures = int(ligne["nb_references_rupture"])
        total_actives += actives
        total_ruptures += ruptures
        par_entrepot.append(
            {
                "id_entrepot": ligne["id_entrepot"],
                "nb_references_actives": actives,
                "nb_references_rupture": ruptures,
                "taux_rupture_pct": round(100.0 * ruptures / actives, 2)
                if actives
                else 0.0,
            }
        )
    taux_global = (
        round(100.0 * total_ruptures / total_actives, 2) if total_actives else 0.0
    )
    return {
        "taux_global_pct": taux_global,
        "nb_references_actives": total_actives,
        "nb_references_rupture": total_ruptures,
        "par_entrepot": par_entrepot,
    }


def _kpi_top_familles_marge() -> list[dict]:
    """Top 3 des familles par taux de marge."""
    lignes = requete(
        """
        SELECT famille, quantite_vendue, chiffre_affaires, taux_marge
        FROM offre_marge_familles
        ORDER BY taux_marge DESC, famille ASC
        LIMIT 3
        """
    )
    return [
        {
            "famille": ligne["famille"],
            "quantite_vendue": int(ligne["quantite_vendue"]),
            "chiffre_affaires": round(float(ligne["chiffre_affaires"]), 2),
            "taux_marge": round(float(ligne["taux_marge"]), 2),
        }
        for ligne in lignes
    ]


def _kpi_pire_rotation() -> dict:
    """Famille dont la rotation de la periode est la plus faible."""
    lignes = requete(
        """
        SELECT famille, rotation_periode
        FROM offre_marge_familles
        ORDER BY rotation_periode ASC, famille ASC
        LIMIT 1
        """
    )
    ligne = lignes[0]
    return {
        "famille": ligne["famille"],
        "rotation_periode": round(float(ligne["rotation_periode"]), 2),
    }


def _kpi_otif() -> dict:
    """OTIF moyen des fournisseurs et les 3 fournisseurs les plus en retard."""
    lignes = requete(
        """
        SELECT nom_fournisseur, famille_principale, taux_otif
        FROM supply_delais_fournisseurs
        ORDER BY taux_otif ASC, nom_fournisseur ASC
        """
    )
    valeurs = [float(ligne["taux_otif"]) for ligne in lignes]
    moyen = round(sum(valeurs) / len(valeurs), 2) if valeurs else 0.0
    pires = [
        {
            "nom_fournisseur": ligne["nom_fournisseur"],
            "famille_principale": ligne["famille_principale"],
            "taux_otif": round(float(ligne["taux_otif"]), 2),
        }
        for ligne in lignes[:3]
    ]
    return {"moyen_pct": moyen, "nb_fournisseurs": len(valeurs), "pires": pires}


def _kpi_dormants() -> dict:
    """Produits dont le stock ne tourne plus (statut dormant)."""
    lignes = requete(
        """
        SELECT id_produit, nom_produit, famille, id_entrepot,
               stock_disponible, couverture_jours
        FROM supply_couverture_stock
        WHERE statut_couverture = 'dormant'
        ORDER BY couverture_jours DESC, id_produit ASC
        """
    )
    produits = [
        {
            "id_produit": ligne["id_produit"],
            "nom_produit": ligne["nom_produit"],
            "famille": ligne["famille"],
            "id_entrepot": ligne["id_entrepot"],
            "stock_disponible": int(ligne["stock_disponible"]),
            "couverture_jours": round(float(ligne["couverture_jours"]), 1),
        }
        for ligne in lignes
    ]
    return {"nb": len(produits), "produits": produits}


# ---------------------------------------------------------------------------
# Partie narrative : ce qu'il faut retenir cette semaine
# ---------------------------------------------------------------------------

def generer_puces(kpi: dict) -> list[str]:
    """Retourne les 3 puces de synthese : LLM si cle presente, sinon regles."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        puces = _puces_llm(kpi)
        if puces:
            return puces
    return puces_deterministes(kpi)


def _puces_llm(kpi: dict) -> list[str] | None:
    """Demande 3 puces de synthese au LLM. Retourne None en cas d'echec."""
    try:
        import anthropic

        client = anthropic.Anthropic()
        consigne = (
            "Tu rediges la synthese hebdomadaire supply et offre d'une enseigne "
            "de decoration et d'ameublement, destinee aux Directions Metiers. "
            "A partir des KPI ci-dessous, redige exactement 3 puces en francais, "
            "une phrase par puce, factuelles et actionnables, sans tiret "
            "cadratin. Reponds uniquement avec les 3 puces, une par ligne, "
            "chacune commencant par '- '.\n\nKPI de la semaine :\n" + repr(kpi)
        )
        reponse = client.messages.create(
            model=MODELE_LLM,
            max_tokens=500,
            messages=[{"role": "user", "content": consigne}],
        )
        texte = "".join(
            bloc.text for bloc in reponse.content if getattr(bloc, "type", "") == "text"
        )
        puces = [
            ligne.lstrip("- ").strip()
            for ligne in texte.splitlines()
            if ligne.strip().startswith("-")
        ]
        return puces[:3] if len(puces) >= 3 else None
    except Exception:
        return None


def puces_deterministes(kpi: dict) -> list[str]:
    """Gabarit de repli : 3 puces construites sur des regles metier."""
    puces = []

    # Puce 1 : disponibilite produit.
    rupture = kpi["rupture"]
    pire_entrepot = max(
        rupture["par_entrepot"], key=lambda e: e["taux_rupture_pct"], default=None
    )
    if rupture["taux_global_pct"] > SEUIL_ALERTE_RUPTURE_PCT:
        puces.append(
            f"Alerte disponibilite : le taux de rupture atteint "
            f"{rupture['taux_global_pct']} % ({rupture['nb_references_rupture']} "
            f"references sur {rupture['nb_references_actives']}), "
            f"entrepot le plus touche : {pire_entrepot['id_entrepot']} "
            f"a {pire_entrepot['taux_rupture_pct']} %."
        )
    else:
        puces.append(
            f"Disponibilite maitrisee : taux de rupture global de "
            f"{rupture['taux_global_pct']} % ({rupture['nb_references_rupture']} "
            f"references sur {rupture['nb_references_actives']}), "
            f"point de vigilance sur l'entrepot {pire_entrepot['id_entrepot']} "
            f"a {pire_entrepot['taux_rupture_pct']} %."
        )

    # Puce 2 : fiabilite fournisseurs.
    otif = kpi["otif"]
    pire_fournisseur = otif["pires"][0] if otif["pires"] else None
    if otif["moyen_pct"] < SEUIL_ALERTE_OTIF_PCT:
        puces.append(
            f"Fiabilite fournisseurs sous le seuil : OTIF moyen de "
            f"{otif['moyen_pct']} % (objectif {SEUIL_ALERTE_OTIF_PCT:.0f} %), "
            f"plan d'action a ouvrir avec {pire_fournisseur['nom_fournisseur']} "
            f"({pire_fournisseur['taux_otif']} %)."
        )
    else:
        puces.append(
            f"Fiabilite fournisseurs correcte : OTIF moyen de "
            f"{otif['moyen_pct']} %, surveiller toutefois "
            f"{pire_fournisseur['nom_fournisseur']} "
            f"({pire_fournisseur['taux_otif']} %)."
        )

    # Puce 3 : offre, marge et stocks qui dorment.
    top = kpi["top_familles_marge"][0]
    dormants = kpi["dormants"]
    pire_rotation = kpi["pire_rotation"]
    puces.append(
        f"Cote offre, la famille {top['famille']} affiche la meilleure marge "
        f"({top['taux_marge']} %), mais {dormants['nb']} produits dormants et "
        f"la rotation la plus faible sur {pire_rotation['famille']} "
        f"({pire_rotation['rotation_periode']}) appellent un destockage cible."
    )

    return puces


# ---------------------------------------------------------------------------
# Rendu markdown
# ---------------------------------------------------------------------------

def rendre_markdown(kpi: dict, puces: list[str], horodatage: datetime) -> str:
    """Construit le rapport markdown (email / Slack)."""
    rupture = kpi["rupture"]
    otif = kpi["otif"]
    dormants = kpi["dormants"]

    lignes = [
        "# Rapport hebdomadaire supply et offre",
        "",
        f"Genere le {horodatage.strftime('%d/%m/%Y a %H:%M')}.",
        "",
        "## Ce qu'il faut retenir cette semaine",
        "",
    ]
    lignes += [f"- {puce}" for puce in puces]
    lignes += [
        "",
        "## Taux de rupture",
        "",
        f"Taux global : **{rupture['taux_global_pct']} %** "
        f"({rupture['nb_references_rupture']} references en rupture "
        f"sur {rupture['nb_references_actives']} actives).",
        "",
        "| Entrepot | References actives | En rupture | Taux |",
        "| --- | ---: | ---: | ---: |",
    ]
    for entrepot in rupture["par_entrepot"]:
        lignes.append(
            f"| {entrepot['id_entrepot']} | {entrepot['nb_references_actives']} "
            f"| {entrepot['nb_references_rupture']} "
            f"| {entrepot['taux_rupture_pct']} % |"
        )
    lignes += [
        "",
        "## Top 3 familles par taux de marge",
        "",
        "| Famille | Quantite vendue | Chiffre d'affaires | Taux de marge |",
        "| --- | ---: | ---: | ---: |",
    ]
    for famille in kpi["top_familles_marge"]:
        lignes.append(
            f"| {famille['famille']} | {famille['quantite_vendue']} "
            f"| {famille['chiffre_affaires']:,.0f} EUR".replace(",", " ")
            + f" | {famille['taux_marge']} % |"
        )
    pire_rotation = kpi["pire_rotation"]
    lignes += [
        "",
        "## Rotation",
        "",
        f"Pire rotation de la periode : **{pire_rotation['famille']}** "
        f"({pire_rotation['rotation_periode']}).",
        "",
        "## Fiabilite fournisseurs (OTIF)",
        "",
        f"OTIF moyen sur {otif['nb_fournisseurs']} fournisseurs : "
        f"**{otif['moyen_pct']} %**.",
        "",
        "| Fournisseur a surveiller | Famille principale | OTIF |",
        "| --- | --- | ---: |",
    ]
    for fournisseur in otif["pires"]:
        lignes.append(
            f"| {fournisseur['nom_fournisseur']} "
            f"| {fournisseur['famille_principale']} "
            f"| {fournisseur['taux_otif']} % |"
        )
    lignes += [
        "",
        "## Produits dormants",
        "",
        f"{dormants['nb']} produits ne tournent plus :",
        "",
        "| Produit | Famille | Entrepot | Stock | Couverture (jours) |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for produit in dormants["produits"]:
        lignes.append(
            f"| {produit['nom_produit']} | {produit['famille']} "
            f"| {produit['id_entrepot']} | {produit['stock_disponible']} "
            f"| {produit['couverture_jours']} |"
        )
    lignes.append("")
    return "\n".join(lignes)


# ---------------------------------------------------------------------------
# Rendu HTML
# ---------------------------------------------------------------------------

_CSS = """
  :root { color-scheme: light; }
  body { margin: 0; padding: 32px 16px; background: #f4f1ec;
         font-family: Georgia, 'Times New Roman', serif; color: #2b2520; }
  .page { max-width: 880px; margin: 0 auto; background: #fffdf9;
          border: 1px solid #e3dcd2; border-radius: 12px; padding: 40px 48px;
          box-shadow: 0 2px 12px rgba(43, 37, 32, 0.06); }
  h1 { font-size: 1.7rem; margin: 0 0 4px; letter-spacing: 0.01em; }
  h2 { font-size: 1.05rem; margin: 36px 0 12px; text-transform: uppercase;
       letter-spacing: 0.08em; color: #8a6d3b;
       border-bottom: 1px solid #e3dcd2; padding-bottom: 6px; }
  .horodatage { color: #857a6d; font-size: 0.9rem; margin: 0 0 24px; }
  .retenir { background: #f8f4ec; border-left: 4px solid #8a6d3b;
             border-radius: 0 8px 8px 0; padding: 16px 20px; }
  .retenir ul { margin: 0; padding-left: 20px; }
  .retenir li { margin: 8px 0; line-height: 1.5; }
  .tuiles { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 16px; }
  .tuile { flex: 1 1 150px; background: #faf7f1; border: 1px solid #e3dcd2;
           border-radius: 10px; padding: 14px 16px; }
  .tuile .valeur { font-size: 1.5rem; font-weight: bold; }
  .tuile .libelle { font-size: 0.8rem; color: #857a6d; margin-top: 4px;
                    font-family: Helvetica, Arial, sans-serif; }
  .alerte .valeur { color: #a94431; }
  .ok .valeur { color: #4a7a4c; }
  table { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
  th { text-align: left; font-family: Helvetica, Arial, sans-serif;
       font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em;
       color: #857a6d; padding: 8px 10px; border-bottom: 2px solid #e3dcd2; }
  td { padding: 8px 10px; border-bottom: 1px solid #efe9df; }
  td.nombre, th.nombre { text-align: right; font-variant-numeric: tabular-nums; }
  .enveloppe-table { overflow-x: auto; }
  svg { display: block; }
  .note { color: #857a6d; font-size: 0.85rem; margin-top: 28px;
          border-top: 1px solid #e3dcd2; padding-top: 12px; }
"""


def _barre_svg(valeur: float, maximum: float, couleur: str) -> str:
    """Mini-barre horizontale SVG inline proportionnelle a la valeur."""
    largeur_totale = 120
    ratio = min(max(valeur / maximum, 0.0), 1.0) if maximum > 0 else 0.0
    largeur = max(round(largeur_totale * ratio), 2)
    return (
        f'<svg width="{largeur_totale}" height="10" '
        f'viewBox="0 0 {largeur_totale} 10" role="img" '
        f'aria-label="{valeur}">'
        f'<rect width="{largeur_totale}" height="10" rx="5" fill="#efe9df"/>'
        f'<rect width="{largeur}" height="10" rx="5" fill="{couleur}"/>'
        f"</svg>"
    )


def rendre_html(kpi: dict, puces: list[str], horodatage: datetime) -> str:
    """Construit la page HTML autonome du rapport."""
    e = html_lib.escape
    rupture = kpi["rupture"]
    otif = kpi["otif"]
    dormants = kpi["dormants"]
    pire_rotation = kpi["pire_rotation"]

    classe_rupture = (
        "alerte" if rupture["taux_global_pct"] > SEUIL_ALERTE_RUPTURE_PCT else "ok"
    )
    classe_otif = "alerte" if otif["moyen_pct"] < SEUIL_ALERTE_OTIF_PCT else "ok"

    lignes_rupture = "".join(
        f"<tr><td>{e(str(x['id_entrepot']))}</td>"
        f"<td class='nombre'>{x['nb_references_actives']}</td>"
        f"<td class='nombre'>{x['nb_references_rupture']}</td>"
        f"<td class='nombre'>{x['taux_rupture_pct']} %</td>"
        f"<td>{_barre_svg(x['taux_rupture_pct'], 10.0, '#a94431')}</td></tr>"
        for x in rupture["par_entrepot"]
    )
    lignes_marge = "".join(
        f"<tr><td>{e(x['famille'])}</td>"
        f"<td class='nombre'>{x['quantite_vendue']}</td>"
        f"<td class='nombre'>{x['chiffre_affaires']:,.0f} EUR</td>".replace(",", " ")
        + f"<td class='nombre'>{x['taux_marge']} %</td>"
        f"<td>{_barre_svg(x['taux_marge'], 100.0, '#8a6d3b')}</td></tr>"
        for x in kpi["top_familles_marge"]
    )
    lignes_otif = "".join(
        f"<tr><td>{e(x['nom_fournisseur'])}</td>"
        f"<td>{e(x['famille_principale'])}</td>"
        f"<td class='nombre'>{x['taux_otif']} %</td>"
        f"<td>{_barre_svg(x['taux_otif'], 100.0, '#a94431')}</td></tr>"
        for x in otif["pires"]
    )
    lignes_dormants = "".join(
        f"<tr><td>{e(x['nom_produit'])}</td><td>{e(x['famille'])}</td>"
        f"<td>{e(str(x['id_entrepot']))}</td>"
        f"<td class='nombre'>{x['stock_disponible']}</td>"
        f"<td class='nombre'>{x['couverture_jours']}</td></tr>"
        for x in dormants["produits"]
    )
    puces_html = "".join(f"<li>{e(puce)}</li>" for puce in puces)

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rapport hebdomadaire supply et offre</title>
<style>{_CSS}</style>
</head>
<body>
<div class="page">
  <h1>Rapport hebdomadaire supply et offre</h1>
  <p class="horodatage">Genere le {horodatage.strftime('%d/%m/%Y a %H:%M')}</p>

  <h2>Ce qu'il faut retenir cette semaine</h2>
  <div class="retenir"><ul>{puces_html}</ul></div>

  <div class="tuiles">
    <div class="tuile {classe_rupture}">
      <div class="valeur">{rupture['taux_global_pct']} %</div>
      <div class="libelle">Taux de rupture global</div>
    </div>
    <div class="tuile {classe_otif}">
      <div class="valeur">{otif['moyen_pct']} %</div>
      <div class="libelle">OTIF moyen fournisseurs</div>
    </div>
    <div class="tuile">
      <div class="valeur">{dormants['nb']}</div>
      <div class="libelle">Produits dormants</div>
    </div>
    <div class="tuile">
      <div class="valeur">{e(pire_rotation['famille'])}</div>
      <div class="libelle">Pire rotation ({pire_rotation['rotation_periode']})</div>
    </div>
  </div>

  <h2>Taux de rupture par entrepot</h2>
  <div class="enveloppe-table"><table>
    <tr><th>Entrepot</th><th class="nombre">References actives</th>
        <th class="nombre">En rupture</th><th class="nombre">Taux</th><th></th></tr>
    {lignes_rupture}
  </table></div>

  <h2>Top 3 familles par taux de marge</h2>
  <div class="enveloppe-table"><table>
    <tr><th>Famille</th><th class="nombre">Quantite vendue</th>
        <th class="nombre">Chiffre d'affaires</th>
        <th class="nombre">Taux de marge</th><th></th></tr>
    {lignes_marge}
  </table></div>

  <h2>Fiabilite fournisseurs (OTIF)</h2>
  <div class="enveloppe-table"><table>
    <tr><th>Fournisseur a surveiller</th><th>Famille principale</th>
        <th class="nombre">OTIF</th><th></th></tr>
    {lignes_otif}
  </table></div>

  <h2>Produits dormants</h2>
  <div class="enveloppe-table"><table>
    <tr><th>Produit</th><th>Famille</th><th>Entrepot</th>
        <th class="nombre">Stock</th><th class="nombre">Couverture (jours)</th></tr>
    {lignes_dormants}
  </table></div>

  <p class="note">Source : entrepot analytique supply-dbt-warehouse
  (marts valides par dbt). Rapport genere automatiquement chaque lundi matin.</p>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Generation complete
# ---------------------------------------------------------------------------

def generer_rapport(
    dossier_sortie: str | None = None, horodatage: datetime | None = None
) -> dict:
    """Calcule les KPI, genere rapport.md et docs/rapport-hebdo.html.

    Retourne un dict avec les chemins ecrits et les KPI calcules.
    """
    if dossier_sortie is None:
        dossier_sortie = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if horodatage is None:
        horodatage = datetime.now()

    kpi = calculer_kpi()
    puces = generer_puces(kpi)

    chemin_md = os.path.join(dossier_sortie, "rapport.md")
    dossier_docs = os.path.join(dossier_sortie, "docs")
    os.makedirs(dossier_docs, exist_ok=True)
    chemin_html = os.path.join(dossier_docs, "rapport-hebdo.html")

    with open(chemin_md, "w", encoding="utf-8") as fichier:
        fichier.write(rendre_markdown(kpi, puces, horodatage))
    with open(chemin_html, "w", encoding="utf-8") as fichier:
        fichier.write(rendre_html(kpi, puces, horodatage))

    return {"markdown": chemin_md, "html": chemin_html, "kpi": kpi, "puces": puces}
