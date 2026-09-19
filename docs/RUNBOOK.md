# ScholarTrace 本地运行手册（M6-M10）

## 离线回归与私有工件验证

`uv run pytest` 使用可公开的确定性 Fixture，不依赖本机历史 Evidence 文件，不执行真实模型或付费调用。M2 pilot loader 的公开测试覆盖有效输入、哈希/来源/论文身份篡改、论文池漂移及缺失文件。

已有私有 M2 工件可独立只读校验。将 `SCHOLARTRACE_M2_REPORT` 指向本地工件后执行：

```powershell
uv run python scripts/verify_m2_pilot_packet.py `
  --report "$env:SCHOLARTRACE_M2_REPORT" `
  --expected-claims 12 --expected-evidence 11
```

上述数量对应冻结的三篇论文 M2 实测记录。其他输入须显式提供其预期数量。文件缺失、校验失败或数量不符会返回非零退出码；输出仅含汇总和哈希，不输出原始 Evidence。这是已有记录的确定性验证，不代表重新执行在线检索、模型推理或质量评测；原始工件仍不进入 Git 或发布包。

## 1. 本地启动

环境要求：Python 3.11、uv、Node.js 20+ 和 npm。M6 的确定性演示不需要 API Key、DocuMind、ScholarGraph 或 Ollama。

启动 API：

```powershell
uv sync --all-groups
uv run uvicorn scholartrace.api.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

当前 Web/API 装配的是确定性 Demo，并非全部真实研究组件的生产装配；上游与模型 smoke 是独立入口。

默认任务数据写入 `artifacts/m6-delivery`。本地启动前可设置 SCHOLARTRACE_DATA_DIR 切换目录：相对路径以项目根解析，绝对路径保持其位置。宿主机 uvicorn 不自动加载 .env，应显式设置环境变量。Compose 从 SCHOLARTRACE_CONTAINER_DATA_DIR 映射容器内 SCHOLARTRACE_DATA_DIR，默认 /var/lib/scholartrace；不能把宿主机路径直接填入容器变量。单进程执行器默认 1 个 worker、4 个排队槽位，可用 `SCHOLARTRACE_WORKERS` 和 `SCHOLARTRACE_QUEUE_CAPACITY` 调整；这两个参数只控制本地进程，不提供跨进程协调。

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

新运行的默认摘要写入已忽略的 `artifacts/reports/`，冻结的 evaluation/reports 保留原始证据、不随重跑更新；临时 SQLite 位于已忽略的 `artifacts/`。M10-P1 负载命令仅运行本地确定性控制面，分 1/2/4/8 并发档位记录 429 背压、恢复、延迟和终态 SSE，不访问付费模型、DocuMind、ScholarGraph 或 Ollama。容量命令要求冻结 ScholarGraph `1.2.0` 已在 `127.0.0.1:8002` ready，按并发 1 顺序执行三次 Basic，不访问付费模型，不保存问题或答案。矩阵中的 `limited` 或 `missing` 表示质量证据缺口；B3/B4 的 `measured` 表示已完成人工盲审，不表示 B4 获得收益。

### 4.1 M10-P3 全文 acquisition smoke

当前明确支持 DocuMind 3.0.0，同时保留原2.x。先在独立测试部署确认 DocuMind 的 `health/ready` 返回 retrieval ready，且 Ollama 已安装并可预热
`qwen3-embedding`，再执行：

```powershell
uv run python scripts/run_m10_p3_smoke.py --documind-version 3.0.0 --documind-commit <部署版本的完整40位SHA>
```

将上面的 SHA 占位符替换为经核对的实际值，不要原样执行。必须由操作者确认部署来自该干净提交；readiness 不提供 SHA 证明。版本与 SHA 在下载和模型调用前检查，实际 readiness/检索版本也须匹配，报告不再默认为旧 SHA。该约束同样适用于 run_m2_live_smoke.py。

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
  --output backups/scholartrace-1.0.1-20260919.zip
```

命令输出的 `archive_sha256`、`manifest_sha256`、数据库数量和字节数应另行保存。备份包含
`tasks.sqlite`、`runtime.sqlite` 及数据根子目录中的其他 SQLite；报告是 `tasks.sqlite` 中的
hash 校验 BLOB。`.env`、PDF、缓存、日志、临时文件、`*-wal` 和 `*-shm` 不进入归档。

恢复永远写到不存在的新目录：

```powershell
uv run scholartrace-restore `
  --archive backups/scholartrace-1.0.1-20260919.zip `
  --target-dir artifacts/m6-delivery-restored-20260904
$env:SCHOLARTRACE_DATA_DIR = "artifacts/m6-delivery-restored-20260904"
uv run uvicorn scholartrace.api.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

只有恢复输出 `verified=true`，且 live/ready、任务计数、事件回放和报告下载抽查通过后，才把
新目录设为长期配置。目标目录已存在、版本不匹配或任一 hash/完整性/表计数不一致时，恢复会
失败关闭；不要使用文件管理器直接把 WAL 主库复制回来。

恢复完成时，目标根目录会生成
`.scholartrace-restore-reconciliation-required` 标记。它表示备份可能遗漏备份之后
发生的外部调用，不是 SQLite 损坏。当前 journal 仍可读取历史结果、费用和未知占用，
但拒绝激活阶段授权及发出新操作；子目录内的 journal 同样受限。重复备份并恢复也不会
自动解除限制。`verified=true` 只证明恢复完整性，不表示可以恢复真实执行。

不要删除该标记或单独搬走数据库来恢复调用。当前没有自动解除入口；真实任务必须先
核对外部副作用与费用，再使用经过验证的显式恢复流程。新建独立数据目录不会恢复旧任务，
也不能作为重放旧任务的替代办法。备份中的 SQL 记录保持原值，恢复限制不通过清零预算、
删除授权或覆盖 unknown 记录实现。

### 5.3 Compose 备份、恢复与切换

容器镜像内安装了 `scholartrace-backup` 和 `scholartrace-restore`。备份前先 drain 并停止 API：

```powershell
docker compose stop scholartrace-api
New-Item -ItemType Directory -Force backups | Out-Null
docker compose run --rm --no-deps `
  -v "${PWD}/backups:/backup" `
  scholartrace-api scholartrace-backup `
  --data-dir /var/lib/scholartrace `
  --output /backup/scholartrace-1.0.1-20260919.zip
```

恢复到同一数据卷中的新子目录，再显式切换：

```powershell
docker compose run --rm --no-deps `
  -v "${PWD}/backups:/backup" `
  scholartrace-api scholartrace-restore `
  --archive /backup/scholartrace-1.0.1-20260919.zip `
  --target-dir /var/lib/scholartrace/restored-20260904
```

随后在未跟踪 `.env` 中设置
`SCHOLARTRACE_CONTAINER_DATA_DIR=/var/lib/scholartrace/restored-20260904`，执行 `docker compose up -d`
并完成 health、任务、SSE 和报告抽查。旧目录保留到观察期结束；不要在验证前删除。

### 5.4 升级与回滚

1. 记录当前发布包/提交和 `SCHOLARTRACE_DATA_DIR`，drain 队列并停止 API；
2. 用当前版本创建备份并完成一次隔离恢复校验；
3. 解压新发布包到新目录，执行 `uv sync --locked`/`npm ci` 或重新构建 Compose；
4. 先用恢复副本启动新版本，再检查 live、ready、SSE、任务和报告；
5. 当前 `1.0.1` 没有自动数据库迁移器；版本不一致时不得强行恢复；
6. 升级失败时停止新版本，切回旧发布包和旧数据目录，确认 ready 后再恢复服务。

确定性本地源代码发布包：

```powershell
uv run python scripts/run_m10_p4_release_drill.py
```

该脚本连续构建两次，只有 ZIP SHA-256 相同才写出
`artifacts/m10-p4/ScholarTrace-0.5.0-final.zip`，公开摘要写入
`artifacts/reports/m10_p4_release.json`。包包含锁文件但不捆绑 Docker 镜像或依赖缓存，首次
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

## 8. 当前契约检查与历史复现

默认检查已提交、由分支/tag 可达的 DocuMind ref，不读取未提交文件为契约，不默认访问网络：

~~~powershell
uv run python scripts/verify_m2_documind_compatibility.py --provider-repo ../DocuMind --provider-ref HEAD
~~~

显式 --base-url 才增加一次 readiness GET；这不证明部署 SHA 或在线全文链路。当前明确接受3.0.0及原2.x；不接受任意新版。3.0.0的组件ready在HTTP 200/503下均可表示纯检索可用，其他HTTP错误不得通过。离线Schema和合成HTTP流程通过不等于真实在线全文/模型验证通过。

历史2.1/2.2对象只用于档案重放：使用 --historical，并通过 --provider-repo 指向保有旧对象的私有归档。verify_upstreams.ps1 和 verify_m5_scholargraph.ps1 也要求显式 -Historical；它们不是当前版本验收入口。Git 历史清理后旧 SHA 不可用是正常边界，不应依赖 dangling 对象维持当前部署。

## 9. 运行目录、日志与发布范围

data/、logs/、artifacts/ 只跟踪 .gitkeep；程序使用时创建子目录。既有默认任务根 artifacts/m6-delivery 不自动迁移，新部署可显式选择 data/tasks。更改变量不会自动搬迁或恢复旧数据库。

应用默认输出日志到标准输出/错误流，不自动创建日志文件。需要留存时可在项目根执行：

~~~powershell
New-Item -ItemType Directory -Force logs | Out-Null
uv run uvicorn scholartrace.api.app:app --host 127.0.0.1 --port 8000 --no-access-log *> logs/api.log
~~~

不要在日志中记录请求查询串、token、全文或模型原始回答；该示例覆盖同名日志，长期运行需另配轮转。Compose 使用 docker compose logs 查看标准日志。

新运行默认报告在 artifacts/reports/；显式输出选项可能覆盖指定文件，请勿指向 evaluation/reports 中的冻结证据。备份文件应存放在数据根之外、Git 忽略的私有目录；下述根目录 backups 已加入忽略，不进入源码发布包。工具状态与私有记录不进入源码包；保留的三份 .gitkeep 只用于初始化目录，不含实际数据。

## 10. DocuMind 3.0.0 兼容与复现范围

- 入库函数必须显式接收已经核对的documind_version；当前smoke将readiness、绑定、检索审计及RunManifest串联为同一服务版本。旧2.2.0绑定不能直接冒充3.0.0，应在受控部署重新确认入库/活动索引，再通过显式CAS更新绑定，不能批量替换数据库字符串。
- 文档列表、multipart上传、活动索引状态与单文档retrieve路径仍在/api/v1下。源文件hash、返回document/index/source、页码、Chunk hash与顺序约束保持严格。
- 删除404按幂等完成、204按无正文完成；200必须返回同一document_key、status=deleted且cleanup_pending=false。待清理、错误身份或异常正文不会计为清理通过，不自动扩大删除范围。
- 使用隔离DocuMind测试实例、独立注册表/向量集合和新的工件目录验证；默认脚本按运行前列表保护既有文档，不提供并发所有权锁，不可对多人共享业务实例试跑。同名文件上传可能影响已有逻辑文档。
- 历史M6收集脚本prepare_m6_b3_b4_evidence.py仍是2.2.0固定实验入口，本轮只让它显式传入已确认版本，未把旧实验重新解释为3.0.0实验；新版联调使用run_m2_live_smoke.py或run_m10_p3_smoke.py。
- tests/fixtures/documind/v3_0_0_provider.json记录已提交上游的模型Schema与来源hash，不是在线测量。旧contracts/openapi、实验Fixture及evaluation报告保留原身份；只更新当前Consumer生成Schema。
- 上游示例的重复字符内容hash仅用于结构示意，不是有效证据。合成测试计算真实hash，Consumer不会为接入新版而关闭完整性检查。


## 交付数据库与本地回归

交付库当前 schema v4：v2增加计划/审批，v3增加整任务预算预留，v4增加已记录用量与
待对账状态。迁移使用可嵌套 SAVEPOINT，失败回滚全部新DDL和版本记录；v3的已结算
记录回填已知用量，旧任务/工件不删除。checkpoint、Runtime Ledger 与交付库仍物理分离。

升级前停止写入并备份全部关联 SQLite 文件；不要在线复制有活动WAL的单一数据库文件。
本版本的合成回归验证失败回滚与重试，不代替真实部署备份/还原演练。旧程序拒绝新schema，
需要回退时恢复升级前完整备份并使用匹配程序，不直接修改版本号或删除列。

real 任务留下的未确认预算不会自动归零或释放；保留本地用量和未知占用，先核对外部
结果，再接入明确的恢复流程。当前没有自动重放真实付费调用的授权或实现。

effect journal 另有独立 schema 1：保留原 effects/effect_budgets 布局，增加授权、
调用上下文与版本侧表；迁移失败整体回滚。旧数据不自动获得真实调用资格。
即便旧程序能读取原两表，它也不具备新授权检查，不能直接配合新 live 库降级运行；
降级必须停机，并同时使用匹配的旧源码和完整旧备份。任务库 schema 4 与 effect schema 1
不是同一个版本序列。

前端运行 `npm ci --ignore-scripts`、`npm test`、`npm run build`；测试使用与React主版本
匹配的 react-test-renderer 和 Node 内置 test runner，不调用模型。类型检查、hook单测与
构建不等同于浏览器视觉/E2E验收。后端运行 `uv run pytest`、`uv run ruff check src tests scripts`、
`uv run mypy src`、`uv lock --check`；通过 `scripts/export_schemas.py` 重新生成并核对契约漂移。
