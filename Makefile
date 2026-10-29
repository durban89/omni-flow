SHELL := /usr/bin/env bash
PYTHON ?= python3
VENV := .venv

.PHONY: setup pipeline build deploy dev-worker check clean

setup:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/python -m pip install -e "./01-local-worker[dev]"
	cd 02-cloudflare/worker && npm install

pipeline:
	./scripts/pipeline.sh

build:
	hugo --source 03-distribution/site --minify --destination "$(CURDIR)/01-local-worker/build/public"

deploy:
	./scripts/deploy-pages.sh

dev-worker:
	cd 02-cloudflare/worker && npm run dev

check:
	$(VENV)/bin/ruff check 01-local-worker
	$(VENV)/bin/pytest 01-local-worker
	cd 02-cloudflare/worker && npm run typecheck

clean:
	rm -rf 01-local-worker/build/public 02-cloudflare/worker/.wrangler
