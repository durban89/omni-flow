#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
fi

export PYTHONPATH="$ROOT/01-local-worker/src${PYTHONPATH:+:$PYTHONPATH}"

SETTINGS="${LOCAL_WORKER_SETTINGS:-$ROOT/config/settings.yaml}"
python3 -m local_worker.pipeline.run all --settings "$SETTINGS"
