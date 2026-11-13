#!/usr/bin/env bash
# Pipeline de bout en bout : environnement, dependances, base fixture, tests.
#
# Usage : ./run_e2e.sh
#
# Choisit un interpreteur Python compatible (3.13, 3.12 ou 3.11, dans cet
# ordre), cree .venv, installe requirements.txt, construit la base fixture
# DuckDB depuis les CSV, puis lance toute la suite pytest en mode verbeux.
# Aucun secret ni appel reseau n'est necessaire (hors telechargement pip).

set -euo pipefail

cd "$(dirname "$0")"

# 1. Choix de l'interpreteur : 3.13, 3.12 ou 3.11 uniquement.
#    (Python 3.15 est refuse : pandas y provoque un segfault.)
PYTHON=""
for candidat in python3.13 python3.12 python3.11; do
    if command -v "$candidat" >/dev/null 2>&1; then
        PYTHON="$candidat"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    # Dernier recours : python3, seulement s'il est en 3.11, 3.12 ou 3.13.
    if command -v python3 >/dev/null 2>&1; then
        version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
        case "$version" in
            3.11|3.12|3.13) PYTHON="python3" ;;
            *)
                echo "Erreur : python3 est en version $version." >&2
                echo "Ce projet exige Python 3.11, 3.12 ou 3.13 (installez par exemple python3.13)." >&2
                exit 1
                ;;
        esac
    else
        echo "Erreur : aucun interpreteur Python trouve (python3.13, python3.12, python3.11)." >&2
        exit 1
    fi
fi

echo "==> Interpreteur retenu : $PYTHON ($($PYTHON --version))"

# 2. Environnement virtuel.
if [ ! -d .venv ]; then
    echo "==> Creation de l'environnement virtuel .venv"
    "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# 3. Dependances epinglees.
echo "==> Installation des dependances (requirements.txt)"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

# 4. Base fixture DuckDB (reconstruite a chaque run depuis les CSV).
echo "==> Construction de la base fixture fixtures/entrepot_test.duckdb"
python fixtures/construire_base.py

# 5. Suite de tests complete contre la base fixture.
echo "==> Lancement de la suite pytest"
TEST_MODE=1 python -m pytest tests/ -v

echo "==> Pipeline de bout en bout termine avec succes."
