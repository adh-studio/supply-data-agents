"""Tests du module rapport_hebdo contre la base fixture.

Les valeurs attendues sont calculees a la main depuis les CSV de fixtures/
et ecrites en dur : tout changement de fixture doit etre repercute ici.
Aucun appel reseau : la partie LLM est mockee ou passe par le repli.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from rapport_hebdo import moteur


@pytest.fixture()
def kpi():
    return moteur.calculer_kpi()


HORODATAGE_FIXE = datetime(2026, 11, 9, 7, 30)


def _sans_horodatage(texte: str) -> str:
    """Retire les lignes d'horodatage pour comparer deux generations."""
    return "\n".join(
        ligne for ligne in texte.splitlines() if "Genere le" not in ligne
    )


# ---------------------------------------------------------------------------
# KPI exacts
# ---------------------------------------------------------------------------

class TestKpi:
    def test_taux_rupture_global(self, kpi):
        rupture = kpi["rupture"]
        # 141 ruptures observees sur 4320 observations : 3.26 %.
        assert rupture["nb_observations"] == 4320
        assert rupture["nb_ruptures"] == 141
        assert rupture["taux_global_pct"] == 3.26

    def test_taux_rupture_par_entrepot(self, kpi):
        par_entrepot = {
            entrepot["id_entrepot"]: entrepot
            for entrepot in kpi["rupture"]["par_entrepot"]
        }
        assert set(par_entrepot) == {"E01", "E02"}
        assert par_entrepot["E01"]["nb_observations"] == 2400
        assert par_entrepot["E01"]["nb_ruptures"] == 93
        assert par_entrepot["E01"]["taux_rupture_pct"] == 3.88
        assert par_entrepot["E02"]["nb_observations"] == 1920
        assert par_entrepot["E02"]["nb_ruptures"] == 48
        assert par_entrepot["E02"]["taux_rupture_pct"] == 2.5

    def test_top_3_familles_par_marge(self, kpi):
        top = kpi["top_familles_marge"]
        assert [famille["famille"] for famille in top] == [
            "Objets d art",
            "Decoration",
            "Miroirs",
        ]
        assert [famille["taux_marge"] for famille in top] == [61.4, 58.9, 55.3]
        assert top[1]["quantite_vendue"] == 2150
        assert top[1]["chiffre_affaires"] == 96750.00

    def test_pire_rotation(self, kpi):
        assert kpi["pire_rotation"] == {
            "famille": "Objets d art",
            "rotation_periode": 0.6,
        }

    def test_otif_moyen_et_pires_fournisseurs(self, kpi):
        otif = kpi["otif"]
        assert otif["nb_fournisseurs"] == 15
        assert otif["moyen_pct"] == pytest.approx(82.93, abs=0.01)
        assert [f["nom_fournisseur"] for f in otif["pires"]] == [
            "Tissages du Maroc",
            "Mobilier Atlas Export",
            "Metal Design Torino",
        ]
        assert [f["taux_otif"] for f in otif["pires"]] == [41.67, 50.0, 60.0]
        assert [f["pays"] for f in otif["pires"]] == ["Maroc", "Maroc", "Italie"]

    def test_produits_dormants(self, kpi):
        dormants = kpi["dormants"]
        assert dormants["nb"] == 3
        # Tries par quantite disponible decroissante.
        assert [p["id_produit"] for p in dormants["produits"]] == [
            "P009",
            "P017",
            "P012",
        ]
        # Un produit dormant ne se vend plus : couverture non calculable.
        assert dormants["produits"][0]["couverture_jours"] is None
        assert dormants["produits"][0]["quantite_disponible"] == 210
        assert dormants["produits"][0]["nom_produit"] == "Tapis shaggy Como taupe"


# ---------------------------------------------------------------------------
# Partie narrative
# ---------------------------------------------------------------------------

class TestPuces:
    def test_trois_puces_deterministes(self, kpi, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        puces = moteur.generer_puces(kpi)
        assert len(puces) == 3
        assert all(isinstance(puce, str) and puce for puce in puces)

    def test_regles_metier_appliquees(self, kpi, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        puces = moteur.generer_puces(kpi)
        # Rupture a 3.26 % : sous le seuil de 5 %, pas d'alerte.
        assert "Disponibilite maitrisee" in puces[0]
        assert "3.26 %" in puces[0]
        # OTIF moyen a 82.93 % : sous le seuil de 85 %, alerte avec le pire.
        assert "sous le seuil" in puces[1]
        assert "Tissages du Maroc" in puces[1]
        # Offre : meilleure marge et dormants.
        assert "Objets d art" in puces[2]
        assert "3 produits dormants" in puces[2]

    def test_sans_cle_pas_d_appel_llm(self, kpi, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        def explosion(_kpi):
            raise AssertionError("appel LLM interdit sans cle")

        monkeypatch.setattr(moteur, "_puces_llm", explosion)
        puces = moteur.generer_puces(kpi)
        assert len(puces) == 3

    def test_avec_cle_le_llm_est_utilise(self, kpi, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-de-test")
        attendues = ["puce une", "puce deux", "puce trois"]
        monkeypatch.setattr(moteur, "_puces_llm", lambda _kpi: list(attendues))
        assert moteur.generer_puces(kpi) == attendues

    def test_avec_cle_mais_echec_llm_repli(self, kpi, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-de-test")
        monkeypatch.setattr(moteur, "_puces_llm", lambda _kpi: None)
        puces = moteur.generer_puces(kpi)
        assert puces == moteur.puces_deterministes(kpi)


# ---------------------------------------------------------------------------
# Sorties markdown et HTML
# ---------------------------------------------------------------------------

class TestSorties:
    def test_markdown_bien_forme(self, kpi, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        puces = moteur.puces_deterministes(kpi)
        markdown = moteur.rendre_markdown(kpi, puces, HORODATAGE_FIXE)

        assert markdown.startswith("# Rapport hebdomadaire supply et offre")
        for section in [
            "## Ce qu'il faut retenir cette semaine",
            "## Taux de rupture",
            "## Top 3 familles par taux de marge",
            "## Rotation",
            "## Fiabilite fournisseurs (OTIF)",
            "## Produits dormants",
        ]:
            assert section in markdown
        # Trois puces.
        assert sum(1 for l in markdown.splitlines() if l.startswith("- ")) == 3
        # Tables markdown avec separateurs d'alignement.
        assert markdown.count("| --- |") >= 1
        assert "| E01 | 2400 | 93 | 3.88 % |" in markdown
        assert "| Tissages du Maroc | Maroc | 41.67 % |" in markdown
        # Pas de tiret cadratin.
        assert "—" not in markdown

    def test_html_contient_les_sections_attendues(self, kpi, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        puces = moteur.puces_deterministes(kpi)
        html = moteur.rendre_html(kpi, puces, HORODATAGE_FIXE)

        assert html.startswith("<!DOCTYPE html>")
        assert "<title>Rapport hebdomadaire supply et offre</title>" in html
        for section in [
            "Ce qu'il faut retenir cette semaine",
            "Taux de rupture par entrepot",
            "Top 3 familles par taux de marge",
            "Fiabilite fournisseurs (OTIF)",
            "Produits dormants",
        ]:
            assert section in html
        # CSS inline et mini-barres SVG inline.
        assert "<style>" in html
        assert "<svg" in html
        # Valeurs cles visibles par un dirigeant.
        assert "3.26 %" in html
        assert "82.93 %" in html
        assert "Tapis shaggy Como taupe" in html

    def test_generer_rapport_ecrit_les_deux_fichiers(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        resultat = moteur.generer_rapport(
            dossier_sortie=str(tmp_path), horodatage=HORODATAGE_FIXE
        )
        chemin_md = tmp_path / "rapport.md"
        chemin_html = tmp_path / "docs" / "rapport-hebdo.html"
        assert chemin_md.exists()
        assert chemin_html.exists()
        assert resultat["markdown"] == str(chemin_md)
        assert resultat["html"] == str(chemin_html)
        assert "Genere le 09/11/2026 a 07:30" in chemin_md.read_text("utf-8")

    def test_mode_deterministe_stable(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        dossier_1 = tmp_path / "run1"
        dossier_2 = tmp_path / "run2"
        dossier_1.mkdir()
        dossier_2.mkdir()

        moteur.generer_rapport(dossier_sortie=str(dossier_1))
        moteur.generer_rapport(dossier_sortie=str(dossier_2))

        md_1 = (dossier_1 / "rapport.md").read_text("utf-8")
        md_2 = (dossier_2 / "rapport.md").read_text("utf-8")
        html_1 = (dossier_1 / "docs" / "rapport-hebdo.html").read_text("utf-8")
        html_2 = (dossier_2 / "docs" / "rapport-hebdo.html").read_text("utf-8")

        assert _sans_horodatage(md_1) == _sans_horodatage(md_2)
        assert _sans_horodatage(html_1) == _sans_horodatage(html_2)
