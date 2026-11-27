# Cloudflare KV 初始化与同步

Layer 2 的 Worker 只从 KV 读取 `offer:<slug>` 并返回 `302`。本文从零开始把 KV 配好，并说明日常同步、验证与回滚。

相关文件：

| 文件 | 作用 |
| --- | --- |
| `.env` | `CLOUDFLARE_ACCOUNT_ID`、`CLOUDFLARE_API_TOKEN`、各模式的 KV namespace id（不入库） |
| `config/master-affiliate-db.json` | 联盟主库（含真实推广链接，不入库） |
| `config/affiliate-offers.json` | `sync-affiliate` 从主库生成的 offers，供 KV 同步 |
| `02-cloudflare/worker/wrangler.toml` | 本地 Wrangler 配置（不入库），含 `APP_KV` 绑定 |
| `01-local-worker/build/data/kv_bulk.json` | `sync-kv` 生成的 bulk 上传文件 |

## 1. 前置条件

- Cloudflare 账号；自定义域名可选（没有域名时先用 `workers.dev` 子域验证）。
- Node.js 20+，Wrangler 通过 `npx wrangler` 调用，不需要全局安装。
- 本地已完成 `uv sync` 与 `npm install`（`./scripts/bootstrap.sh`）。
- 已准备 `config/master-affiliate-db.json`；没有的话先复制样例：

  ```bash
  cp config/master-affiliate-db.example.json config/master-affiliate-db.json
  ```

## 2. 认证 Wrangler

任选一种方式，二选一即可。

**方式 A：浏览器登录（本地开发推荐）**

```bash
cd 02-cloudflare/worker
npx wrangler login
npx wrangler whoami        # 确认账号与权限
```

**方式 B：API Token（CI 或无法开浏览器时）**

在 Cloudflare Dashboard 创建 Token，至少包含：

- Account / Workers KV Storage / Edit（读写 KV）
- Account / Workers Scripts / Edit（部署 Worker 时需要）
- Account / Cloudflare Pages / Edit（部署 Pages 时需要）

然后写入根目录 `.env`：

```dotenv
CLOUDFLARE_ACCOUNT_ID=<你的 account id>
CLOUDFLARE_API_TOKEN=<你的 token>
```

`sync-kv`、`deploy-worker`、`deploy-pages` 都通过 `.env`（`python-dotenv` 自动加载）或已有的 `wrangler login` 会话认证。

## 3. 创建两个 namespace

正式与测试各一个，避免本地试验写脏线上数据。namespace 的显示名只是标签，真正定位用的是返回的 `id`。

```bash
cd 02-cloudflare/worker
npx wrangler kv namespace create condom-iq-kv
npx wrangler kv namespace create condom-iq-kv-dev
```

输出形如：

```text
[[kv_namespaces]]
binding = "APP_KV"
id = "0123456789abcdef0123456789abcdef"
```

如果 namespace 已经存在，用下面的命令列出并取 `id`：

```bash
npx wrangler kv namespace list
```

## 4. 把 id 写入 .env

```dotenv
# production 模式使用
CLOUDFLARE_KV_NAMESPACE_ID_PROD=<condom-iq-kv 的 id>
# test 模式使用
CLOUDFLARE_KV_NAMESPACE_ID_DEV=<condom-iq-kv-dev 的 id>
# 可选：设置后覆盖上面两个，临时把同步指向指定 namespace
CLOUDFLARE_KV_NAMESPACE_ID=
```

选择规则（`01-local-worker/src/local_worker/pipeline/kv_sync.py`）：

1. `CLOUDFLARE_KV_NAMESPACE_ID`（变量名可用 `config/settings.yaml` 的 `cloudflare.kv_namespace_env` 改写）优先。
2. 否则按当前模式取：`production` → `_PROD`，`test` → `_DEV`。
3. 当前模式由 `--mode` > `LOCAL_WORKER_MODE` > `app.mode` 决定。

namespace id 属于环境配置，不要提交到仓库；`.env` 已被 `.gitignore` 忽略。

## 5. 配置 Worker 绑定

```bash
cd 02-cloudflare/worker
cp wrangler.example.toml wrangler.toml
```

- `[[kv_namespaces]].binding` 必须与 `config/settings.yaml` 的 `cloudflare.kv_namespace_binding` 一致（默认 `APP_KV`），Worker 代码通过 `env.APP_KV` 读取。
- `[[kv_namespaces]].id` 只是本地兜底值。`deploy-worker` 会按模式把 `name` 和 namespace id 改写成 `wrangler.deploy.toml` 再部署，所以正式 id 填这里即可；跑 `test` 模式时不会误用。

## 6. 生成 offers

从联盟主库生成两个产物：

```bash
uv run python -m local_worker.pipeline.run sync-affiliate
```

- `config/affiliate-offers.json`：含真实 `destination`，供 KV 同步。
- `03-distribution/site/data/affiliate-map.json`：前端脱敏映射，只含 product 与 `/out/<slug>`。

`slug` 取自主库条目的 `masked_path` 最后一段（`/out/okamoto-001` → `okamoto-001`），KV 键为 `offer:okamoto-001`，与 Worker 读取逻辑一致。`sync-affiliate` 会跳过 inactive 条目。

## 7. 校验后写入

先 dry run，只生成 `kv_bulk.json`、不发起网络写入：

```bash
uv run python -m local_worker.pipeline.run sync-kv --mode production --dry-run
```

检查 `01-local-worker/build/data/kv_bulk.json` 里的键值是否符合预期，再正式同步：

```bash
uv run python -m local_worker.pipeline.run sync-kv --mode production
# 测试环境
uv run python -m local_worker.pipeline.run sync-kv --mode test
```

实际写入的命令等价于：

```bash
cd 02-cloudflare/worker
npx wrangler kv bulk put ../../01-local-worker/build/data/kv_bulk.json \
  --namespace-id "$CLOUDFLARE_KV_NAMESPACE_ID_PROD" --remote
```

需要注意：

- bulk put 只新增/覆盖键，不会删除已从 offers 中消失的键；下线 offer 需手动删除。
- `make pipeline` / `run all` 的最后一步就是 `sync-kv`（非 dry run）。在 `test` 模式下它写的是 dev namespace，确认 `.env` 里的 id 对应正确环境。
- 校验失败（缺 `slug` / `destination`、`status` 不是 `active|paused|disabled`）会在写入前中断，不会产生半套数据。

## 8. 验证

读取单个键：

```bash
npx wrangler kv key get "offer:okamoto-001" \
  --namespace-id "$CLOUDFLARE_KV_NAMESPACE_ID_PROD" --remote
```

检查 Worker 跳转：

```bash
curl -i https://<worker 域名>/healthz
curl -i https://<worker 域名>/out/okamoto-001
```

期望：`/healthz` 返回 `{"ok":true,...}`；`/out/<slug>` 返回 `302`、`Location` 为联盟目标、`Cache-Control: no-store`；offer 不存在或 `status` 不是 `active` 时返回 `404`。

本地调试用 `make dev-worker`，此时读取的是 `wrangler.toml` 里绑定的 namespace；想验证 dev 数据，先按上面的方式把 dev id 填进去（或临时设置 `CLOUDFLARE_KV_NAMESPACE_ID`）。

## 9. 日常变更与回滚

- 改链接、调佣金、暂停 offer：改 `config/master-affiliate-db.json` → `sync-affiliate` → `sync-kv --mode <mode>`。跳转使用 `302` + `no-store`，变更立即生效。
- 临时下线：把主库条目的 `is_active` 设为 `false`（或从 offers 里移除），重新同步；已写入的键需要手动删除：

  ```bash
  npx wrangler kv key delete "offer:<slug>" \
    --namespace-id "$CLOUDFLARE_KV_NAMESPACE_ID_PROD" --remote
  ```

- 误写环境：如果 test 数据进了 production，重新按正确模式跑 `sync-kv` 覆盖即可；被删除键的影响面由 offer 列表决定。
- namespace 泄露或误删：namespace id 本身不是密钥，但删除 namespace 会清空全部键。重新创建后更新 `.env` 与 `wrangler.toml`，再跑一次 `sync-kv`。

## 10. 常见问题

**提示 `No KV namespace id configured; falling back to binding APP_KV`。** 当前模式对应的 `CLOUDFLARE_KV_NAMESPACE_ID_*` 为空，`sync-kv` 退回 `wrangler.toml` 的绑定（通常是正式库）。检查 `.env` 是否被加载、变量名是否拼错、`--mode` 是否与预期一致。

**报 `Authentication error` / `403`。** Token 缺少 Workers KV Storage 的 Edit 权限，或 `CLOUDFLARE_ACCOUNT_ID` 不属于该 Token；用 `npx wrangler whoami` 确认身份。

**`bulk put` 成功但 Worker 仍 404。** 三种常见原因：写到了另一个 namespace；Worker 部署时绑定了 `wrangler.toml` 的兜底 id 而不是当前模式；键名不是 `offer:<slug>`。用 `wrangler kv key get` 与 `deploy-worker --mode ...` 分别核对。

**dry run 也生成了文件？** `--dry-run` 只跳过网络写入，仍会写出 `kv_bulk.json` 供检查，这是预期行为。
