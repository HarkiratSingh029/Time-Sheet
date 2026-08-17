#!/usr/bin/env bash
# Local development launcher.
#
#   ./scripts/dev.sh            # run with reload on http://127.0.0.1:8000
#   ./scripts/dev.sh --fresh    # wipe data/, rebuild brand artifacts, reseed, then run
#
# --fresh exists because a half-migrated local database is a worse debugging
# experience than losing a throwaway one.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

FRESH=false
for arg in "$@"; do
  case "$arg" in
    --fresh) FRESH=true ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

if [[ ! -d .venv ]]; then
  echo "==> creating .venv"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if [[ ! -f .env ]]; then
  echo "==> .env missing, copying from .env.example"
  cp .env.example .env
fi

echo "==> installing dependencies"
pip install --quiet --upgrade pip
pip install --quiet -e ".[dev]"

echo "==> building brand artifacts"
python3 brands/scripts/build_all.py >/dev/null

if [[ "$FRESH" == true ]]; then
  echo "==> --fresh: removing data/"
  rm -rf data
fi
mkdir -p data/uploads

if [[ ! -f backend/app/main.py ]]; then
  cat >&2 <<'EOF'
The application does not exist yet — backend/app/main.py is missing.

The platform is set up (gates, tokens, docs, skills); the app itself is EPIC 0,
story 0.1. See docs/ROADMAP.md.
EOF
  exit 1
fi

echo "==> starting TS Timesheets on http://127.0.0.1:8000"
exec uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
