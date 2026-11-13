# Publication du repo sur GitHub

Prerequis : [gh](https://cli.github.com/) authentifie sur le compte `adh-studio`
(`gh auth status` pour verifier).

## 1. Creer le repo public et pousser main

Depuis la racine du projet :

```bash
gh repo create supply-data-agents --public \
  --description "La donnee validee devient de l'action : 5 automatisations (alertes, self-service, rapport, qualite, generation assistee) au-dessus de l'entrepot dbt supply-dbt-warehouse" \
  --source . --push
```

La CI (`.github/workflows/ci.yml`) se lance automatiquement au premier push :
elle execute `run_e2e.sh` sur ubuntu-latest avec Python 3.12, sans aucun secret.

## 2. Activer GitHub Pages (source : branche main, dossier /docs)

```bash
gh api --method POST repos/adh-studio/supply-data-agents/pages \
  -f "source[branch]=main" -f "source[path]=/docs"
```

Si Pages est deja active et qu'il faut seulement changer la source :

```bash
gh api --method PUT repos/adh-studio/supply-data-agents/pages \
  -f "source[branch]=main" -f "source[path]=/docs"
```

## 3. Verifier

```bash
# Statut de la CI
gh run list --repo adh-studio/supply-data-agents --limit 1

# Statut de Pages (le site peut mettre une ou deux minutes a sortir)
gh api repos/adh-studio/supply-data-agents/pages --jq '.html_url, .status'
```

Adresses finales :

- Repo : https://github.com/adh-studio/supply-data-agents
- Vitrine : https://adh-studio.github.io/supply-data-agents/
- Exemple de rapport : https://adh-studio.github.io/supply-data-agents/rapport-hebdo.html
