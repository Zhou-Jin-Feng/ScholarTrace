# ScholarTrace 接口契约

> 状态：M10-P3 受限公开 arXiv acquisition 与 DocuMind 闭环已实现；跨进程、多用户、租户隔离、出版社来源和公网安全仍不在范围内
> 内部协议：版本化 HTTP/JSON + OpenAPI 3.1  
> 契约目录：`contracts/openapi/`

## 1. 服务边界

| 服务 | 拥有 | 不拥有 |
|---|---|---|
| ScholarTrace | 研究计划、论文身份、Agent 路由、预算、证据关系、核验、报告 | PDF 解析、向量索引、GraphRAG 语义索引 |
| DocuMind | 文档生命周期、解析、Chunk、Embedding、Milvus、active index、纯检索 | 跨来源论文身份、研究计划、最终 Claim |
| ScholarGraph | 固定 RAG 摘要语料、语义索引、四种查询模式、查询诊断 | 任意领域检索、全文 Evidence、研究工作流决策 |

## 2. 通用约束

- `Content-Type: application/json`；
- 每个响应携带 Schema 和服务版本，或由已冻结端点语义提供；
- 请求关联使用 `X-Request-ID`，日志不得记录 API Key、全文或原始 Prompt；
- 网络超时、限流、依赖故障和业务空结果使用不同状态；
- Consumer 不兼容时 fail fast，禁止猜测字段或回退 DocuMind `/chat/stream`；
- 只读检索端点不需要 Idempotency-Key，未来写接口必须使用幂等键。

## 3. DocuMind 2.x 与 3.0.0 接口

机器契约：`contracts/openapi/documind-v1.openapi.json`

最低兼容版本为 `2.1.0/32c5eb8`；`2.2.0/212f60a` 保持相同请求/响应字段，并增加 retrieval readiness、有界执行与错误码。历史报告只证明当时冻结对象。当前检查默认解析可达 ref 的已提交契约，不将未提交工作树视为协议；旧对象重放必须显式使用 --historical。当前版本门禁保留2.x（minor ≥ 1）并精确增加3.0.0；不泛化到任意3.x。Schema仍为1.0，服务主版本与Schema版本独立。新增3.0.0快照在tests/fixtures/documind/v3_0_0_provider.json；历史OpenAPI快照不替换为新实测。

### 3.1 健康检查

```text
GET /api/v1/health/live
GET /api/v1/health/ready
```

`live` 只说明进程存活；`ready` 检查实际依赖。ScholarTrace 启动时先校验版本和 readiness，运行中对暂时故障执行预算内重试或降级。DocuMind 2.2.0 即使因生成模型不可用返回 HTTP 503，只要 `components.retrieval == "ready"`，纯检索仍可用；组件缺失或非 ready 时 fail closed。

### 3.2 单文档证据检索

```http
POST /api/v1/retrieve
X-Request-ID: m0-contract-0001
Content-Type: application/json

{
  "schema_version": "1.0",
  "query": "Which signal triggers corrective retrieval?",
  "document_key": "<64 lowercase hex>",
  "expected_index_id": "<64 lowercase hex>",
  "top_k": 8,
  "retrieval_mode": "dense",
  "distance_threshold": null
}
```

关键响应字段：

| 字段 | 约束 |
|---|---|
| schema_version | 固定 `1.0` |
| service_version | 保留2.x（minor ≥ 1），精确增加3.0.0；必须和当前Paper绑定完全一致 |
| retrieval_version | 固定 `dense-v1` |
| document_key/index_id/source_sha256 | 64 位小写 SHA-256 |
| chunks | 0-20 条；空数组是成功业务结果 |
| chunk_id/content_sha256 | 64 位小写 SHA-256 |
| page_number | 1 起始或 null，不伪造页码 |
| distance/rank | Dense L2 距离和 1 起始排名 |

### 3.3 稳定错误

| HTTP | code | Consumer 行为 |
|---:|---|---|
| 404 | document_not_found | 标记论文未入库，不能重试检索 |
| 409 | stale_document_index | 重新读取绑定；禁止静默使用新 index |
| 409 | document_operation_in_progress | 短暂退避，受节点预算限制 |
| 409 | document_index_unavailable | 标记无 active index，进入入库/人工检查 |
| 413 | request_too_large | 修正请求，不重试原请求 |
| 422 | validation_error | 编程错误，fail fast |
| 503 | retrieval_service_unavailable | 有界重试后降级或失败 |
| 503 | retrieval_capacity_exceeded | 相同论文范围内有界退避；不得扩大 document scope |
| 503 | retrieval_timeout | 相同请求最多执行 Consumer 预算内重试 |

Client 还必须校验返回的 document、index、source、服务版本、Chunk 内容 hash、唯一 ID 和连续 rank。`stale_document_index` 不自动刷新绑定或重试；调用方必须显式读取新 active index 并执行 CAS。

### 3.5 M10-P3 全文 acquisition

内部入口为 `acquire_pdf(Paper, output_dir)`，不是公开 HTTP 端点。输入 Paper 必须含唯一、
带版本号的 arXiv `abs` 来源；当前只生成 `https://arxiv.org/pdf/<version>.pdf`，允许最终
域名为 `arxiv.org` 或 `export.arxiv.org`。请求逐跳关闭自动重定向并限制最多 3 跳，拒绝
HTTP、非 443 端口、用户信息、跨域跳转、非 PDF Content-Type、超 30 MiB 响应和缺少 `%PDF-`
魔数的内容。

成功返回 `PdfAcquisition(path, size_bytes, sha256, reused)`。调用 `ingest_papers()` 时可传入
`expected_source_sha256`；上传前后文件 hash 和 DocuMind 返回的 `source_sha256` 必须一致，
否则返回 `FullTextAcquisitionError` 并停止 Evidence 流程。清理只接收本次新建的 document key，
DELETE 404 为幂等成功，其他失败汇总为 `DocumentCleanupError`。该接口不提供断点续传，
不绕过付费墙/访问控制，也不把摘要转换成全文 Evidence。

### 3.4 M2 历史在线状态边界

`evaluation/reports/m2_documind_compatibility.json` 证明 2.1.0 与 2.2.0 的冻结 Provider Schema 可被 Consumer 接受。最终阶段复审时本机 `127.0.0.1:8001` 运行冻结部署 `2.2.0/212f60a`，OpenAPI 包含 `/api/v1/retrieve`，预热后的 readiness 为 HTTP 200 且 `components.retrieval=ready`，因此 `online_acceptance_passed=true`。`evaluation/reports/m2_live_documind_smoke.json` 另行记录三篇公开全文的真实 upload/status/retrieve 与 Evidence 闭环；Fixture 指标和在线指标保持分离。

## 4. ScholarGraph `1.2.0` 已实现接口

机器契约：`contracts/openapi/scholargraph-v1.openapi.json`

冻结 Provider 为 ScholarGraph commit `3aa5e2a`、服务 `1.2.0`、GraphRAG `3.1.2`。M5 Consumer 与 Provider 的 OpenAPI 规范化后完全一致：

```text
GET  /api/v1/health/live
GET  /api/v1/health/ready
GET  /api/v1/capabilities
GET  /api/v1/metrics
POST /api/v1/query
```

### 4.1 能力清单

必须声明：

- `corpus_id = openalex-rag-abstracts-2020-2025-v1`；
- 主题仅为 retrieval-augmented generation；
- 年份 2020-2025、198 篇英文摘要；
- 证据等级 `abstract`；
- 方法 basic/local/global/drift；
- 默认方法 `basic`。

### 4.2 查询规则

- Capability Router 在调用前检查主题、年份、证据等级和预算；
- Basic 是默认方法；Local 只用于 `entity_neighborhood`；
- Global/DRIFT 在 M5 保持禁用，显式方法请求也会被路由门禁跳过；
- Capability Router 还检查全文需求、索引写入、API 调用预算和剩余墙钟时间；
- M5 Consumer 暂不接受非空 `source_refs`，避免把未经独立映射的引用当成 Evidence；
- 无可验证 source refs 的 answer 只能作为摘要级辅助上下文或检索线索；
- 轻量 GET 探针最多尝试两次；昂贵查询只尝试一次，Provider timeout 外加 15 秒响应宽限；
- 超时、失败、503、错误信封或契约漂移全部回退 B3，不把部分输出当成成功答案。

兼容性报告 `evaluation/reports/m5_scholargraph_contract_compatibility.json` 覆盖五个操作，Consumer 与 Provider 精确匹配。真实 Basic 联调报告 `evaluation/reports/m5_scholargraph_live_smoke.json` 只保存脱敏指标，不保存问题、答案、Prompt 或原始诊断。

## 5. ScholarTrace Research Task API

M6 已装配可运行的 Research Task API：

```text
GET  /api/v1/health/live
GET  /api/v1/health/ready
GET  /api/v1/evaluation/m6
POST /api/v1/research/tasks
GET  /api/v1/research/tasks/{task_id}
POST /api/v1/research/tasks/{task_id}/approve
POST /api/v1/research/tasks/{task_id}/cancel
GET  /api/v1/research/tasks/{task_id}/events
GET  /api/v1/research/tasks/{task_id}/artifacts
GET  /api/v1/research/tasks/{task_id}/report?format=json|markdown|html|pdf
```

创建接口支持 `Idempotency-Key`；相同 Key 与相同请求体返回原任务，Key 复用但请求体变化返回 409。任务先进入 `waiting_approval`，审批后进入进程内有界队列，状态依次可见为 `queued`、`running`，最终为 `completed`、`degraded`、`cancelled` 或 `failed`。队列满返回 HTTP 429 和 `Retry-After`，关闭期间不再接受新任务。对 `queued` 或 `running` 任务调用 `cancel` 会发出协作式取消请求，并在阶段边界生成 `task_cancelled` 与终态导出。

M3 的只读事件补发 Router 继续提供：

```text
GET /api/v1/research/tasks/{task_id}/events
Last-Event-ID: event:<sequence>
Accept: text/event-stream
```

事件先以稳定 key 写入 Runtime Ledger，再按单调 `event:<sequence>` 以 SSE 返回。无效 `Last-Event-ID` 返回 HTTP 400；响应禁止代理缓冲和缓存。M10-P0/P1 API 既支持有限回放，也支持显式 `follow=true` 持续 tail、heartbeat、断线续传和终态关闭；P1 的队列、取消和停机状态通过任务摘要与 SSE 事件可见。

### 5.1 访问控制与部署边界

- `SCHOLARTRACE_DEPLOYMENT_MODE` 只能是 `loopback` 或 `trusted_private`，默认是 `loopback`；
- `loopback` 只提供绑定地址的单机边界；若配置 `SCHOLARTRACE_AUTH_TOKEN`，任务 API 同样要求 Bearer token；
- `trusted_private` 必须配置至少 16 个不含空白字符的 `SCHOLARTRACE_AUTH_TOKEN`，否则应用启动失败；
- `POST/GET /research/tasks`、任务状态、审批、取消、事件、工件和报告均需要认证；health 与静态前端保持可探测；
- 普通 HTTP 请求使用 `Authorization: Bearer <token>`；为兼容原生 `EventSource` 与浏览器下载，仅事件和报告 `GET` 接受 `access_token` 查询参数；其他方法和路径不会从查询参数读取 token；
- 查询参数 token 是兼容性折衷，可能被客户端或代理记录。部署时使用 TLS、关闭包含查询串的访问日志，并禁止把共享 token 当成账号、租户或细粒度 Artifact 授权。

认证失败统一返回 `401`、`WWW-Authenticate: Bearer` 和非敏感错误消息；应用结构化日志只记录方法、路径、状态码和请求 ID，不记录 Authorization 或查询串。

- 创建支持 `Idempotency-Key`；审批使用任务状态与计划版本门禁防止重复执行，已处理决策重试返回409，不承诺HTTP响应重放；
- 事件先持久化再通过 SSE 发送，支持 `Last-Event-ID`；
- 长研究问题放 POST body，不放查询参数；
- Artifact 响应默认返回元数据和安全摘要，正文使用授权下载端点。

## 6. 契约变更流程

1. Consumer 先提交失败的契约测试和新草案；
2. Provider 在自己的仓库实现最小能力；
3. Provider/Consumer 均通过契约测试；
4. Provider 发布版本并记录 commit；
5. ScholarTrace 更新 RunManifest 基线；
6. 不兼容变更必须新建主版本端点或 Schema。

## 7. M4 内部 Provider/Consumer 边界

- `OpenAlexCitationProvider` 接受完整 `Paper` 或轻量 `CitationSeed`，只读取显式 `referenced_works`，先取 seed work，再对受限目标 ID 批量取元数据；API Key 只进入私有请求参数，不进入请求审计或缓存键；
- Provider 返回 `CitationExpansionResult`，区分成功空引用、缺失引用列表、目标元数据缺失和网络失败；Semantic Scholar 尚未作为必选 Provider；
- `CitationLifecycleGate` 消费归一化后的 `Paper`，不接受原始学术 API 记录；acquire 与 DocuMind ingest 继续由拥有合法访问和文档生命周期的调用层执行；
- `EvidenceValidator` 消费 Paper、Evidence、Binding、RetrievalChunk、CitationGraph 和 Lifecycle 快照，不调用网络或模型；
- `VerifierRunner` 只消费 deterministic validation 通过的 Claim；生产模型必须匹配启用的 `critical_verifier` Profile；
- `M4ReliabilityPipeline` 输出 Validation、Verification、ReportGate 和至多一个 FollowUpRequest，尚未暴露新的 HTTP 端点。

## 上游隔离与观测约束

以下为跨服务规范；接口是否已交付，以本版本各服务的实现状态及机器契约为准。

- DocuMind 与 ScholarGraph 的基础 URL 来自配置 Allowlist，不能由模型或论文内容指定；三个服务分别维护版本和 Provider/Consumer 契约测试，不共享内部实现对象。
- ScholarGraph 的只读封装不得允许任意工作区路径、容器参数、环境变量或索引写入；超时或取消后的查询资源需要清理，正式索引保持只读。
- 业务空结果、能力不支持、客户端错误、版本冲突、限流、超时和服务故障分开记录；内部降级不能因进程退出码为零而伪装为完整成功。
- 请求与响应在进入模型上下文前进行 Schema、大小和枚举校验；服务 Key 不进入 State、Artifact、Prompt、SSE 或日志，私密查询和原文不进入 URL 或公开诊断。
- 跨服务保留 `X-Request-ID` 与可用的 Trace Context，记录方法、状态、耗时、重试和语料身份；Token 仅为可见下界时必须标明，不把它当成完整用量或实际账单。

## 当前3.0.0兼容补充

已逐项核对DocuMind 3.0.0提交3a9bf0c9980f4109482f078ee697c527a99e44ae的health、documents、retrieve路由与响应模型。离线消费者回归不启动上游服务；真实在线链路应另行验证。

3.0.0 readiness必须提供components.retrieval=ready，允许正常200或依赖降级503，不允许其他HTTP错误伪装成功；缺失组件不回退整体ready字段。原2.x兼容仍保留。ingest_papers必须显式传documind_version，来自调用方已确认的readiness；不再硬编码2.2.0。请求方法、multipart字段file、活动索引与源文件hash检查未放宽。

DELETE200必须具备匹配身份、deleted状态及无cleanup_pending；204和404保留其完成语义。异常清理聚合失败、尽量处理其余本次文档，不删除其他范围。


## 8. 交付计划、预算与事件契约

当前交付服务区分确定性 Demo 与尚未装配完成的 real 流水线。配置 URL 或启用开关
不代表通过健康探测，更不代表付费模型已获批准。

### 8.1 两道计划门禁

1. `POST /research/tasks/{task_id}/plan/estimate` 获取生成计划的估算。
2. 用户确认后，`POST /research/tasks/{task_id}/plan/acknowledge-cost`，请求必须含
   非负且有限的 `acknowledged_max_cny`，不能以缺字段代替零元确认。
3. `POST /research/tasks/{task_id}/plan/generate` 不接确认请求体；当前可运行的生成器
   仅为零模型调用的 fixture。真实 Coordinator 未装配时明确拒绝。
4. `GET /research/tasks/{task_id}/plan` 或 `/plan/versions` 查看待审版本。
5. `POST /research/tasks/{task_id}/approve` 的决策必须绑定当前 `plan_version` 和
   `plan_digest`。`approve` 才尝试排队，`reject` 终止任务，`modify` 只保存新待审版本。

`modify` 使用完整 `modified_plan`，只含 `sub_questions`、`source_scope`、`exclusions`、
`budget_plan`；内容与 `ResearchPlanView` 的可编辑字段一致。不能发送 `modifications`，
也不能把内部工作流的 M3 `ResearchPlan` 当作交付计划。原先未正式发布的交付修改请求
误用了 M3 类型；本增量纠正该契约，M3 自身的契约不变。增加预算上限不能通过 modify
绕过复核；本接口只允许保持或降低原计划的金额、调用次数与时长。

队列满或关闭时，任务、决策与预算事务回滚，可再次审批。重复已受理的审批返回409，
不会重复入队。无持久化计划的旧 Demo 客户端仍可 approve；real 无计划不可旁路。
生成、修改和取消在单进程内串行处理。进程内队列不是持久化消息代理，也不承诺跨进程
或外部模型副作用恰好一次；崩溃任务恢复必须在 real 流水线启用前另行完成。

### 8.2 实际预算与任务列表

`GET /research/tasks/{task_id}/budget` 返回 `BudgetReport`：

- `reservation` 为 null 或预留对象；预留不是消费，也不是提供方账单。
- `reservation.reserved` 是批准上限；`settled` 为 null 表示未结算，不能显示成已消费0元。
- `recorded_usage` 保留本地已知计量；`reconciliation_required=true` 表示外部结果待核对，
  即使本地计量为0，也不等于实际费用为0。未知预留继续占用额度。
- `measured_usage` 来自 Runtime Ledger；Demo 已知计量可自动结算，real 中断/终态没有
  完整外部结果证明时不自动释放预留。
- `committed_cny_all_tasks` 是所有未结算任务的保守占用，不是累计历史花费。
- 金额必须有限且非负，次数、时长必须为非负整数；冲突结算被拒绝。

服务可显式注入 `reservation_capacity_cny` 作为全局并发预留容量；未配置时不杜撰全局
用户预算。每任务限额来自所审计划，启动执行前检查已有用量。未来真实并发调用还需
原子的单调用预留/结算、提供方幂等及结果未知恢复，不能把此启动检查当作完整付费风控。

列表 `GET /research/tasks` 的 items 使用实际 `TaskSummary` 字段，并返回
`next_cursor`、`total_known`。未实现的 paper/claim 计数和核验统计不填假0；新工作台
接入前不承诺 claims/evidence 端点存在。

### 8.3 SSE 与错误

后端使用具名 `event: <kind>`，不是只发送默认 message。新 hook 显式订阅事件、按
sequence 去重、使用 `last_event_id=event:<sequence>` 续传。任务失败/拒绝/取消事件
之后仍可有预算与导出事件；仅 `exports_ready`、`task_terminal_no_exports` 或
`stream_end` 的 terminal 控制消息结束流。有限退避耗尽后需用户重试；任务切换会清空
旧游标与事件，旧连接回调不更新新任务。

HTTP 错误兼容 `detail` 字符串、`detail: {code, detail}` 和422字段错误数组。
不得把嵌套错误对象转换为 `[object Object]`。未发布的骨架 hook 已单独测试，旧 main
页面的全面装配与模块拆分仍是后续界面工作，不据此宣称整页已完成。

### 8.4 依赖状态

`GET /health/dependencies` 中 URL/配置存在仅显示 `configured=true, state=unknown`；
缺配置为 unavailable，付费开关关闭或缺凭据为 disabled。该路由不调用外部服务、
不加载模型，也不回显地址或凭据。未探活的依赖不可显示 ready。队列不接单时 API
显示 draining，Demo unavailable。真正的有界健康探测与 real 流水线装配分开验收。
