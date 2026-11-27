# OmniFlow

[中文](README.md) | English

An automation matrix for CPS affiliate content sites in vertical niches. The project uses a three-layer architecture: local data production, Cloudflare edge infrastructure, and public distribution and conversion. The repository now runs the full chain end to end — scrape → content generation → validation → Hugo build → KV sync → deploy — and ships a local review console.

> Compliance boundary: this project does not implement cloaking, doorway pages, serving different content to search engines and real users, fabricated credentials, or any other means of evading review. Scraping must respect the target site's robots.txt and terms of service; content ratings, affiliate disclosure, privacy, and advertising compliance for the target niche are the operator's responsibility.

Further reading: [Architecture](docs/architecture.md), [Content contract](docs/content-contract.md).

## 1. Three layers

| Layer | Directory | Runs on | Responsibility |
| --- | --- | --- | --- |
| Layer 1 Local Worker | `01-local-worker/` | Local / CI | Demand scraping, content preprocessing, static site build, artifact validation |
| Layer 2 Cloudflare | `02-cloudflare/` | Cloudflare Edge | Pages hosting, Worker routing, KV config, R2 assets |
| Layer 3 Distribution | `03-distribution/` | Build time + Edge | Hugo site, landing page templates, SEO metadata, conversion components |

### Layer 1: Local Worker / Scraper Layer

An offline pipeline that turns "incoming data -> structured items -> content drafts -> static artifacts". It exposes no HTTP service.

- `scraper/`: source adapters for `json_file`, `rss`, and `playwright` (dynamically rendered pages); remote requests include robots.txt checks, rate limiting, and timeouts.
- `content/`: HTML to text/Markdown, dedupe, slug generation, LLM content generation, human review, Hugo export, and contract validation.
- `pipeline/`: the orchestration entry point (`local_worker.pipeline.run`), chaining scrape → content → validate → export → build → sync-kv and owning affiliate master DB and KV sync.
- `server/`: local review console bound to `127.0.0.1`, running jobs serially.
- `build/`: build artifacts, not committed.
- `tests/`: unit tests and fixtures.

### Layer 2: Cloudflare Serverless Infrastructure

Runtime and edge storage only; no business scraping logic.

- `worker/`: edge routing. `/healthz` health check, `/out/:slug` reads the affiliate destination from KV and returns a 302.
- `kv/`: KV key conventions and sample data; writes are performed by Layer 1's `sync-kv` stage.
- `r2/`: R2 bucket conventions and asset sync scripts (later).

### Layer 3: Public Distribution Layer

- `site/`: Hugo source site; `public/` is published by Pages.
- `landing/`: high-conversion landing page templates and components.
- `seo/`: structured data, sitemap, robots, redirect manifest, and so on.

## 2. Data flow

```text
Data sources / APIs
   │
   ▼
[Layer 1] scraper ──► raw_items.json ──► content ──► content_items.json
                                                          │
                                                          ├──► Hugo build ──► build/public
                                                          │
   affiliate map                                          │ static assets
   ▼                                                      ▼
[Layer 2] KV (offer:*)                           [Layer 2] Pages
   │                                                      │
   └──────────────► Worker /out/:slug ◄───────────────────┘
                           │
                           ▼
                    302 affiliate destination
```

## 3. Directory layout

```text
.
├── LICENSE
├── README.md
├── README.en.md
├── .env.example
├── .gitignore
├── Makefile
├── pyproject.toml
├── uv.lock
├── config/
│   ├── settings.example.yaml
│   ├── curated-input.example.json
│   ├── master-affiliate-db.example.json
│   └── affiliate-offers.example.json
├── 01-local-worker/
│   ├── README.md
│   ├── pyproject.toml
│   ├── src/local_worker/
│   │   ├── config.py
│   │   ├── models.py
│   │   ├── errors.py
│   │   ├── scraper/
│   │   ├── content/
│   │   ├── pipeline/
│   │   └── server/
│   ├── tests/
│   └── build/
├── 02-cloudflare/
│   ├── worker/
│   │   ├── src/index.ts
│   │   ├── package.json
│   │   ├── tsconfig.json
│   │   └── wrangler.toml
│   ├── kv/
│   └── r2/
├── 03-distribution/
│   ├── site/
│   │   ├── hugo.toml
│   │   ├── content/
│   │   ├── layouts/
│   │   ├── static/
│   │   ├── assets/
│   │   └── data/
│   ├── landing/
│   └── seo/
├── scripts/
│   ├── bootstrap.sh
│   ├── pipeline.sh
│   └── deploy-pages.sh
└── docs/
    ├── architecture.md
    ├── content-contract.md
    └── kv-setup.md
```

## 4. Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (Python dependencies and virtual environments)
- Node.js 20+
- Hugo Extended 0.120+ (needed by `make build` and `make pipeline`)
- Wrangler 4+ (invoked through `npx wrangler`; needed for deploy and KV management)
- A Cloudflare account and API token (deploy stage)

## 5. Quick start

```bash
cp .env.example .env
cp config/settings.example.yaml config/settings.yaml
cp config/master-affiliate-db.example.json config/master-affiliate-db.json
# Edit config/settings.yaml and .env: site details and LLM credentials

./scripts/bootstrap.sh                 # uv sync + npm install

# 1) Generate config/affiliate-offers.json and the site affiliate-map.json
uv run python -m local_worker.pipeline.run sync-affiliate
# 2) Run the full pipeline: scrape → content → validate → export → Hugo build → KV sync
make pipeline
# 3) Local review console (127.0.0.1 only)
uv run python -m local_worker.pipeline.run serve
# 4) Static checks: Ruff + Pytest + TypeScript typecheck
make check
```

If `config/settings.yaml` is missing, the pipeline falls back to `config/settings.example.yaml`, but content generation and affiliate sync usually need your local settings first.

Python dependencies are managed by uv: the root `pyproject.toml` declares the workspace and dev dependencies, `01-local-worker/pyproject.toml` declares runtime dependencies, and `uv.lock` pins exact versions (committed). `bootstrap.sh` runs `uv sync` to create/update the root `.venv` and installs the Worker's npm dependencies. Playwright scraping additionally needs `uv run python -m playwright install chromium`.

Everyday usage:

```bash
uv sync                              # Install/sync dependencies from uv.lock
uv run pytest 01-local-worker        # Run a command inside the project environment
uv add --package local-worker httpx  # Add a Layer 1 runtime dependency
uv add --group dev pytest-cov        # Add a workspace dev dependency
uv lock --upgrade                    # Upgrade pinned dependency versions
```

## 6. Common commands

| Command | Description |
| --- | --- |
| `make setup` | `uv sync` to create/update the virtualenv, plus the Worker's npm install |
| `make lock` | Re-resolve and update `uv.lock` |
| `make pipeline` | Run the full Layer 1 pipeline and build the static site (same as `run all`) |
| `make build` | Hugo build only; output in `01-local-worker/build/public` |
| `make dev-worker` | Start the Worker locally (`wrangler dev`) |
| `make deploy` | Build and deploy Pages + Worker |
| `make check` | Ruff + Pytest (via `uv run`) + TypeScript typecheck |
| `make clean` | Remove build artifacts and local Wrangler state |

`make pipeline` reads `config/settings.yaml` by default (`LOCAL_WORKER_SETTINGS` can point elsewhere) and runs scrape → normalize → validate → Hugo build. Generated content defaults to `draft` and never enters the public index. If Hugo is missing, the build stage is skipped with a warning and the remaining stages still finish.

### Pipeline stages

`local_worker.pipeline.run` accepts the following stages, each runnable on its own for debugging:

```bash
uv run python -m local_worker.pipeline.run <stage> [--settings PATH] [--mode test|production]
```

| Stage | Purpose |
| --- | --- |
| `sync-affiliate` | Build `config/affiliate-offers.json` and the site's `affiliate-map.json` from `master-affiliate-db.json`; supports `--dry-run` |
| `scrape` | Fetch configured sources into `build/data/raw_items.json` |
| `content` | Generate/normalize content into `build/data/content_items.json` |
| `validate` | Validate items against the content contract; exits on failure |
| `export` | Export Hugo Markdown to `03-distribution/site/content/posts/` |
| `build` | Call Hugo to produce `build/public` |
| `sync-kv` | Write offers to Cloudflare KV; supports `--dry-run` |
| `deploy-pages` / `deploy-worker` | Deploy the static site / Worker |
| `review` | CLI review queue: `--action list\|approve\|reject --target <slug>` |
| `serve` | Start the local review console, default `http://127.0.0.1:8787` |
| `all` | Run the pipeline above in order (deploy stages excluded) |

## 7. Content output

The stable output contract lives in [`docs/content-contract.md`](docs/content-contract.md).

| File | Contents |
| --- | --- |
| `01-local-worker/build/data/raw_items.json` | Scrape results, keeping raw body, source, license, and retrieval time |
| `01-local-worker/build/data/content_items.json` | Normalized final entries consumed by Layer 3 |
| `01-local-worker/build/data/manifest.json` | Per-run statistics |
| `01-local-worker/build/public/` | Hugo static site output |

Default output rules:

- Every new entry is `status=draft`, `reviewed=false`, `seo.noindex=true`.
- A `published` entry must be reviewed, have `noindex` turned off, and be at least 300 words.
- `id` and `slug` are unique within a single run's output.
- Every entry keeps at least one source URL; validation fails when source data is missing.

## 8. Review and publishing

Content stops at the pending-review state until a human approves it:

1. Run `uv run python -m local_worker.pipeline.run serve`, open `http://127.0.0.1:8787`, and use the console to inspect raw/content entries, edit offers, trigger pipeline jobs, and approve content. The console only accepts loopback connections and runs jobs serially.
2. Or work the queue from a terminal:

```bash
uv run python -m local_worker.pipeline.run review --action list
uv run python -m local_worker.pipeline.run review --action approve --target <slug> --reviewer <name>
uv run python -m local_worker.pipeline.run review --action reject  --target <slug> --notes "reason"
```

Approving or rejecting immediately rewrites `content_items.json` and re-exports the Hugo Markdown; run `make build` or `make deploy` afterwards to update the live site. `--auto-approve` lets eligible drafts pass automatically during a pipeline run and is only recommended for local testing.

## 9. Configuration and secrets

- `config/settings.yaml`: non-sensitive runtime configuration; make sure it holds no secrets before committing. See `config/settings.example.yaml`.
- `config/master-affiliate-db.json`: affiliate master DB (real promotional URLs), local/private, never committed.
- `config/affiliate-offers.json`: generated by `sync-affiliate` from the master DB, consumed by KV sync (contains destinations).
- `03-distribution/site/data/affiliate-map.json`: front-end masked map generated by `sync-affiliate` (product + `/out/<slug>` only).
- `.env`: local secrets, already ignored by `.gitignore`.
- Deployment secrets go through `wrangler secret` or CI secrets, never into `wrangler.toml`.

### Runtime modes

The `modes:` block in `settings.yaml` provides overrides for `test` / `production` (Pages project, Worker name, KV namespace). Selection priority, highest first:

1. CLI `--mode test|production`
2. Environment variable `LOCAL_WORKER_MODE`
3. `app.mode` in `settings.yaml`

KV namespace IDs are never stored in config files; they are read from `.env` per mode: `CLOUDFLARE_KV_NAMESPACE_ID_PROD` for production and `CLOUDFLARE_KV_NAMESPACE_ID_DEV` for test; setting `CLOUDFLARE_KV_NAMESPACE_ID` overrides both. The full walkthrough from creating a namespace to verifying redirects is in [KV setup and sync](docs/kv-setup.md) (Chinese).

### LLM configuration

`llm.enable`, `llm.model`, `llm.temperature`, and `llm.max_words` control generation; the primary provider's credentials come from `LLM_BASE_URL` / `LLM_API_KEY`. You can configure an OpenAI-compatible fallback provider (`fallback_base_url` / `fallback_model` / `fallback_api_key_env`) that is used when the primary provider errors; leaving `fallback_api_key_env` empty allows a keyless free endpoint.

KV key conventions:

```text
offer:<slug>  -> { destination, status, geo?, disclosure_required }
config:<name> -> runtime configuration
```

Redirects always use `302`, never long-cached `301`; destination changes, offer takedowns, and compliance checks must stay reversible.

## 10. Deployment

```bash
# First time: log in and create KV namespaces, then put the returned ids in .env
npx wrangler login
npx wrangler kv namespace create condom-iq-kv
npx wrangler kv namespace create condom-iq-kv-dev

# Build and deploy (Pages + Worker) for a given mode
LOCAL_WORKER_MODE=production make deploy

# Sync KV on its own (use --dry-run first to review the payload)
uv run python -m local_worker.pipeline.run sync-kv --mode production --dry-run
uv run python -m local_worker.pipeline.run sync-kv --mode production
```

`make deploy` runs `scripts/deploy-pages.sh`: Hugo build → `wrangler deploy` for the Worker → `wrangler pages deploy` for the static site. After deploying, smoke-test `/healthz`, `/out/<slug>`, and the key pages. For the first-time KV setup, token permissions, and namespace binding, follow [KV setup and sync](docs/kv-setup.md).

## 11. Development conventions

1. Layer 1 never writes Cloudflare KV/R2 directly; it goes through `affiliate-offers.json` (`sync-affiliate` → `sync-kv`).
2. Layer 2 contains no scraping or content generation logic — only routing, auth, caching, and storage.
3. Layer 3 templates only consume structured data and never call an LLM or scraper directly.
4. Add new sources as standalone adapters under `scraper/` and register them in `settings.yaml`.
5. Keep new content processors pure-function-first for easy testing and replay.
6. Every user-facing affiliate redirect must carry disclosure, and must never differ between search engines and real users.
7. Site copy is written in English; technical docs, code comments, and runtime logs are in Chinese.
8. Run `make check` before committing.

## 12. Current status

- [x] Three-layer directory skeleton
- [x] Example configs and secret templates
- [x] Worker health check and KV redirect skeleton
- [x] Minimal Hugo site skeleton and article rendering templates
- [x] Scraper adapters (`json_file` / `rss` / `playwright` dynamic rendering)
- [x] Rich-text HTML to Markdown, content normalization, and contract validation
- [x] Automated Hugo Markdown and data JSON export
- [x] LLM content generator with a human review/publish workflow (review queue + local console)
- [x] Bulk Cloudflare KV affiliate offer sync
- [ ] Automated Pages / Worker CI deployment
- [ ] Disclosure components and analytics instrumentation

## 13. Troubleshooting

**`uv sync` hangs or is very slow.** On slow networks, point uv at a mirror for one run: `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple uv sync`, or configure it persistently in `~/.config/uv/uv.toml`.

**`hugo binary not found`.** Install Hugo Extended (0.120+) and make sure `hugo` is on `PATH`; `make build`, `make pipeline`, and `make deploy` all need it.

**Playwright scraping reports a missing browser.** Run `uv run python -m playwright install chromium` and confirm the source has `enabled: true`.

**`sync-kv` complains about a missing namespace or credentials.** Check `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, and the mode-specific `CLOUDFLARE_KV_NAMESPACE_ID_*` in `.env`. Validate the payload with `--dry-run` before writing for real.

**Configuration changes do not take effect in the pipeline.** Make sure `LOCAL_WORKER_SETTINGS` and `--settings` point at the same file and that the mode (`--mode` / `LOCAL_WORKER_MODE`) is the one you expect.

## 14. License

This project is released under the [GNU Affero General Public License v3.0 (AGPL-3.0)](LICENSE). See the [LICENSE](LICENSE) file in the repository root for the full terms.
