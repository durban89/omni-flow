#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PAGES_PROJECT="${CLOUDFLARE_PAGES_PROJECT:-example-matrix}"
OUTPUT_DIR="$ROOT/01-local-worker/build/public"

hugo --source 03-distribution/site --minify --destination "$OUTPUT_DIR"

cd "$ROOT/02-cloudflare/worker"
npm run deploy

npx wrangler pages deploy "$OUTPUT_DIR" --project-name "$PAGES_PROJECT"
