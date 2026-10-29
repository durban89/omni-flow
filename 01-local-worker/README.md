# Layer 1: Local Worker

本地数据生产层，运行在开发机或 CI，不对外暴露端口。

## 职责

- 采集公开需求信号与关键词，保留来源、时间和许可信息。
- 清洗、去重、规范化内容，并执行输出契约校验。
- 组装 Hugo 数据与内容，构建静态站。
- 输出可被 Layer 3 和部署脚本消费的结构化结果。

## 目录

```text
01-local-worker/
├── pyproject.toml
├── src/local_worker/
│   ├── config.py          # 设置加载
│   ├── models.py          # RawItem / ContentItem 数据契约
│   ├── errors.py          # 可预期的业务异常
│   ├── scraper/           # 数据源适配器与 HTTP 客户端
│   ├── content/           # 清洗、规范化、校验
│   └── pipeline/          # 编排入口
├── tests/                 # 单元测试与 fixture
└── build/                 # 构建产物，已忽略
```

## 流水线阶段

1. `scrape`：读取 `scraper.sources`，采集并去重，输出 `build/data/raw_items.json`。
2. `content`：把 raw item 规范化为最终 `ContentItem`，输出 `build/data/content_items.json` 和 `manifest.json`。
3. `validate`：校验 `content_items.json` 的字段、唯一性、审核状态与 SEO 规则。
4. `build`：调用 Hugo，把静态站输出到 `build/public/`。

`all` 按 `scrape -> content -> validate -> build` 顺序执行。

## 数据源

当前支持两种适配器：

```yaml
scraper:
  sources:
    - id: curated
      type: json_file
      enabled: true
      path: "config/curated-input.example.json"
      default_category: "guides"
      default_tags: ["curated"]
    - id: example_feed
      type: rss
      enabled: false
      url: "https://example.com/feed.xml"
      default_category: "news"
      default_tags: ["rss"]
```

- `json_file`：读取本地人工整理或授权导入的数据，字段见 `docs/content-contract.md`。
- `rss`：解析 RSS 2.0 与 Atom，远程请求遵守 robots.txt、限速和超时设置。

新增数据源时在 `scraper/` 下实现独立适配器，并在 `scraper/__init__.py` 注册。

## 输出

最终内容契约见 [`docs/content-contract.md`](../docs/content-contract.md)。

- 所有新条目默认 `status=draft`、`reviewed=false`、`seo.noindex=true`。
- `published` 条目必须审核通过、正文至少 300 词，并关闭 `noindex`。
- 每个条目至少保留一个来源 URL。

## 开发

```bash
source ../.venv/bin/activate
python -m local_worker.pipeline.run scrape
python -m local_worker.pipeline.run content
python -m local_worker.pipeline.run validate
python -m local_worker.pipeline.run all --settings ../config/settings.yaml
pytest
ruff check .
```

## 约束

- 抓取严格遵守 robots.txt、服务条款和速率限制；不得绕过验证码或访问控制。
- 未通过人工审核的内容不得发布。
- 不把 API Key、联盟链接或个人信息写入代码和测试 fixture。
- 产物统一写入 `build/`，不提交到版本库。
