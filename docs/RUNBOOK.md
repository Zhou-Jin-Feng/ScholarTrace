# ScholarTrace 本地运行手册（M6-M10）

## 1. 本地启动

环境要求：Python 3.11、uv、Node.js 20+ 和 npm。M6 的确定性演示不需要 API Key、DocuMind、ScholarGraph 或 Ollama。

启动 API：

```powershell
uv sync --all-groups
uv run uvicorn scholartrace.api.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

默认任务数据写入 `artifacts/m6-delivery`。部署到 Compose 时由 `SCHOLARTRACE_DATA_DIR` 指向持久卷；本地也可在启动前设置该变量以切换目录。单进程执行器默认 1 个 worker、4 个排队槽位，可用 `SCHOLARTRACE_WORKERS` 和 `SCHOLARTRACE_QUEUE_CAPACITY` 调整；这两个参数只控制本地进程，不提供跨进程协调。

另开终端启动前端：

```powershell
Set-Location frontend
npm ci
npm run dev
```

浏览器打开 `http://127.0.0.1:5173`。Vite 会把 `/api` 请求代理到 `127.0.0.1:8000`。

## 2. Compose 启动

```powershell
docker compose up --build
```

前端地址为 `http://127.0.0.1:5173`，API 健康检查为 `http://127.0.0.1:8000/api/v1/health/live`。Compose 的数据卷只保存任务元数据、事件和导出工件；不会下载论文或保存模型原始回答。

停止：

```powershell
docker compose down
```

不要使用 `docker compose down -v`，除非明确要删除本地任务数据卷。

### 2.1 部署模式与访问控制

默认 `SCHOLARTRACE_DEPLOYMENT_MODE=loopback`，Compose 将 API 和前端绑定到
`127.0.0.1`，适合单机使用。若需要让同一可信私网中的其他设备访问，必须显式切换为
`trusted_private` 并设置至少 16 个不含空白字符的共享 token：

```powershell
$env:SCHOLARTRACE_DEPLOYMENT_MODE = "trusted_private"
$env:SCHOLARTRACE_AUTH_TOKEN = "替换为至少16位的随机token"
$env:SCHOLARTRACE_BIND_ADDRESS = "192.168.1.20"
docker compose up --build
```

`SCHOLARTRACE_BIND_ADDRESS` 只控制 Compose 端口绑定；它不把服务变成公网服务，也不替代
防火墙、VPN、反向代理或 TLS。`trusted_private` 是单用户/共享密钥边界：所有持有同一 token
的客户端拥有相同的任务和报告访问权，没有账号、租户隔离或逐对象授权。不要把该模式直接暴露
到公网。

浏览器工作台会把 token 保存在当前会话的 `sessionStorage`（不可用时仅保存在内存），普通
请求使用 `Authorization: Bearer`。原生 `EventSource` 和报告下载不能自定义请求头，因此只
对这两个 `GET` 端点使用短时 URL 查询参数 `access_token`；该参数可能出现在浏览器历史、代理
或监控记录中，必须使用 TLS、关闭访问日志中的查询串并在会话结束后清除 token。应用日志只记录
路径和请求 ID，不记录 token。

健康检查 `health/live`、`health/ready` 和静态前端保持可探测；任务、事件、工件和报告路径均
需要认证。启动时 `trusted_private` 缺少 token 或 token 不合规会直接失败（fail closed）。

## 3. API 最小流程

创建任务：

```powershell
$task = Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/research/tasks `
  -Headers @{"Idempotency-Key" = "manual-m6-demo-1"} `
  -ContentType "application/json" `
  -Body '{"question":"How can retrieval evidence improve RAG reports?","demo_mode":"success"}'

$task.task_id
```

在 `trusted_private` 模式下，为任务创建、审批和取消请求增加 Header：

```powershell
$auth = @{ Authorization = "Bearer $env:SCHOLARTRACE_AUTH_TOKEN" }
Invoke-RestMethod -Method Get -Uri http://127.0.0.1:8000/api/v1/health/ready
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/research/tasks `
  -Headers ($auth + @{"Idempotency-Key" = "manual-private-demo-1"}) `
  -ContentType "application/json" `
  -Body '{"question":"How can retrieval evidence improve RAG reports?","demo_mode":"success"}'
```

事件流和报告下载按前文说明使用 `access_token` 查询参数；不要把 token 写入脚本、仓库或长期
浏览器书签。

审批并运行确定性演示：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri ("http://127.0.0.1:8000/api/v1/research/tasks/{0}/approve" -f $task.task_id) `
  -ContentType "application/json" `
  -Body '{"action":"approve","reason":"manual demo approval"}'
```

报告格式：`markdown`、`html`、`pdf`、`json`。事件默认返回已持久事件；增加 `?follow=true` 后
保持 SSE 长连接，发送 `retry`、增量事件和空闲 heartbeat。浏览器 `EventSource` 会自动使用
`Last-Event-ID` 断线续传；终态 `exports_ready` 后服务发送 `stream_end` 并关闭连接。

手工观察长连接：

```powershell
curl.exe -N "http://127.0.0.1:8000/api/v1/research/tasks/<task_id>/events?follow=true"
```

`Last-Event-ID` 也可作为查询参数 `last_event_id=event:<sequence>` 传入；游标必须属于同一
任务，格式错误、跨任务游标或同时传入冲突的 Header/查询游标会返回 `400`。不存在的任务返回
`404`，避免建立永不结束的空连接。

审批后任务会进入 `queued`/`running` 状态；队列已满时审批返回 `429`，客户端应按
`Retry-After` 重试。取消正在排队或运行的任务：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri ("http://127.0.0.1:8000/api/v1/research/tasks/{0}/cancel" -f $task.task_id)
```

`GET /api/v1/health/ready` 会返回 `ready` 或 `draining` 以及当前队列快照。服务停止时先停止
接收新审批，等待已接收任务排空；超时任务收到取消信号并由任务边界安全结束。

## 4. M6 评测与 Demo

```powershell
uv run python scripts/run_m6_evaluation.py
uv run python scripts/run_m6_demo.py
uv run python scripts/run_m6_scholargraph_capacity.py --repeat-count 3
uv run python scripts/run_m10_p1_load.py --task-count 12
```

评测和 Demo 只写脱敏 JSON 到 `evaluation/reports/`；临时 SQLite 位于已忽略的 `artifacts/`。M10-P1 负载命令仅运行本地确定性控制面，分 1/2/4/8 并发档位记录 429 背压、恢复、延迟和终态 SSE，不访问付费模型、DocuMind、ScholarGraph 或 Ollama。容量命令要求冻结 ScholarGraph `1.2.0` 已在 `127.0.0.1:8002` ready，按并发 1 顺序执行三次 Basic，不访问付费模型，不保存问题或答案。矩阵中的 `limited` 或 `missing` 表示质量证据缺口；B3/B4 的 `measured` 表示已完成人工盲审，不表示 B4 获得收益。

### 4.1 M10-P3 全文 acquisition smoke

先确认 DocuMind `2.2.0` 的 `health/ready` 返回 retrieval ready，且 Ollama 已安装并可预热
`qwen3-embedding`，再执行：

```powershell
uv run python scripts/run_m10_p3_smoke.py
```

该命令只读取 `tests/fixtures/documind/m2_three_papers.json` 中的三篇版本化公开 arXiv
来源，逐跳限制 HTTPS/域名/端口、最多 3 次重定向、`application/pdf`、30 MiB 和 `%PDF-`
魔数；完整下载后计算 `source_sha256`，再上传到 DocuMind 并执行单论文 Dense retrieve。
临时 PDF 在 `artifacts/m2-live-documents/`，绑定和报告在 `artifacts/m2-evidence-live/`，均被
`.gitignore` 排除。默认只删除本次新建的 DocuMind 文档，`--keep-documents` 才保留索引。

不要把 DOI、OpenAlex landing page、出版社登录页或 ScholarGraph 摘要直接传给 acquisition；
这类来源没有在本阶段建立合法全文策略。下载失败会返回结构化 acquisition code，并清理
未完成临时文件；DocuMind 返回 hash 不一致时 fail closed。

## 5. 安装、升级、备份与恢复

### 5.1 安装与固定数据目录

源码安装要求 Python 3.11、uv、Node.js 20+ 和 npm：

```powershell
uv sync --locked --all-groups
Push-Location frontend
npm ci
npm run build
Pop-Location
uv run pytest --basetemp artifacts/pytest-install
```

Compose 安装使用 `docker compose build --pull=false` 和 `docker compose up -d`。API 镜像通过
固定的 `uv==0.12.3` 严格消费 `uv.lock`，前端通过 `npm ci` 消费锁文件。默认容器数据根是
`/var/lib/scholartrace`，宿主机开发默认是 `artifacts/m6-delivery`。不要把临时测试目录或
DocuMind 数据目录混成同一个 `SCHOLARTRACE_DATA_DIR`。

健康检查顺序：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/live
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/ready
Invoke-WebRequest http://127.0.0.1:5173 -UseBasicParsing
```

`live` 只证明进程响应；只有 `ready.status=ready` 且 queue 仍 accepting 才可接收新审批。
需要全文任务时还必须单独检查 DocuMind `127.0.0.1:8001/api/v1/health/ready` 的
`components.retrieval=ready`。

### 5.2 宿主机备份与隔离恢复

发布或升级前先停止新审批，等待 running/queued 清零并停止 API。备份输出必须在数据目录外：

```powershell
New-Item -ItemType Directory -Force backups | Out-Null
uv run scholartrace-backup `
  --data-dir artifacts/m6-delivery `
  --output backups/scholartrace-0.5.0-20260904.zip
```

命令输出的 `archive_sha256`、`manifest_sha256`、数据库数量和字节数应另行保存。备份包含
`tasks.sqlite`、`runtime.sqlite` 及数据根子目录中的其他 SQLite；报告是 `tasks.sqlite` 中的
hash 校验 BLOB。`.env`、PDF、缓存、日志、临时文件、`*-wal` 和 `*-shm` 不进入归档。

恢复永远写到不存在的新目录：

```powershell
uv run scholartrace-restore `
  --archive backups/scholartrace-0.5.0-20260904.zip `
  --target-dir artifacts/m6-delivery-restored-20260904
$env:SCHOLARTRACE_DATA_DIR = "artifacts/m6-delivery-restored-20260904"
uv run uvicorn scholartrace.api.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

只有恢复输出 `verified=true`，且 live/ready、任务计数、事件回放和报告下载抽查通过后，才把
新目录设为长期配置。目标目录已存在、版本不匹配或任一 hash/完整性/表计数不一致时，恢复会
失败关闭；不要使用文件管理器直接把 WAL 主库复制回来。

### 5.3 Compose 备份、恢复与切换

容器镜像内安装了 `scholartrace-backup` 和 `scholartrace-restore`。备份前先 drain 并停止 API：

```powershell
docker compose stop scholartrace-api
New-Item -ItemType Directory -Force backups | Out-Null
docker compose run --rm --no-deps `
  -v "${PWD}/backups:/backup" `
  scholartrace-api scholartrace-backup `
  --data-dir /var/lib/scholartrace `
  --output /backup/scholartrace-0.5.0-20260904.zip
```

恢复到同一数据卷中的新子目录，再显式切换：

```powershell
docker compose run --rm --no-deps `
  -v "${PWD}/backups:/backup" `
  scholartrace-api scholartrace-restore `
  --archive /backup/scholartrace-0.5.0-20260904.zip `
  --target-dir /var/lib/scholartrace/restored-20260904
```

随后在未跟踪 `.env` 中设置
`SCHOLARTRACE_DATA_DIR=/var/lib/scholartrace/restored-20260904`，执行 `docker compose up -d`
并完成 health、任务、SSE 和报告抽查。旧目录保留到观察期结束；不要在验证前删除。

### 5.4 升级与回滚

1. 记录当前发布包/提交和 `SCHOLARTRACE_DATA_DIR`，drain 队列并停止 API；
2. 用当前版本创建备份并完成一次隔离恢复校验；
3. 解压新发布包到新目录，执行 `uv sync --locked`/`npm ci` 或重新构建 Compose；
4. 先用恢复副本启动新版本，再检查 live、ready、SSE、任务和报告；
5. 当前 `0.5.0` 没有自动数据库迁移器；版本不一致时不得强行恢复；
6. 升级失败时停止新版本，切回旧发布包和旧数据目录，确认 ready 后再恢复服务。

确定性本地源代码发布包：

```powershell
uv run python scripts/run_m10_p4_release_drill.py
```

该脚本连续构建两次，只有 ZIP SHA-256 相同才写出
`artifacts/m10-p4/ScholarTrace-0.5.0-final.zip`，公开摘要写入
`evaluation/reports/m10_p4_release.json`。包包含锁文件但不捆绑 Docker 镜像或依赖缓存，首次
安装仍需要可用的软件包/镜像源。

### 5.5 DocuMind 恢复边界

ScholarTrace 备份只覆盖自身任务、事件、报告和放入同一数据根的 SQLite，不覆盖 DocuMind
`DocumentRegistry`、Milvus collection、Ollama 模型或原始 PDF。灾难恢复后已有报告仍可读取，
但新的 Evidence 检索只有在 DocuMind 注册表、向量和源文档也恢复或重新入库后才可继续。
DocuMind 应按其自己的版本和备份说明独立恢复，随后检查 `components.retrieval=ready`，再抽查
`document_key/index_id/source_sha256` 闭合；不要把 ScholarTrace 的 binding 当作向量备份。

## 6. 故障排查

| 现象 | 检查 | 处理 |
|---|---|---|
| API 端口不可达 | `GET /api/v1/health/live` | 检查 uvicorn/Compose 日志和端口占用 |
| 前端显示 API 错误 | 浏览器 Network、API health | 先启动 API，再启动 Vite；确认代理端口 8000 |
| 任务重复创建 | 是否复用了不同请求的 Idempotency-Key | 同一 Key 只能对应同一请求内容 |
| 报告 404 | 任务是否已审批并产生终态 | 先调用 approve；仅 waiting_approval 没有导出 |
| 审批返回 429 | `GET /api/v1/health/ready` 的 `queue.queued` | 按 `Retry-After` 等待后重试 approve；不要重复创建任务 |
| 任务长期 queued/running | ready 快照、SSE 时间线和 API 日志 | 检查 worker 是否存活；必要时调用 cancel，停机时让服务自行 drain |
| SSE 连接反复重连 | 浏览器 Network、`Event stream` 状态和 API 日志 | 确认 API 进程、代理不缓冲 SSE；浏览器会用 `Last-Event-ID` 补发，不要手工重复提交任务 |
| ScholarGraph 503 | readiness、版本和服务日志 | 上层回退 B3；不要在 Consumer 内无限重试昂贵 query |
| Ollama 冷启动 503 | DocuMind `health/ready` 的 retrieval 组件 | 使用 DocuMind 的 600 秒有界驻留与 60 秒冷加载探针；必要时预热 `qwen3-embedding` 后重新检查，再进行有界检索 |
| 全文下载被拒绝 | acquisition error code、来源 URL 和 response headers | 只使用版本化公开 arXiv；检查 HTTPS、allowlist、类型和大小，不要放宽到任意域名 |
| DocuMind source hash 不一致 | P3 summary 与 binding 的 `source_sha256` | 删除失败 binding 并重新获取；不要继续生成 Evidence |
| 清理返回 404/部分失败 | `DocumentCleanupError.failed_document_keys` | 404 可视为已清理；其余键按报告重试，不要删除既有文档 |
| 备份找不到数据库 | `SCHOLARTRACE_DATA_DIR` 和卷挂载 | 指向实际数据根；不要用空临时目录冒充成功备份 |
| 恢复拒绝版本 | 清单 `application_version` 与当前程序 | 用原版本先恢复；无迁移器时不要跳过版本检查 |
| 恢复目标已存在 | `--target-dir` | 选择新的兄弟目录；不要覆盖或先清空当前目录 |
| 恢复后 DocuMind Evidence 不可用 | DocuMind ready、registry、Milvus 和 binding | 独立恢复/重建 DocuMind，再核对 document/index/source hash |
| UI 容器立即报 `host not found in upstream scholartrace-api` | 容器网络和 API 服务名 | 用 Compose 启动，或确保 UI/API 同网络且 API 别名为 `scholartrace-api` |
| Docker Hub OAuth 超时 | 基础镜像元数据/授权日志 | 从可信镜像代理拉取相同 tag/digest，在本机重打官方 tag 后用 `--pull=false` 重试 |

## 7. 数据和安全

- 不把 `.env`、API Key、论文全文、原始模型回答、Prompt 或内部路径放入 Git；
- 导出报告只包含任务元数据、事件、工件哈希和安全限制说明；
- 生产 `api-strong` 仍需单独配置、价格快照和人工批准；
- `production_unavailable` 演示模式会显式降级，不会静默调用本地模型。
- `loopback` 是默认安全边界；设置 `SCHOLARTRACE_AUTH_TOKEN` 即使在 loopback 下也会启用
  任务 API 认证；`trusted_private` 必须同时设置 token；
- 共享 token 不是多用户安全方案，公网、账号体系、租户隔离、细粒度 Artifact 授权和审计仍
  不在 M10-P2 范围内。
- M10-P3 acquisition 只支持版本化公开 arXiv；不绕过付费墙或访问控制，不提供断点续传，
  不把摘要当作全文 Evidence；
- M10-P4 备份不包含 `.env`、PDF、缓存或 SQLite sidecar，恢复只允许新目录。DocuMind 和
  ScholarGraph 都是独立上游，不被 ScholarTrace 备份隐式覆盖。
