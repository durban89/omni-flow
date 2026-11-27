# OmniFlow

[English](README.en.md) | 中文

面向垂直品类的 CPS 联盟内容站自动化矩阵。项目采用三层架构：本地数据生产、Cloudflare 边缘基础设施、公开分发与转化。当前仓库已能端到端跑通「采集 → 内容生成 → 校验 → Hugo 构建 → KV 同步 → 部署」的完整链路，并提供本地审核控制台。

> 合规边界：本项目不实现 cloaking、门页、对搜索引擎与真实用户返回不同内容、伪造资质等规避审查的手段。抓取需遵守目标站 robots.txt 与服务条款；目标品类涉及的内容分级、联盟披露、隐私与广告合规由运营方负责。

延伸阅读：[架构说明](docs/architecture.md)、[内容数据契约](docs/content-contract.md)。

## 1. 三层架构

| 层级 | 目录 | 运行位置 | 职责 |
| --- | --- | --- | --- |
| Layer 1 Local Worker | `01-local-worker/` | 本地 / CI | 需求采集、内容预处理、静态站构建、产物校验 |
| Layer 2 Cloudflare | `02-cloudflare/` | Cloudflare Edge | Pages 托管、Worker 路由、KV 配置、R2 资源 |
| Layer 3 Distribution | `03-distribution/` | 构建期 + Edge | Hugo 站点、落地页模板、SEO 元数据、转化组件 |

### Layer 1: Local Worker / Scraper Layer

负责“数据进来 -> 结构化 -> 内容草案 -> 静态产物”的离线流水线，不对外提供 HTTP 服务。

- `scraper/`：数据源适配器，支持 `json_file`、`rss`、`playwright`（动态渲染页面）；远程请求带 robots.txt 检查、限速和超时。
- `content/`：HTML 转文本 / Markdown、去重、slug 生成、LLM 内容生成、人工审核、Hugo 导出与契约校验。
- `pipeline/`：编排入口（`local_worker.pipeline.run`），串联 scrape → content → validate → export → build → sync-kv，并承载联盟主库与 KV 同步。
- `server/`：只监听 `127.0.0.1` 的本地审核控制台，任务串行执行。
- `build/`：构建产物，不提交到版本库。
- `tests/`：单元测试与 fixture。

### Layer 2: Cloudflare Serverless Infrastructure

只承担运行时与边缘存储，不放业务采集逻辑。

- `worker/`：边缘路由。`/healthz` 存活检查，`/out/:slug` 从 KV 读取联盟目标并返回 302。
- `kv/`：KV key 规范与样例数据；写入由 Layer 1 的 `sync-kv` 阶段完成。
- `r2/`：R2 bucket 规范与资源同步脚本（后续）。

### Layer 3: Public Distribution Layer

- `site/`：Hugo 源站，生成 `public/` 后由 Pages 发布。
- `landing/`：高转化落地页模板与组件。
- `seo/`：结构化数据、sitemap、robots、重定向清单等。

## 2. 数据流

```text
数据源 / API
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
                    302 联盟目标
```

## 3. 目录结构

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

## 4. 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)（Python 依赖与虚拟环境管理）
- Node.js 20+
- Hugo Extended 0.120+（`make build` 与 `make pipeline` 需要）
- Wrangler 4+（经 `npx wrangler` 调用，部署与 KV 管理需要）
- Cloudflare 账号与 API Token（部署阶段）

## 5. 快速开始

```bash
cp .env.example .env
cp config/settings.example.yaml config/settings.yaml
cp config/master-affiliate-db.example.json config/master-affiliate-db.json
# 编辑 config/settings.yaml 与 .env，填入站点信息与 LLM 凭据

./scripts/bootstrap.sh                 # uv sync + npm install

# 1) 从联盟主库生成 config/affiliate-offers.json 与站点 affiliate-map.json
uv run python -m local_worker.pipeline.run sync-affiliate
# 2) 跑完整流水线：采集 → 生成 → 校验 → 导出 → Hugo 构建 → KV 同步
make pipeline
# 3) 本地审核控制台（仅 127.0.0.1）
uv run python -m local_worker.pipeline.run serve
# 4) 静态检查：Ruff + Pytest + TypeScript 类型检查
make check
```

`config/settings.yaml` 不存在时，流水线会回退到 `config/settings.example.yaml`，但内容生成与联盟同步通常需要你先补齐本地配置。

Python 依赖由 uv 管理：根目录 `pyproject.toml` 声明 workspace 与开发依赖，`01-local-worker/pyproject.toml` 声明运行时依赖，`uv.lock` 锁定精确版本（提交到版本库）。`bootstrap.sh` 会执行 `uv sync` 创建/更新根 `.venv` 并安装 Worker 依赖。Playwright 抓取需要额外执行 `uv run python -m playwright install chromium`。

日常用法：

```bash
uv sync                              # 按 uv.lock 安装/同步依赖
uv run pytest 01-local-worker        # 在项目虚拟环境中运行命令
uv add --package local-worker httpx  # 为 Layer 1 新增运行时依赖
uv add --group dev pytest-cov        # 新增 workspace 开发依赖
uv lock --upgrade                    # 升级锁定的依赖版本
```

## 6. 常用命令

| 命令 | 说明 |
| --- | --- |
| `make setup` | `uv sync` 创建/更新虚拟环境，并安装 Worker 的 npm 依赖 |
| `make lock` | 重新解析并更新 `uv.lock` |
| `make pipeline` | 执行 Layer 1 完整流水线并构建静态站（等价 `run all`） |
| `make build` | 仅执行 Hugo 构建，产物在 `01-local-worker/build/public` |
| `make dev-worker` | 本地启动 Worker 调试（`wrangler dev`） |
| `make deploy` | 构建并部署 Pages + Worker |
| `make check` | Ruff + Pytest（经 `uv run`）+ TypeScript 类型检查 |
| `make clean` | 清理构建产物与本地 Wrangler 状态 |

`make pipeline` 默认读取 `config/settings.yaml`（可用 `LOCAL_WORKER_SETTINGS` 指定其他路径），跑通采集 → 规范化 → 校验 → Hugo 构建。生成的内容默认是 `draft`，不会进入公开索引。Hugo 未安装时构建阶段会跳过并记录警告，其余阶段照常完成。

### 流水线阶段

`local_worker.pipeline.run` 支持以下阶段，可单独执行以便调试：

```bash
uv run python -m local_worker.pipeline.run <stage> [--settings PATH] [--mode test|production]
```

| 阶段 | 作用 |
| --- | --- |
| `sync-affiliate` | 由 `master-affiliate-db.json` 生成 `config/affiliate-offers.json` 与站点 `affiliate-map.json`；支持 `--dry-run` |
| `scrape` | 按配置抓取数据源，输出 `build/data/raw_items.json` |
| `content` | 生成/规范化内容，输出 `build/data/content_items.json` |
| `validate` | 按内容契约校验条目，失败即退出 |
| `export` | 导出 Hugo Markdown（`03-distribution/site/content/posts/`） |
| `build` | 调用 Hugo 生成 `build/public` |
| `sync-kv` | 将 offers 写入 Cloudflare KV；支持 `--dry-run` |
| `deploy-pages` / `deploy-worker` | 部署静态站 / Worker |
| `review` | 命令行审核队列：`--action list\|approve\|reject --target <slug>` |
| `serve` | 启动本地审核控制台，默认 `http://127.0.0.1:8787` |
| `all` | 依次执行上述流水线（部署阶段除外） |

## 7. 内容输出

稳定输出契约见 [`docs/content-contract.md`](docs/content-contract.md)。

| 文件 | 内容 |
| --- | --- |
| `01-local-worker/build/data/raw_items.json` | 采集结果，保留原始正文、来源、许可与抓取时间 |
| `01-local-worker/build/data/content_items.json` | 规范化后的最终内容条目，供 Layer 3 消费 |
| `01-local-worker/build/data/manifest.json` | 本轮运行统计 |
| `01-local-worker/build/public/` | Hugo 静态站产物 |

默认输出规则：

- 所有新条目为 `status=draft`、`reviewed=false`、`seo.noindex=true`。
- `published` 条目必须完成审核、关闭 `noindex`，且正文至少 300 词。
- `id` 和 `slug` 在单轮输出内唯一。
- 每个条目至少保留一个来源 URL；来源信息缺失时校验失败。

## 8. 审核与发布

内容默认停在待审核状态，需要人工放行：

1. 运行 `uv run python -m local_worker.pipeline.run serve`，打开 `http://127.0.0.1:8787`，在控制台查看 raw / content 条目、编辑 offers、触发流水线任务并批准内容。控制台只接受回环地址访问，任务串行执行。
2. 或在终端操作审核队列：

```bash
uv run python -m local_worker.pipeline.run review --action list
uv run python -m local_worker.pipeline.run review --action approve --target <slug> --reviewer <name>
uv run python -m local_worker.pipeline.run review --action reject  --target <slug> --notes "reason"
```

批准/驳回会立即回写 `content_items.json` 并重新导出 Hugo Markdown；随后执行 `make build` 或 `make deploy` 才会更新线上页面。`--auto-approve` 可让符合条件的草稿在流水线中自动通过，仅建议在本地测试时使用。

## 9. 配置与密钥

- `config/settings.yaml`：非敏感运行配置，提交前确认不含密钥。样例见 `config/settings.example.yaml`。
- `config/master-affiliate-db.json`：联盟主库（含真实推广 URL），本地/私密，不提交。
- `config/affiliate-offers.json`：由 `sync-affiliate` 从主库生成，供 KV 同步（含 destination）。
- `03-distribution/site/data/affiliate-map.json`：由 `sync-affiliate` 生成的前端脱敏映射（仅 product + `/out/<slug>`）。
- `.env`：本地密钥，已被 `.gitignore` 忽略。
- Cloudflare 部署密钥使用 `wrangler secret` 或 CI Secret，不写入 `wrangler.toml`。

### 运行模式

`settings.yaml` 的 `modes:` 块为 `test` / `production` 提供覆盖值（Pages 项目名、Worker 名、KV namespace）。选择方式优先级从高到低：

1. 命令行 `--mode test|production`
2. 环境变量 `LOCAL_WORKER_MODE`
3. `settings.yaml` 中的 `app.mode`

KV namespace ID 不写入配置文件，按模式从 `.env` 读取：production 用 `CLOUDFLARE_KV_NAMESPACE_ID_PROD`，test 用 `CLOUDFLARE_KV_NAMESPACE_ID_DEV`；`CLOUDFLARE_KV_NAMESPACE_ID` 设置后覆盖两者。从创建 namespace 到验证跳转的完整步骤见 [KV 初始化与同步](docs/kv-setup.md)。

### LLM 配置

`llm.enable`、`llm.model`、`llm.temperature`、`llm.max_words` 控制内容生成，主模型凭据走 `LLM_BASE_URL` / `LLM_API_KEY`。可配置一个兼容 OpenAI 协议的备用 provider（`fallback_base_url` / `fallback_model` / `fallback_api_key_env`），主模型报错时自动降级；`fallback_api_key_env` 留空可接无需 key 的免费端点。

KV key 约定：

```text
offer:<slug>  -> { destination, status, geo?, disclosure_required }
config:<name> -> 运行时配置
```

跳转统一使用 `302`，不做 301 长期缓存；目标变更、offer 下线、合规检查都需要可回滚。

## 10. 部署

```bash
# 首次：登录并创建 KV namespace，把返回的 id 写入 .env
npx wrangler login
npx wrangler kv namespace create condom-iq-kv
npx wrangler kv namespace create condom-iq-kv-dev

# 使用某个模式构建并部署（Pages + Worker）
LOCAL_WORKER_MODE=production make deploy

# 单独同步 KV（可先 --dry-run 核对内容）
uv run python -m local_worker.pipeline.run sync-kv --mode production --dry-run
uv run python -m local_worker.pipeline.run sync-kv --mode production
```

`make deploy` 会执行 `scripts/deploy-pages.sh`：Hugo 构建 → `wrangler deploy` 发布 Worker → `wrangler pages deploy` 发布静态站。部署后用 `/healthz`、`/out/<slug>` 和关键页面做一次冒烟检查。首次配置 KV、token 权限与 namespace 绑定请按 [KV 初始化与同步](docs/kv-setup.md) 操作。

## 11. 开发约定

1. Layer 1 不直接写 Cloudflare KV/R2；通过 `affiliate-offers.json`（`sync-affiliate` → `sync-kv`）同步。
2. Layer 2 不包含采集与内容生成逻辑，只做路由、鉴权、缓存与存储。
3. Layer 3 的模板只消费结构化数据，不直接调用 LLM 或爬虫。
4. 新增数据源时在 `scraper/` 下建独立适配器，并在 `settings.yaml` 注册。
5. 新增内容处理器时保持纯函数优先，便于测试与回放。
6. 任何面向用户的联盟跳转都必须有披露，且不得对搜索引擎与用户返回不同内容。
7. 站点正文使用英文；技术文档、注释与运行日志使用中文。
8. 提交前运行 `make check`。

## 12. 当前状态

- [x] 三层目录骨架
- [x] 配置样例与密钥模板
- [x] Worker 存活检查与 KV 跳转骨架
- [x] Hugo 最小站点骨架与内容文章渲染模板
- [x] scraper 适配器（`json_file` / `rss` / `playwright` 动态渲染）
- [x] 富文本 HTML 转 Markdown、内容规范化与契约校验
- [x] Hugo Markdown 文章与 Data JSON 自动化导出
- [x] LLM 内容生成器与人工审核发布工作流（Review Queue + 本地控制台）
- [x] Cloudflare KV 联盟 Offer 批量同步脚本
- [ ] Pages / Worker CI 自动化部署
- [ ] 披露组件、分析埋点

## 13. 常见问题

**`uv sync` 下载卡住或很慢。** 国内网络可临时指定镜像：`UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple uv sync`，或在 `~/.config/uv/uv.toml` 中长期配置。

**提示 `hugo binary not found`。** 安装 Hugo Extended（0.120+）并确保 `hugo` 在 `PATH` 中；`make build`、`make pipeline`、`make deploy` 都依赖它。

**Playwright 抓取报浏览器缺失。** 执行 `uv run python -m playwright install chromium`，并确认对应数据源的 `enabled: true`。

**`sync-kv` 报缺少 namespace 或凭据。** 检查 `.env` 中 `CLOUDFLARE_ACCOUNT_ID`、`CLOUDFLARE_API_TOKEN`，以及当前模式下对应的 `CLOUDFLARE_KV_NAMESPACE_ID_*`。先用 `--dry-run` 验证内容，再实际写入。

**流水线改了配置却不生效。** 确认 `LOCAL_WORKER_SETTINGS` 与 `--settings` 指向同一个文件，且模式（`--mode` / `LOCAL_WORKER_MODE`）与预期一致。

## 14. 开源许可 (License)

本项目基于 [GNU Affero General Public License v3.0 (AGPL-3.0)](LICENSE) 协议开源。详细条款请参阅项目根目录下的 [LICENSE](LICENSE) 文件。
