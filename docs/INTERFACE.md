# ScholarTrace 接口契约

> 状态：M0 冻结草案  
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

## 3. DocuMind `2.1.0` 已实现接口

机器契约：`contracts/openapi/documind-v1.openapi.json`

### 3.1 健康检查

```text
GET /api/v1/health/live
GET /api/v1/health/ready
```

`live` 只说明进程存活；`ready` 检查实际依赖。ScholarTrace 启动时先校验版本和 readiness，运行中对暂时故障执行预算内重试或降级。

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
| service_version | M0 基线 `2.1.0`，兼容范围由 Consumer 显式升级 |
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

## 4. ScholarGraph 待实现接口

机器契约：`contracts/openapi/scholargraph-v1.openapi.json`

ScholarGraph commit `953e40b` 当前只有 Python `src.demo_service.run_query` 和 Streamlit Demo。以下 HTTP 端点是 M5 Provider 实现目标，不得在 M0/M1/M2 中写成现有能力：

```text
GET  /api/v1/health/live
GET  /api/v1/health/ready
GET  /api/v1/capabilities
POST /api/v1/query
```

### 4.1 能力清单

必须声明：

- `corpus_id = rag-openalex-2020-2025-198-v1`；
- 主题仅为 retrieval-augmented generation；
- 年份 2020-2025、198 篇；
- 证据等级 `abstract`；
- 方法 basic/local/global/drift；
- 默认方法 `basic`。

### 4.2 查询规则

- Capability Router 在调用前检查主题、年份、证据等级和预算；
- Basic 是默认方法；Local/Global 只用于预登记问题；
- DRIFT 需要显式长任务预算，默认交互路径禁止；
- `source_refs` 只有在能确定性映射正式语料时填写；
- 无可验证 source_refs 的 answer 只能作为分析或检索线索；
- 超时或失败回退 B3，不把部分输出当成成功答案。

## 5. ScholarTrace Research Task API 计划

这些端点在 M3/M6 实现，本阶段只冻结职责：

```text
POST /api/v1/research/tasks
GET  /api/v1/research/tasks/{task_id}
POST /api/v1/research/tasks/{task_id}/approve
GET  /api/v1/research/tasks/{task_id}/events
GET  /api/v1/research/tasks/{task_id}/artifacts
GET  /api/v1/research/tasks/{task_id}/report
```

- 创建和审批使用 Idempotency-Key；
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

## 上游隔离与观测约束

以下为跨服务规范；接口是否已交付，以本版本各服务的实现状态及机器契约为准。

- DocuMind 与 ScholarGraph 的基础 URL 来自配置 Allowlist，不能由模型或论文内容指定；三个服务分别维护版本和 Provider/Consumer 契约测试，不共享内部实现对象。
- ScholarGraph 的只读封装不得允许任意工作区路径、容器参数、环境变量或索引写入；超时或取消后的查询资源需要清理，正式索引保持只读。
- 业务空结果、能力不支持、客户端错误、版本冲突、限流、超时和服务故障分开记录；内部降级不能因进程退出码为零而伪装为完整成功。
- 请求与响应在进入模型上下文前进行 Schema、大小和枚举校验；服务 Key 不进入 State、Artifact、Prompt、SSE 或日志，私密查询和原文不进入 URL 或公开诊断。
- 跨服务保留 `X-Request-ID` 与可用的 Trace Context，记录方法、状态、耗时、重试和语料身份；Token 仅为可见下界时必须标明，不把它当成完整用量或实际账单。
