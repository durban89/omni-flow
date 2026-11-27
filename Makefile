SHELL := /usr/bin/env bash
UV ?= uv
VENV := .venv

.PHONY: setup lock pipeline build deploy dev-worker check clean

setup:
	$(UV) sync
	cd 02-cloudflare/worker && npm install

lock:
	$(UV) lock

pipeline:
	./scripts/pipeline.sh

build:
	hugo --source 03-distribution/site --minify --destination "$(CURDIR)/01-local-worker/build/public"

deploy:
	./scripts/deploy-pages.sh

dev-worker:
	cd 02-cloudflare/worker && npm run dev

check:
	$(UV) run ruff check 01-local-worker
	$(UV) run pytest 01-local-worker
	cd 02-cloudflare/worker && npm run typecheck

clean:
	rm -rf 01-local-worker/build/public 02-cloudflare/worker/.wrangler
