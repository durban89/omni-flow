# Layer 1: Local Worker

本地数据生产层，运行在开发机或 CI，不对外暴露网络服务端口。

## 职责

- 采集公开需求信号、关键词与竞品信息，保留来源、时间和许可信息。
- 清洗、去重、富文本规范化（HTML 转 Markdown），并执行严格的数据契约校验。
- 借助 LLM 或合规模板扩展深度评测与导购内容。
- 人工审核（Review Queue）工作流与状态持久化流转。
- 将已审核/草案内容导出为 Hugo Markdown 文章与 Data 数据源，驱动静态站构建。
- 将联盟 Offer 映射同步至 Cloudflare KV。

## 目录结构

```text
01-local-worker/
├── pyproject.toml
├── src/local_worker/
│   ├── config.py              # 设置加载与校验
│   ├── models.py              # RawItem / ContentItem / SourceRef / SEO 数据契约
│   ├── errors.py              # 业务异常定义
│   ├── scraper/               # 数据源适配器与 HTTP 客户端
│   │   ├── base.py            # SourceAdapter 协议
│   │   ├── http.py            # 支持 robots.txt 与 Tenacity 重试的 HTTP 客户端
│   │   ├── json_file.py       # 本地文件导入适配器
│   │   ├── rss.py             # RSS / Atom 源适配器
│   │   ├── playwright_source.py # 动态浏览器抓取适配器
│   │   └── topic_search.py    # 搜索 API 选题发现适配器
│   ├── content/               # 清洗、生成、规范化、审核与导出
│   │   ├── text.py            # 富文本 HTML 转 Markdown、摘要提取、字数统计
│   │   ├── generator.py       # LLM 与合规模板内容扩展生成
│   │   ├── normalize.py       # RawItem 规范化并关联审核状态
│   │   ├── validate.py        # 唯一性与 SEO/状态门禁契约校验
│   │   ├── review.py          # 审核服务与 review_state.json 状态持久化
│   │   └── export_hugo.py     # 导出 Hugo Markdown 文章与 Data JSON
│   └── pipeline/              # 编排入口与同步工具
│       ├── run.py             # 分阶段执行入口 CLI
│       ├── affiliate_sync.py  # master DB 生成 Hugo 脱敏映射与 affiliate-offers.json
│       └── kv_sync.py         # Cloudflare KV bulk put 生成与同步
├── tests/                     # 单元与集成测试
└── build/                     # 构建产物（已忽略）
```

## 流水线阶段

| 阶段 | 命令 | 说明 |
| :--- | :--- | :--- |
| `scrape` | `python -m local_worker.pipeline.run scrape` | 采集各数据源并去重，输出 `build/data/raw_items.json` |
| `content` | `python -m local_worker.pipeline.run content` | 规范化为 `ContentItem`，输出 `content_items.json`，并同步导出至 Hugo。**增量执行**：id（源 URL + 标题）已存在的条目会跳过，删除某条后重跑只补生成该条 |
| `validate` | `python -m local_worker.pipeline.run validate` | 校验内容契约、字数、唯一性、SEO 与审核发布门禁 |
| `export` | `python -m local_worker.pipeline.run export` | 将内容输出为 Hugo Markdown 文件 (`03-distribution/site/content/posts/*.md`) |
| `build` | `python -m local_worker.pipeline.run build` | 调用 Hugo 构建静态站产物输出到 `build/public/` |
| `sync-affiliate` | `python -m local_worker.pipeline.run sync-affiliate` | 从 `master-affiliate-db.json` 生成 Hugo 脱敏映射 + `affiliate-offers.json` |
| `sync-kv` | `python -m local_worker.pipeline.run sync-kv` | 校验 `affiliate-offers.json` 并生成 Cloudflare KV bulk 数据/同步到 KV |
| `review` | `python -m local_worker.pipeline.run review` | 人工审核工作流（查看列表、通过审核、驳回） |
| `all` | `python -m local_worker.pipeline.run all` | 串联完整流程：`scrape -> content -> validate -> export -> build -> sync-affiliate -> sync-kv` |

## 测试 / 生产模式

在 `config/settings.yaml` 用 `app.mode` 选择配置档，`modes:` 下的同名块会**深度合并**覆盖基础配置，只需写差异部分：

```yaml
app:
  mode: "test"        # test | production

modes:
  test:
    pipeline: {max_items_per_run: 1}   # content 阶段最多生成 1 条
    scraper: {max_items_per_run: 1}    # scrape 阶段最多采集 1 条
  production:
    pipeline: {max_items_per_run: 20}
    scraper: {max_items_per_run: 20}
    # 上线换更强模型时在这里覆盖：
    # llm: {model: "your/stronger-model"}
```

临时切换而不改文件：

```bash
python -m local_worker.pipeline.run content --mode production
LOCAL_WORKER_MODE=production python -m local_worker.pipeline.run scrape
```

优先级：`--mode` > 环境变量 `LOCAL_WORKER_MODE` > `app.mode`，缺省为 `production`；写了 `modes:` 但模式名不存在会直接报错，不会静默回退。每条命令启动时会打印生效配置，例如：

```
[config] mode=test | scrape cap=1 | content cap=1 | model=baidu/ernie-5.5-flash-preview:free
```

注意：test 模式下 `scrape` 只保留 1 条线索（会覆盖 `raw_items.json`），适合验证提示词与模型效果；累积文章请用 production 模式。

## 人工审核工作流 (Review Queue)

默认抓取并规范化的新内容均为 `status="draft"`、`reviewed=false`、`seo.noindex=true`。通过以下命令进行人工审核发布：

```bash
# 1. 查看待审核与已发布清单
python -m local_worker.pipeline.run review --action list

# 2. 审核通过指定文章（要求字数 >= 300 词，自动转为 status=published, reviewed=true, noindex=false）
python -m local_worker.pipeline.run review --action approve --target example-buying-guide --reviewer editor1 --notes "Ready for index"

# 3. 驳回不合规内容
python -m local_worker.pipeline.run review --action reject --target example-buying-guide --notes "Low quality"
```

审核决策保存在 `build/data/review_state.json`，重新运行流水线不会覆盖已有人工审核状态。在全自动 CI 场景下，可追加 `--auto-approve` 参数自动通过符合门禁的文章。

## 内容生成配置

`content:` 段（`model`、`temperature`、`max_words`、`timeout_seconds`）与 `llm:` 段会合并为一份 LLM 配置，`llm:` 中的同名键优先；`enable` 也接受 `enabled` 写法，缺省为关闭。只有 `llm.enable` 为真且环境中存在 `OPENAI_API_KEY` / `GEMINI_API_KEY` / `LLM_API_KEY` 之一时才会调用模型，否则使用模板生成。`category` 为 `health`、`medical` 或 `wellness` 的条目会走健康内容提示词，并只允许使用 master DB 中处于启用状态的联盟短代码。

模型会同时返回 `title`，生成的条目优先用它作为标题（选题线索的标题常常只是站点名，例如「衛生福利部食品藥物管理署」）；去重 id 仍由源 URL + 线索标题派生，所以改标题不会造成重复生成。中文站点的 `app.default_locale` 建议设为 `zh-CN`。

**LLM 失败时的行为**：普通源（RSS / JSON / 动态页）会退回模板生成；`topic` 选题线索是纯 LLM 生成的，失败时**不写入** `content_items.json`（模板骨架是英文，写进去还会占住 id 导致永远不再重试），只记录 warning 并在下次运行 `content` 时自动重试。此时若没有新条目，`content_items.json` 不会被重写。

## 数据源适配器配置

支持 `json_file`、`rss`、`topic_search`（搜索 API 选题发现）以及 `playwright`（无头浏览器动态抓取）：

```yaml
scraper:
  sources:
    - id: curated
      type: json_file
      enabled: true
      path: "config/curated-input.example.json"
    - id: tech_feed
      type: rss
      enabled: true
      url: "https://example.com/rss.xml"
    - id: topic_health_zh
      type: topic_search
      enabled: true
      queries:
        - "避孕套 选购 尺寸 材质"
      num_results: 8
      default_category: "health"
      default_tags: ["topic", "health"]
    - id: dynamic_page
      type: playwright
      enabled: false
      url: "https://example.com/products"
      wait_selector: ".product-grid"
```

### 🔎 选题发现（topic_search）

`topic_search` 通过搜索 API（默认 Exa，`POST https://api.exa.ai/search`）按关键词检索选题，只取标题、URL 与短摘要作为写作线索，**不抓取、不复用第三方正文**。生成的条目会带上 `topic` 标签，`content` 阶段据此使用「原创写作」提示词，模板兜底也不会把检索摘要写进正文。

需要设置 `EXA_API_KEY`（写入 `.env` 或环境变量，`scrape` 阶段会加载项目根目录的 `.env`）。它调用的是文档化的 API 端点，因此不受 robots.txt 约束；被 robots 禁止的页面仍然会被 `playwright` 适配器拒绝。

### 📡 RSS 源

| 名称 | URL |
| :--- | :--- |
|BBC Technology                 | https://feeds.bbci.co.uk/news/technology/rss.xml |
|Hacker News (Best)             | https://hnrss.org/best                            |
|TechCrunch                     | https://techcrunch.com/feed/                      |
|The Verge                      | https://www.theverge.com/rss/index.xml            |

### 📄 JSON API

| 名称 | URL |
| :--- | :--- |
|JSONPlaceholder Posts                | https://jsonplaceholder.typicode.com/posts
|Hacker News Top Stories              | https://hacker-news.firebaseio.com/v0/topstories.json
|DummyJSON Products                   | https://dummyjson.com/products

### 🌐 Dynamic Page (需要 Playwright)
| 名称 | URL | wait_selector |
| :--- | :--- | :---: |

|Quotes to Scrape (JS)  | https://quotes.toscrape.com/js/         | .quote
|Books to Scrape        | https://books.toscrape.com/             | .product_pod
|SPA Quotes (scroll)    | https://quotes.toscrape.com/scroll      | .quote


## 测试与代码规范

```bash
pytest 01-local-worker/tests
ruff check 01-local-worker
```
