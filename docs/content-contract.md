# Content Contract

Layer 1 的稳定输出契约。Layer 3 的模板、校验脚本和后续发布流程都以这里的字段为准。

## 输出文件

| 阶段 | 文件 | 用途 |
| --- | --- | --- |
| `scrape` | `01-local-worker/build/data/raw_items.json` | 数据源采集结果，保留原始正文与来源信息 |
| `content` | `01-local-worker/build/data/content_items.json` | 规范化后的最终内容条目 |
| `content` | `01-local-worker/build/data/manifest.json` | 本轮运行统计 |
| `build` | `01-local-worker/build/public/` | Hugo 静态站产物 |

## Raw Item

`raw_items.json` 结构：

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-01-02T09:00:00Z",
  "source_ids": ["curated"],
  "item_count": 1,
  "items": [
    {
      "source_id": "curated",
      "source_url": "https://example.com/guide",
      "title": "Example Buying Guide",
      "summary": "A short summary.",
      "body": "Raw body text or HTML.",
      "body_format": "markdown",
      "retrieved_at": "2026-01-02T09:00:00Z",
      "category": "guides",
      "tags": ["guide"],
      "keywords": ["comparison"],
      "published_at": "2026-01-01T09:00:00Z",
      "publisher": "Example Editorial",
      "license": "CC-BY-4.0",
      "affiliate_slug": "example-offer"
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `source_id` | string | 是 | 配置中的数据源 ID |
| `source_url` | string | 是 | 原始页面 URL，必须是 http/https |
| `title` | string | 是 | 原始标题 |
| `summary` | string | 否 | 原始摘要，可为 HTML |
| `body` | string | 是 | 原始正文 |
| `body_format` | string | 是 | `html`、`markdown` 或 `text` |
| `retrieved_at` | ISO 8601 | 是 | 采集时间 |
| `category` | string | 是 | 默认 `general` |
| `tags` | string[] | 否 | 来源标签 |
| `keywords` | string[] | 否 | 关键词 |
| `published_at` | ISO 8601 | 否 | 原文发布时间 |
| `publisher` | string | 否 | 发布方 |
| `license` | string | 否 | 内容许可，未知时留空 |
| `affiliate_slug` | string | 否 | 关联的 offer slug |

## Content Item

`content_items.json` 结构：

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-01-02T09:05:00Z",
  "item_count": 1,
  "items": [
    {
      "schema_version": "1.0",
      "id": "4f8c2a9b1d3e5f70",
      "slug": "example-buying-guide",
      "title": "Example Buying Guide",
      "summary": "A short summary.",
      "body_markdown": "Compare materials, dimensions, and warranty terms before buying.",
      "word_count": 12,
      "locale": "en",
      "category": "guides",
      "tags": ["guide"],
      "keywords": ["comparison"],
      "content_rating": "general",
      "affiliate_slug": "example-offer",
      "sources": [
        {
          "url": "https://example.com/guide",
          "title": "Example Buying Guide",
          "publisher": "Example Editorial",
          "license": "CC-BY-4.0",
          "retrieved_at": "2026-01-02T09:00:00Z"
        }
      ],
      "status": "draft",
      "reviewed": false,
      "published_at": null,
      "updated_at": "2026-01-02T09:05:00Z",
      "seo": {
        "title": "Example Buying Guide",
        "description": "A short summary.",
        "canonical": null,
        "noindex": true
      }
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | string | 当前固定为 `1.0` |
| `id` | string | 稳定 ID，由来源 URL 与标题哈希生成 |
| `slug` | string | 小写字母、数字、连字符；同轮运行内唯一 |
| `title` | string | 3-160 字符 |
| `summary` | string | 非空，最多 320 字符 |
| `body_markdown` | string | 规范化后的正文 |
| `word_count` | int | 正文词数，用于发布门槛 |
| `locale` | string | 默认来自 `app.default_locale` |
| `category` | string | 内容分类 |
| `tags` | string[] | 去重后的标签 |
| `keywords` | string[] | 去重后的关键词 |
| `content_rating` | string | `general` 或 `sensitive` |
| `affiliate_slug` | string \| null | 对应 KV 中的 `offer:<slug>` |
| `sources` | object[] | 至少一项，记录来源与许可 |
| `status` | string | `draft`、`review`、`published`、`archived` |
| `reviewed` | bool | 是否完成人工审核 |
| `published_at` | ISO 8601 \| null | 发布时间 |
| `updated_at` | ISO 8601 | 最后更新时间 |
| `seo` | object | 标题、描述、canonical、noindex |

## 校验规则

- `id` 与 `slug` 在单轮输出内唯一。
- `sources` 不能为空，URL 必须是 http/https。
- 非 `published` 条目必须 `seo.noindex=true`。
- `published` 条目必须满足：`reviewed=true`、`seo.noindex=false`、有 `published_at`、正文至少 300 词。
- `affiliate_slug` 必须符合 slug 格式。
- `content_rating` 只能是 `general` 或 `sensitive`。
- 未审核内容不得进入公开构建。

## 状态流转

```text
draft -> review -> published -> archived
```

- `draft`：刚规范化的内容，默认 `noindex`。
- `review`：等待人工审核或事实核查。
- `published`：审核完成，允许索引与展示。
- `archived`：下线但保留记录。

## Manifest

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-01-02T09:05:00Z",
  "raw_item_count": 10,
  "content_item_count": 10,
  "status_counts": {"draft": 10},
  "category_counts": {"guides": 10},
  "affiliate_linked_count": 2,
  "outputs": {
    "raw_items": "raw_items.json",
    "content_items": "content_items.json"
  }
}
```
