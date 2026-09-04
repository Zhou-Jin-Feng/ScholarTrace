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

## 3. DocuMind `2.1.0+` 已实现接口

机器契约：`contracts/openapi/documind-v1.openapi.json`

最低兼容版本为 `2.1.0/32c5eb8`；`2.2.0/212f60a` 保持相同请求/响应字段，并增加 retrieval readiness、有界执行与错误码。兼容检查只读取冻结 Git 对象，不将 DocuMind 当前未提交工作树视为协议。

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
| service_version | 支持冻结的 `2.1.0` 与 `2.2.0`；必须和当前 Paper 绑定完全一致 |
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

### 3.4 M2 在线状态边界

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

- 创建和审批使用 `Idempotency-Key`（当前为交付 API 的确定性演示装配）；
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
