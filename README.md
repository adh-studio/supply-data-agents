# Supply Data Agents

[![CI](https://github.com/adh-studio/supply-data-agents/actions/workflows/ci.yml/badge.svg)](https://github.com/adh-studio/supply-data-agents/actions/workflows/ci.yml)

**La donnee validee devient de l'action.** Cinq automatisations posees au-dessus
de l'entrepot analytique [supply-dbt-warehouse](https://github.com/adh-studio/supply-dbt-warehouse)
(dbt + DuckDB) d'une enseigne de decoration et d'ameublement : alertes, self-service,
rapports, garde de qualite et generation assistee, toujours avec un humain dans la boucle.

Vitrine pour non-techniciens : https://adh-studio.github.io/supply-data-agents/

## Architecture

```
supply-dbt-warehouse (dbt)          supply-data-agents (ce repo)
  sources -> staging -> marts         la couche ACTION au-dessus des marts
        |
        v
  supply.duckdb -------- lecture seule via lib/base.py -------+
    5 marts :                                                 |
    - supply_couverture_stock      +--------------------------+--------------+
    - supply_taux_rupture          | alerte_rupture   : alerte Slack 7 h     |
    - supply_delais_fournisseurs   | agent_qa         : question -> SQL garde|
    - offre_marge_familles         | rapport_hebdo    : 5 KPI + synthese     |
    - offre_top_flop_produits      | gardien_qualite  : diagnostic tests dbt |
                                   | fiches_produits  : brouillons a valider |
                                   +------------------------------------------+
```

- **Source de donnees** : la variable d'environnement `DUCKDB_PATH` pointe vers
  l'entrepot reel (defaut : `../supply-dbt-warehouse/supply.duckdb`). Toute lecture
  passe par `lib/base.py` : connexion en lecture seule, requetes `SELECT` uniquement,
  allowlist stricte des 5 marts.
- **Mode fixture (tests et CI)** : avec `TEST_MODE=1`, tout tourne sur
  `fixtures/entrepot_test.duckdb`, une petite base construite depuis les CSV du
  dossier `fixtures/` (`python fixtures/construire_base.py`). Les CSV
  reproduisent exactement le schema des 5 marts reels (memes tables, memes
  colonnes, memes types). Aucune dependance au repo voisin ni au reseau : la
  CI est autonome.
- **IA en chaine de replis** (du plus riche au plus sobre), aucun appel reseau
  dans les tests :
  1. API Anthropic (`claude-sonnet-5`) si `ANTHROPIC_API_KEY` est presente ;
  2. **Claude Code en mode headless** (`claude -p`, abonnement Claude, aucune cle
     API ni facturation a l'usage) si la CLI `claude` est installee : l'assistant
     repond alors aux questions libres hors des 8 types connus, et les fiches
     produits sont redigees par le modele. Le SQL genere passe par la MEME garde
     (SELECT unique, allowlist des 5 marts) avant toute execution. Reglages :
     `CLAUDE_CODE_MODELE` (defaut `haiku`), `CLAUDE_CODE_DESACTIVE=1` pour couper ;
  3. mode deterministe de repli (8 questions types, fiches gabarit).
- **Python** : 3.11 a 3.13 (`run_e2e.sh` choisit l'interpreteur et refuse le reste).

## Demarrage rapide

```bash
./run_e2e.sh
```

Le script cree `.venv`, installe `requirements.txt`, construit la base fixture et
lance les 105 tests. C'est exactement ce que fait la CI GitHub Actions a chaque push.

## Les 5 modules, un exemple chacun

Les exemples ci-dessous tournent sur la base fixture (`TEST_MODE=1`), donc sans
entrepot reel ni cle d'API.

### 1. alerte_rupture : alerte Slack quotidienne

```bash
TEST_MODE=1 python -m alerte_rupture --json
```

```json
{"date": "2026-11-13", "nb_ruptures": 3, "nb_surstocks": 0,
 "ruptures": [{"id_produit": "P001", "nom_produit": "Canape 3 places Oslo gris",
               "famille": "Canapes", "id_entrepot": "E01",
               "vente_moyenne_jour": 4.2, "couverture_jours": 0.0,
               "quantite_reappro": 126}, "..."],
 "message": ":rotating_light: *Alerte rupture de stock - 13/11/2026* ...",
 "fichier_outbox": "outbox/alerte-20261113-071500.md"}
```

### 2. agent_qa : question metier en francais

```bash
TEST_MODE=1 python -m agent_qa "quels produits sont en rupture ?"
```

```
3 produits sont en rupture : Canape 3 places Oslo gris (entrepot E01),
Lampadaire arc Milo noir (entrepot E01), Tapis berbere Atlas 160x230 (entrepot E01).
```

Chaque echange (question, SQL execute, reponse, refus eventuels) est trace dans
le journal d'audit `journal_qa.jsonl`.

### 3. rapport_hebdo : les 5 KPI du lundi matin

```bash
TEST_MODE=1 python -m rapport_hebdo
```

```
Rapport markdown : ./rapport.md
Rapport HTML : ./docs/rapport-hebdo.html
```

Un exemple genere sur l'entrepot reel (supply-dbt-warehouse) est publie ici :
https://adh-studio.github.io/supply-data-agents/rapport-hebdo.html

### 4. gardien_qualite : diagnostic des tests dbt en echec

```bash
python gardien_qualite/moteur.py gardien_qualite/fixtures/run_results_casse.json
```

```json
{"nb_tests_examines": 5, "nb_echecs": 3, "nb_bloquants": 1, "statut_global": "echec",
 "diagnostics": [{"nom_test": "unique_supply_couverture_stock_id_produit",
                  "table": "supply_couverture_stock",
                  "categorie": "cle dupliquee", "gravite": "bloquant", "..."}]}
```

Code de sortie 1 si au moins un echec bloquant : parfait pour arreter un pipeline.

### 5. fiches_produits : brouillons avec validation humaine

```bash
TEST_MODE=1 python -m fiches_produits generer --limite 1
```

```
[EN ATTENTE] P018 (mode gabarit) -> a_valider/P018.md
1 fiche(s) deposee(s) en zone a_valider/. Validation humaine requise avant toute publication.
```

Puis, apres relecture : `python -m fiches_produits approuver P018` deplace la fiche
en `validees/`. Rien n'est publie sans ce geste humain.
Le rejet motive existe aussi : `fiches_produits.moteur.rejeter(id, motif)` deplace
la fiche en `rejetees/` avec le motif dans l'en-tete YAML.

## SupplySense : le cockpit web

Un cockpit local qui reunit les 5 agents dans une seule interface (famille
StockSense / LeadSense, decline terracotta + blanc, mode sombre brun nuit) :

```bash
TEST_MODE=1 python -m cockpit     # base fixture
python -m cockpit                 # entrepot reel (DUCKDB_PATH)
# -> http://127.0.0.1:4300
```

- **Philosophie zero dependance** : serveur Python stdlib uniquement
  (`http.server` + `json`), SPA vanilla (HTML + CSS + JS, zero framework,
  zero npm). Rien a installer en plus de `requirements.txt`.
- **Lecture seule par construction** : toutes les lectures passent par
  `lib.base.requete` (SELECT uniquement, allowlist des 5 marts). Le serveur
  n'ecrit que dans `outbox/`, `a_valider/`, `validees/`, `rejetees/` et
  `docs/rapport-hebdo.html` (plus `rapport.md` et `journal_qa.jsonl`, sorties
  historiques des modules). La base n'est jamais ecrite.
- **Vues** : Dashboard (KPI + graphiques SVG faits main), Rapport hebdo,
  Assistant (chat Q&A avec SQL affiche en toute transparence), Alertes rupture
  (reappro suggere + depot outbox), Fiches produits (colonnes En attente /
  Validees / Rejetees, approbation et rejet motives a la main), Sante des
  donnees (diagnostics du gardien sur ses deux scenarios de demonstration).
- **Options d'URL** : `?theme=dark|light` force le theme au chargement,
  `?motion=0` coupe les animations (captures), `?q=...` pose une question
  a l'assistant.

## Lien avec le role de Product Owner Data et IA

Ce repo est volontairement petit, mais chaque module incarne un reflexe produit :

- **Partir du probleme, pas de la techno** : chaque module commence par une douleur
  metier (rupture non vue, ticket data pour une question simple, comite du lundi,
  test dbt illisible, redaction de fiches interminable).
- **L'IA sous garde-fous, pas en roue libre** : SQL genere systematiquement filtre
  (lecture seule + allowlist), fiches produits controlees (pas de chiffre invente,
  pas de superlatif) puis soumises a validation humaine explicite.
- **Toujours un mode degrade** : chaque module fonctionne sans cle d'API grace a un
  repli deterministe. L'automatisation ne depend pas de la disponibilite d'un LLM.
- **Observable et auditable** : journal d'audit du self-service, outbox archivee des
  alertes, surveillance anti-echec dans le workflow n8n, code de sortie exploitable.
- **Testable et livrable** : 105 tests sur base fixture, CI sans secret, un script
  unique (`run_e2e.sh`) identique en local et en CI.

## Brancher un vrai Slack / n8n

Les modules produisent des sorties neutres (JSON, markdown, webhook). Pour passer
en production :

1. **Webhook Slack direct** : creez une app Slack avec un
   [Incoming Webhook](https://api.slack.com/messaging/webhooks) sur le canal
   `#supply-alertes`, puis :

   ```bash
   export ALERTE_WEBHOOK_URL="https://hooks.slack.com/services/T000/B000/XXXX"
   python -m alerte_rupture
   ```

   En cas d'echec du webhook (3 tentatives), l'alerte reste archivee dans `outbox/`
   et l'erreur est signalee sur stderr sans faire tomber le job.

2. **Via n8n** : importez `n8n/alerte_rupture.json` dans votre instance n8n.
   Le workflow contient : Schedule Trigger (cron `0 7 * * *`), Execute Command
   (`python -m alerte_rupture --json`), un IF sur `nb_ruptures > 0`, un node Slack
   vers `#supply-alertes`, et une branche d'erreur vers `#supply-incidents`
   (surveillance anti-echec). Remplacez le credential Slack placeholder par le votre
   et ajustez le chemin Python du node Execute Command.

3. **Planification sans n8n** : des exemples cron et launchd sont documentes en tete
   de `rapport_hebdo/__init__.py` ; la meme approche vaut pour l'alerte quotidienne.

4. **Entrepot reel** : pointez `DUCKDB_PATH` vers le `supply.duckdb` produit par
   `dbt build` dans supply-dbt-warehouse, et ne posez pas `TEST_MODE`.

## Structure du repo

```
lib/                 socle : acces DuckDB lecture seule + gardes SQL
fixtures/            CSV + script de construction de la base de test
alerte_rupture/      module 1 : alerte Slack rupture de stock
agent_qa/            module 2 : self-service question -> SQL garde -> reponse
rapport_hebdo/       module 3 : rapport hebdomadaire (markdown + HTML)
gardien_qualite/     module 4 : diagnostic des tests dbt en echec
fiches_produits/     module 5 : fiches produits assistees + validation humaine
cockpit/             SupplySense : cockpit web local (stdlib, lecture seule)
n8n/                 workflow n8n importable (alerte rupture)
docs/                vitrine GitHub Pages + exemple de rapport genere
tests/               105 tests pytest, tous sur la base fixture
run_e2e.sh           pipeline local = pipeline CI
```

---

Henintsoa Andriamaholy ([adh-studio](https://github.com/adh-studio)) - candidat
Product Owner Data et IA.
