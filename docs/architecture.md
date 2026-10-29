# Architecture

## 目标

用最低的常驻成本运行一个可审计的 CPS 内容站：本地生成静态产物，Cloudflare 负责托管、路由和边缘配置，公开分发层只消费已审核的数据。

## 分层边界

### Layer 1: Local Worker

- 运行在开发机或 CI，负责采集、清洗、生成草案、构建与校验。
- 唯一产出是静态文件和结构化映射，不直接对外提供请求。
- 所有外部数据源必须可追溯：来源 URL、抓取时间、许可依据、审核状态。

### Layer 2: Cloudflare

- Pages：托管 Layer 1 产出的静态站。
- Worker：只处理 `/healthz` 和 `/out/*`，不拦截静态资源请求，避免回源循环。
- KV：保存 offer 映射和运行时配置，key 采用 `offer:<slug>` / `config:<name>`。
- R2：保存大体积素材或需要独立生命周期的资源；Pages 已能托管的静态文件不重复放 R2。

### Layer 3: Distribution

- Hugo 负责页面结构与 SEO 元数据。
- 内容、落地页和跳转数据分离，模板不直接调用爬虫或 LLM。
- 所有联盟链接通过站内 `/out/<slug>` 中转，并展示联盟披露。

## 数据契约

Layer 1 的 raw item、最终 content item 与 manifest 字段见 [`content-contract.md`](content-contract.md)。

### Offer 映射

```json
{
  "slug": "example-offer",
  "destination": "https://example.com/affiliate?ref=REPLACE_ME",
  "status": "paused",
  "geo": ["US", "CA", "GB"],
  "disclosure_required": true
}
```

- `slug` 只允许小写字母、数字和连字符。
- `destination` 必须是 allowlist 中的联盟域名。
- `status` 为 `active` 时才允许跳转。
- 跳转使用 `302` 和 `Cache-Control: no-store`，保证下线与换链可回滚。

### 内容前置元数据

```yaml
title: ""
description: ""
draft: true
reviewed: false
sources: []
```

`reviewed` 为 `false` 的内容不得进入公开构建。

## 部署顺序

1. Layer 1 执行 scrape / content / validate。
2. Hugo 构建 `01-local-worker/build/public`，或直接执行 `build` 阶段。
3. 同步 `affiliate-map.json` 到 KV。
4. `wrangler pages deploy` 发布静态站。
5. `wrangler deploy` 发布 Worker。
6. 验证 `/healthz`、`/out/<slug>` 和关键页面。

## 非目标

- 不对搜索引擎与真实用户返回不同内容。
- 不做门页、批量低质页面或伪造资质。
- 不抓取未授权或明确禁止抓取的数据源。
- 不把联盟链接或密钥硬编码进公开仓库。

## 已知风险

- 本地流水线是单点：需要 CI 触发、失败重试和产物备份。
- KV 免费额度写入有限：批量同步需要合并写入。
- Worker 路由配置错误会拦截 Pages 资源：部署后必须做冒烟测试。
- 目标品类可能涉及地区法规、支付通道和内容分级：上线前需单独合规评估。
