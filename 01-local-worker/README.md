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
│   │   └── playwright_source.py # 动态浏览器抓取适配器
│   ├── content/               # 清洗、生成、规范化、审核与导出
│   │   ├── text.py            # 富文本 HTML 转 Markdown、摘要提取、字数统计
│   │   ├── generator.py       # LLM 与合规模板内容扩展生成
│   │   ├── normalize.py       # RawItem 规范化并关联审核状态
│   │   ├── validate.py        # 唯一性与 SEO/状态门禁契约校验
│   │   ├── review.py          # 审核服务与 review_state.json 状态持久化
│   │   └── export_hugo.py     # 导出 Hugo Markdown 文章与 Data JSON
│   └── pipeline/              # 编排入口与同步工具
│       ├── run.py             # 分阶段执行入口 CLI
│       └── kv_sync.py         # Cloudflare KV bulk put 生成与同步
├── tests/                     # 单元与集成测试
└── build/                     # 构建产物（已忽略）
```

## 流水线阶段

| 阶段 | 命令 | 说明 |
| :--- | :--- | :--- |
| `scrape` | `python -m local_worker.pipeline.run scrape` | 采集各数据源并去重，输出 `build/data/raw_items.json` |
| `content` | `python -m local_worker.pipeline.run content` | 规范化为 `ContentItem`，输出 `content_items.json`，并同步导出至 Hugo |
| `validate` | `python -m local_worker.pipeline.run validate` | 校验内容契约、字数、唯一性、SEO 与审核发布门禁 |
| `export` | `python -m local_worker.pipeline.run export` | 将内容输出为 Hugo Markdown 文件 (`03-distribution/site/content/posts/*.md`) |
| `build` | `python -m local_worker.pipeline.run build` | 调用 Hugo 构建静态站产物输出到 `build/public/` |
| `sync-kv` | `python -m local_worker.pipeline.run sync-kv` | 校验 `affiliate-map.json` 并生成 Cloudflare KV bulk 数据/同步到 KV |
| `review` | `python -m local_worker.pipeline.run review` | 人工审核工作流（查看列表、通过审核、驳回） |
| `all` | `python -m local_worker.pipeline.run all` | 串联完整流程：`scrape -> content -> validate -> export -> build -> sync-kv` |

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

## 数据源适配器配置

支持 `json_file`、`rss` 以及 `playwright`（无头浏览器动态抓取）：

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
    - id: dynamic_page
      type: playwright
      enabled: false
      url: "https://example.com/products"
      wait_selector: ".product-grid"
```

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
