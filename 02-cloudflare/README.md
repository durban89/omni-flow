# Layer 2: Cloudflare Serverless Infrastructure

运行时与边缘存储层，不包含采集和内容生成逻辑。

## 组件

| 组件 | 用途 |
| --- | --- |
| Cloudflare Pages | 托管 Layer 1 构建出的静态站 |
| Cloudflare Workers | 处理 `/healthz` 与 `/out/:slug` 跳转 |
| Cloudflare KV | 存储 `offer:<slug>` 和 `config:<name>` |
| Cloudflare R2 | 存储大体积素材或独立生命周期的资源 |

## Worker

入口：`worker/src/index.ts`

- `GET /healthz`：返回运行环境状态。
- `GET /out/:slug`：从 KV 读取 offer，若 `status=active` 则返回 `302`。
- 其他路径返回 `404`。Worker 路由只绑定 `/out/*` 和 `/healthz`，不要用 `/*` 拦截 Pages 静态资源。

跳转策略：

- 使用 `302`，不使用长期缓存的 `301`。
- 响应头设置 `Cache-Control: no-store`。
- offer 不存在或已暂停时返回 `404`。
- 目标域名需经过 allowlist 校验（后续在同步脚本中实现）。

## KV 命名

```text
offer:<slug>  -> { destination, status, geo?, disclosure_required }
config:<name> -> 运行时配置
```

## 部署

```bash
cd worker
npm install
npx wrangler dev
npm run deploy

cd ../..
npx wrangler pages deploy 01-local-worker/build/public --project-name example-matrix
```

首次部署前：

1. 创建 KV namespace，把 ID 写入 `worker/wrangler.toml`。
2. 创建 R2 bucket（如使用）。
3. 通过 `wrangler secret put` 配置密钥。
4. 在 Cloudflare Dashboard 为 Worker 配置 `/out/*` 和 `/healthz` 路由。
