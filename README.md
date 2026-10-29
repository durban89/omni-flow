# OmniFlow

面向垂直品类的 CPS 联盟内容站自动化矩阵。项目采用三层架构：本地数据生产、Cloudflare 边缘基础设施、公开分发与转化。当前仓库已完成三层骨架，以及 Layer 1 的数据采集与规范化输出；LLM 内容生成、模板和部署逻辑在后续迭代中实现。

> 合规边界：本项目不实现 cloaking、门页、对搜索引擎与真实用户返回不同内容、伪造资质等规避审查的手段。抓取需遵守目标站 robots.txt 与服务条款；目标品类涉及的内容分级、联盟披露、隐私与广告合规由运营方负责。

## 1. 三层架构

| 层级 | 目录 | 运行位置 | 职责 |
| --- | --- | --- | --- |
| Layer 1 Local Worker | `01-local-worker/` | 本地 / CI | 需求采集、内容预处理、静态站构建、产物校验 |
| Layer 2 Cloudflare | `02-cloudflare/` | Cloudflare Edge | Pages 托管、Worker 路由、KV 配置、R2 资源 |
| Layer 3 Distribution | `03-distribution/` | 构建期 + Edge | Hugo 站点、落地页模板、SEO 元数据、转化组件 |

### Layer 1: Local Worker / Scraper Layer

负责“数据进来 -> 结构化 -> 内容草案 -> 静态产物”的离线流水线，不对外提供 HTTP 服务。

- `scraper/`：数据源适配器，当前支持 `json_file` 与 `rss`；远程请求带 robots.txt 检查、限速和超时。
- `content/`：内容清洗、HTML 转文本、去重、slug 生成、最终输出校验。
- `pipeline/`：编排入口，串联 scrape -> content -> validate -> build。
- `build/`：构建产物，不提交到版本库。
- `tests/`：单元测试与 fixture。

### Layer 2: Cloudflare Serverless Infrastructure

只承担运行时与边缘存储，不放业务采集逻辑。

- `worker/`：边缘路由。`/healthz` 存活检查，`/out/:slug` 从 KV 读取联盟目标并返回 302。
- `kv/`：KV key 规范与导入脚本（后续）。
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
├── .env.example
├── .gitignore
├── Makefile
├── config/
│   ├── settings.example.yaml
│   ├── curated-input.example.json
│   └── affiliate-map.example.json
├── 01-local-worker/
│   ├── pyproject.toml
│   ├── src/local_worker/
│   │   ├── config.py
│   │   ├── models.py
│   │   ├── errors.py
│   │   ├── scraper/
│   │   ├── content/
│   │   └── pipeline/
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
    └── content-contract.md
```

## 4. 环境要求

- Python 3.11+
- Node.js 20+
- Hugo Extended 0.120+
- Wrangler 4+
- Cloudflare 账号与 API Token（部署阶段）

## 5. 快速开始

```bash
cp .env.example .env
cp config/settings.example.yaml config/settings.yaml
cp config/affiliate-map.example.json config/affiliate-map.json

./scripts/bootstrap.sh
make check
```

`bootstrap.sh` 会创建 `.venv`、安装 Python 依赖、安装 Worker 依赖。Playwright 浏览器需要额外执行 `python -m playwright install chromium`。

## 6. 常用命令

| 命令 | 说明 |
| --- | --- |
| `make setup` | 创建虚拟环境并安装依赖 |
| `make pipeline` | 执行 Layer 1 流水线并构建静态站 |
| `make build` | 仅执行 Hugo 构建 |
| `make dev-worker` | 本地启动 Worker 调试 |
| `make deploy` | 构建并部署 Pages + Worker |
| `make check` | Ruff + Pytest + TypeScript 类型检查 |
| `make clean` | 清理构建产物 |

`make pipeline` 默认读取 `config/curated-input.example.json`，跑通采集 -> 规范化 -> 校验 -> Hugo 构建。生成的内容默认是 `draft`，不会进入公开索引。

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

## 8. 配置与密钥

- `config/settings.yaml`：非敏感运行配置，提交前确认不含密钥。
- `config/affiliate-map.json`：联盟 offer 映射，由 Layer 1 同步到 KV。
- `.env`：本地密钥，已被 `.gitignore` 忽略。
- Cloudflare 部署密钥使用 `wrangler secret` 或 CI Secret，不写入 `wrangler.toml`。

KV key 约定：

```text
offer:<slug>  -> { destination, status, geo?, disclosure_required }
config:<name> -> 运行时配置
```

跳转统一使用 `302`，不做 301 长期缓存；目标变更、offer 下线、合规检查都需要可回滚。

## 9. 开发约定

1. Layer 1 不直接写 Cloudflare KV/R2；通过 `affiliate-map.json` 和部署脚本同步。
2. Layer 2 不包含采集与内容生成逻辑，只做路由、鉴权、缓存与存储。
3. Layer 3 的模板只消费结构化数据，不直接调用 LLM 或爬虫。
4. 新增数据源时在 `scraper/` 下建独立适配器，并在 `settings.yaml` 注册。
5. 新增内容处理器时保持纯函数优先，便于测试与回放。
6. 任何面向用户的联盟跳转都必须有披露，且不得对搜索引擎与用户返回不同内容。
7. 提交前运行 `make check`。

## 10. 当前状态

- [x] 三层目录骨架
- [x] 配置样例与密钥模板
- [x] Worker 存活检查与 KV 跳转骨架
- [x] Hugo 最小站点骨架
- [x] scraper 适配器（`json_file` / `rss`）
- [x] 内容规范化、最终输出契约与校验
- [ ] LLM 内容生成与人工审核队列
- [ ] KV 同步脚本
- [ ] Pages / Worker CI 部署
- [ ] 披露组件、分析埋点

## 11. 开源许可 (License)

本项目基于 [GNU Affero General Public License v3.0 (AGPL-3.0)](LICENSE) 协议开源。详细条款请参阅项目根目录下的 [LICENSE](LICENSE) 文件。

