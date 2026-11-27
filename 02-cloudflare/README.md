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

### 一次性准备

```bash
cd 02-cloudflare/worker
npm install
```

1. `wrangler.toml` 被 gitignore，先从模板复制：

   ```bash
   cp wrangler.example.toml wrangler.toml
   ```

2. 创建 KV namespace（正式 / 测试各一个），把返回的 `id` 填入根目录 `.env`（不要提交）：

   ```bash
   npx wrangler kv namespace create condom-iq
   npx wrangler kv namespace create condom-iq-dev
   ```

   ```dotenv
   CLOUDFLARE_KV_NAMESPACE_ID_PROD=<condom-iq 的 id>
   CLOUDFLARE_KV_NAMESPACE_ID_DEV=<condom-iq-dev 的 id>
   ```

   `sync-kv` 按模式自动选择：`production` 用 `_PROD`，`test` 用 `_DEV`；如需临时指定 namespace，设置 `CLOUDFLARE_KV_NAMESPACE_ID`（变量名由 `cloudflare.kv_namespace_env` 配置）即可覆盖。

   同时把正式 namespace 的 `id` 填到本地 `wrangler.toml` 的 `[[kv_namespaces]]`（该文件已 gitignore），`binding` 必须与 `config/settings.yaml` 的 `cloudflare.kv_namespace_binding` 一致（默认 `APP_KV`）。

3. 创建 R2 bucket（如使用）。

4. 导出 Cloudflare 认证变量（参考根目录 `.env.example`）：

   ```bash
   export CLOUDFLARE_ACCOUNT_ID=...
   export CLOUDFLARE_API_TOKEN=...
   ```

   `CLOUDFLARE_PAGES_PROJECT` 默认留空，由运行模式选择 Pages 项目（`test` → `condom-iq-dev`，`production` → `condom-iq`）；只有需要临时覆盖时才设置它。

   运行时密钥用 `npx wrangler secret put <NAME>` 写入，不要写进 `wrangler.toml`。

### 本地调试

```bash
cd 02-cloudflare/worker
npx wrangler dev
```

前台阻塞运行，按 Ctrl+C 退出后再执行部署。

### 部署 Worker

Worker 名按模式区分：`production` 为 `condom-iq-router`，`test` 为 `condom-iq-router-dev`。推荐用 CLI / 控制台部署，因为只有它们会按模式切换 Worker 名与 KV 绑定：

```bash
cd 01-local-worker
python -m local_worker.pipeline.run deploy-worker --mode test   # 绑 condom-iq-dev
python -m local_worker.pipeline.run deploy-worker --mode production  # 绑 condom-iq
```

该阶段读取 `02-cloudflare/worker/wrangler.toml`，把 `name` 改成当前模式的 `cloudflare.worker_name`、把 `[[kv_namespaces]].id` 换成当前模式的 namespace，写到同目录的 `wrangler.deploy.toml`（已 gitignore）后执行 `npx wrangler deploy --config wrangler.deploy.toml`。`routes` 等配置会原样保留。也可以在本地控制台点「部署 Worker」，日志直接显示在面板里。

直接 `cd 02-cloudflare/worker && npm run deploy` 也能发布，但它不会切换 KV：永远使用 `wrangler.toml` 里写死的 namespace（正式库）。

### 构建并部署 Pages

在仓库根目录：

```bash
make build              # Hugo -> 01-local-worker/build/public
npx wrangler pages deploy 01-local-worker/build/public \
  --project-name "${CLOUDFLARE_PAGES_PROJECT:-condom-iq}"
```

### 从控制台或 CLI 发布 Pages

```bash
cd 01-local-worker
python -m local_worker.pipeline.run deploy-pages
```

该阶段先构建 Hugo，再执行与上面相同的 `wrangler pages deploy`；控制台里的「同步 Pages」按钮走的就是它。项目名按模式取 `config/settings.yaml`：`test` → `condom-iq-dev`，`production` → `condom-iq`；`CLOUDFLARE_PAGES_PROJECT` 仅在显式设置时覆盖。它只发布 Pages，不包含 Worker 部署。

### 一键部署

```bash
make deploy
```

等价于 `scripts/deploy-pages.sh`：Hugo 构建 → `wrangler deploy` Worker → `wrangler pages deploy`。脚本不会自动 `npm install`，首次部署先完成上面的准备步骤。

### 绑定路由

Worker 只处理 `/out/*` 和 `/healthz`，不要绑定 `/*`，否则会拦截 Pages 的静态资源。可在 Cloudflare Dashboard 配置，或取消 `worker/wrangler.toml` 中 `[[routes]]` 的注释并填入自己的域名。

## KV 数据同步

从建 namespace、写 `.env`、配置绑定到验证跳转的完整流程见 [KV 初始化与同步](../docs/kv-setup.md)。以下是日常同步要点。

`python -m local_worker.pipeline.run sync-kv` 从 `config/affiliate-offers.json` 生成 `01-local-worker/build/data/kv_bulk.json`，键名为 `offer:<slug>`，其中 `<slug>` 取自 `masked_path` 的最后一段（`/out/okamoto-001` → `offer:okamoto-001`），与 Worker 的读取规则一致。

默认会自动执行 bulk put（在 `02-cloudflare/worker` 下用 `npx wrangler`，认证走 `.env` 的 `CLOUDFLARE_API_TOKEN` 或 `wrangler login` 会话）：

- `--mode production` → `CLOUDFLARE_KV_NAMESPACE_ID_PROD`（condom-iq）；
- `--mode test`（默认）→ `CLOUDFLARE_KV_NAMESPACE_ID_DEV`（condom-iq-dev）；
- 设置 `CLOUDFLARE_KV_NAMESPACE_ID` 则优先使用它。

手动推送时同样要指定 namespace（否则会退回 `wrangler.toml` 里的 `APP_KV` 绑定）：

```bash
npx wrangler kv bulk put ../../01-local-worker/build/data/kv_bulk.json \
  --namespace-id "$CLOUDFLARE_KV_NAMESPACE_ID_DEV" --remote
```

注意：bulk put 只新增/覆盖键，不会删除已从 `affiliate-offers.json` 移除的键。暂停或下线的 offer 目前不会被自动清理（`sync-affiliate` 会跳过 inactive 条目），需要手动删除：

```bash
npx wrangler kv key delete "offer:<slug>" --namespace-id "$CLOUDFLARE_KV_NAMESPACE_ID_DEV" --remote
```
