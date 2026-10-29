# Layer 3: Public Distribution Layer

公开分发与转化层，消费 Layer 1 产出的内容和映射，构建后由 Cloudflare Pages 托管。

## 目录

```text
03-distribution/
├── site/          # Hugo 源站
│   ├── content/   # 页面内容
│   ├── layouts/   # 模板
│   ├── static/    # 原样拷贝的静态文件
│   ├── assets/    # 需要 Hugo Pipes 处理的资源
│   └── data/      # 结构化数据
├── landing/       # 落地页模板与组件
└── seo/           # 结构化数据、sitemap、robots、重定向清单
```

## 内容约定

页面 front matter：

```yaml
title: ""
description: ""
draft: true
reviewed: false
sources: []
```

- `reviewed: false` 的内容不得发布。
- 每个页面必须展示联盟披露。
- 页面级的内容分级、披露与地区限制组件放在 `landing/`。
- 联盟链接统一使用站内 `/out/<slug>`，不直接写裸联盟 URL。

## 构建

```bash
hugo --source 03-distribution/site --minify --destination 01-local-worker/build/public
```

构建产物写入 `01-local-worker/build/public`，由 `scripts/deploy-pages.sh` 发布。

## 开发

```bash
hugo server --source 03-distribution/site
```

新增页面使用 `hugo new content <path>.md`，生成后补齐 `description`、`sources`，审核通过再把 `reviewed` 改为 `true`。
