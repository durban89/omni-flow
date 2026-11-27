#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v uv >/dev/null 2>&1; then
  printf 'uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/\n' >&2
  exit 1
fi

uv sync

cd "$ROOT/02-cloudflare/worker"
npm install

printf 'Bootstrap complete.\n'
printf 'Optional: uv run python -m playwright install chromium\n'
