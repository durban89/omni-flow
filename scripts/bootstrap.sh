#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e "01-local-worker[dev]"

cd "$ROOT/02-cloudflare/worker"
npm install

printf 'Bootstrap complete.\n'
printf 'Optional: python -m playwright install chromium\n'
