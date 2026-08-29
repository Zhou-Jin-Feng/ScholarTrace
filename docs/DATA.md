# ScholarTrace 数据契约

> 核心契约版本：`1.0`；M1 搜索契约版本：`1.0`；M2 证据契约版本：`1.0`；M3 图状态版本：`1.0`
> 代码源：`src/scholartrace/contracts.py`、`src/scholartrace/search/models.py`、`src/scholartrace/evidence/models.py`、`src/scholartrace/workflow/models.py`
> 机器格式：`contracts/schemas/*.schema.json`

## 1. 设计原则

- Pydantic 模型是业务契约唯一代码源，JSON Schema 由脚本确定性导出；
- 所有顶层对象携带 `schema_version`，未知字段一律拒绝；
- LangGraph State 只保存控制状态和 ArtifactRef，不保存大段论文正文；
- Artifact 内容不可静默覆盖，内容变化产生新 ID 或版本；
- `task_id`、`thread_id`、`canonical_paper_id`、`index_id` 和 `artifact_id` 各有独立语义；
- 跨服务使用版本和内容哈希检测漂移，不依赖分支名或 `latest`。

## 2. 核心对象

| 对象 | 关键字段 | 约束 |
|---|---|---|
| ResearchPlan | task_id、question、subquestions、criteria、budget、status | 至少一个子问题、纳入和排除条件；执行前状态必须 approved |
| Paper | canonical_paper_id、title、authors、year、external IDs、sources | 至少一个 DOI/arXiv/OpenAlex/S2 ID；保留来源记录和 version_of |
| DocuMindBinding | canonical_paper_id、document_key、index_id、source_sha256 | 三个哈希均为 64 位小写 SHA-256；绑定 active index |
| Evidence | paper ID、quote、level、定位、hash | fulltext 必须包含 DocuMind provenance；摘要不能标记为全文 |
| Claim | text、type、evidence_ids、origin、importance | 关键 Claim 在最终报告前必须有 Verification |
| Verification | claim_id、status、checked IDs、action、verifier | 状态限定为 supported/partially_supported/unsupported/conflicted |
| Budget | limits、usage | 限制和使用量分离；费用、Token、时间不可为负数 |
| ModelRoutingPolicy | profiles、routes、paid_routes_enabled | Profile 必须存在；付费路由需要 enabled API Profile；禁止自动升价 |
| ModelUsageRecord | node、provider、model、Token、重试、时间、GPU、成本 | 原始币种和 CNY 同时记录；不保存原始模型回答 |
| ArtifactRef | artifact_id、type、content_sha256、storage_uri | State 只引用该对象，不内嵌完整 Artifact |
| RunManifest | task/run、cutoff、service baselines、model、hash、budget | 公平对照和复现的最小快照 |

M1 搜索对象：

| 对象 | 关键字段 | 约束 |
|---|---|---|
| SearchRequest | query、max_results、year range | 查询非空；结果数和年份有界；起始年不得晚于结束年 |
| SourcePolicy | 请求预算、间隔、超时、重试、响应大小 | 每个来源独立；网络预算必须覆盖最大尝试次数 |
| SourceRequestRecord | 公开请求、时间、缓存、状态、错误、hash、成本 | 不保存 Key；空结果和失败分离；Provider 报告成本不等于账单成本 |
| PaperCandidate | 来源 ID、论文标识、标题、作者、摘要、原始记录 hash | 来源记录进入统一契约前校验类型、长度和访问等级 |
| MergeDecision | merge/version/separate、左右 ID、原因、review flag | 自动版本推断必须可审计；不确定关系进入复核 |
| SearchSnapshot | 来源结果、排序论文、合并决策、候选 hash、outcome | 候选 hash 排除采集时间，固定内容可离线重放 |
| BaselineArtifact | B0/B1、生成器、输入 hash、引用 ID、内容 hash、限制 | 本地模型只能引用实际提供的候选 ID；只声明摘要级证据 |

M2 证据对象：

| 对象 | 关键字段 | 约束 |
|---|---|---|
| DocuMindRetrieveRequest | query、document_key、expected_index_id、top_k | 单文档、Schema 1.0、Dense-only、未知字段拒绝 |
| RetrievalChunk | chunk_id、content/hash、source、page、distance、rank | 内容 hash 必须匹配；ID 唯一；rank 从 1 连续 |
| RetrievalAudit | paper/document/index/source、query hash、attempts、status | 不保存 query 或 Chunk 正文；空结果与失败分离 |
| PaperCard | paper、question、summary、contributions、limitations、ID 引用 | 输入、模型 Profile 和生成时间可审计 |
| PaperAnalysisBundle | PaperCard、Claim、Evidence、RetrievalAudit | 只允许一篇 Paper；Claim 不得引用 bundle 外 Evidence |
| EvidenceReportArtifact | 3-5 个分析 bundle、Markdown、content hash | Paper/Claim/Evidence ID 全局唯一；Markdown hash 必须匹配 |

`PaperAnalysisDraft` 是模型内部临时对象，不是最终 Evidence。模型只返回 `chunk-1..6` 与 `quote-1..6`；Consumer 在单论文白名单内解析为真实 `chunk_id` 和原始 Chunk 文本。长哈希和逐字 quote 不依赖模型复制。

M3 控制对象：

| 对象 | 关键字段 | 约束 |
|---|---|---|
| ApprovalDecision | action、modified_plan、reason | modify 必须给出完整 ResearchPlan；reject 不执行 Search/Worker |
| SearchRoundArtifact | round、query、adjustment、candidate/new IDs、coverage、ratio | 完整轮次只进入 Artifact Store；State 保存引用 |
| PaperWorkerArtifact | paper ID、status、output/public reason | 稳定 task+paper Artifact ID；重复投递不得重复执行 |
| PersistedEvent | event/sequence、task、node、kind、artifact、payload | 单调 sequence；稳定 key 去重；安全 payload |
| ResearchState | task/thread、plan/search/worker refs、paper IDs、round、status | 不含完整计划、检索结果、Worker output、Evidence 或报告 |

M0 默认 Budget 上限：输入 160,000 Token、输出 40,000 Token、总计 200,000 Token、60 次模型调用、16 次 API/工具调用、1,800 秒、10 CNY。达到任一限制即停止新增调用；10 CNY 是硬上限，不是预计花费。

## 3. ID 规则

| ID | 生成和用途 |
|---|---|
| task_id | ScholarTrace 业务任务 ID，例 `task:dev-adaptive-rag` |
| thread_id | LangGraph Checkpointer 配置；不得与 task_id 互换 |
| canonical_paper_id | 优先 DOI，其次 arXiv、OpenAlex、S2，最后才是保守标题键 |
| document_key | DocuMind 内容寻址文档 ID，64 位小写 SHA-256 |
| index_id | DocuMind active index ID，64 位小写 SHA-256 |
| artifact_id | 不可变业务产物 ID，包含类型和可读范围 |
| evidence_id | 一条定位证据 ID，不因报告重排而改变 |
| claim_id | 一条原子 Claim ID；措辞或含义变化时产生新版本 |
| idempotency_key | 后续写入层由 task、node、paper、round、input hash 组合 |

## 4. Paper 归一化

身份优先级：

1. 规范化 DOI；
2. arXiv ID；
3. OpenAlex Work ID；
4. Semantic Scholar Paper ID；
5. 规范化标题、第一作者和年份的保守候选匹配。

精确 DOI、arXiv ID 和来源 ID 用于确定性合并；无强标识时，只有规范化标题、第一作者和年份同时一致才允许保守 fallback 合并。不同独立 DOI 不因标题相同而合并。

预印本和独立 DOI 出版记录先保留为两个 Paper。标题、作者和年份高度一致时可以建立 `version_of` 候选关系，但自动推断必须设置 `review_required=true`。来源冲突不得由 LLM 猜测解决，必须进入人工复核队列。

## 5. Claim-Evidence 不变量

1. 每个关键 Claim 至少引用一个存在的 Evidence；
2. Evidence 引用的 Paper 必须存在；
3. fulltext Evidence 的 Paper、document_key、index_id、chunk_id、chunk_content_sha256 和 source_sha256 必须一致；
4. 数值 Claim 同时保留指标、数据集和实验条件；
5. unsupported Claim 不进入无标记结论；
6. conflicted Claim 同时展示支持和反证；
7. abstract Evidence 不得表述成已核对全文；
8. ScholarGraph 只产生 abstract 辅助证据或检索线索；
9. 同一输入的幂等重试不得产生不同 Artifact ID；
10. 内容哈希变化必须生成新 Artifact 或新版本。
11. Evidence `content_sha256` 必须等于 quote 的 UTF-8 SHA-256；模型短引用只能解析到本次单论文输入。

## 6. Artifact Store 边界

| 存储内容 | 是否进入 LangGraph State | 是否可被日志完整记录 |
|---|---:|---:|
| task_id、状态、计数、ArtifactRef | 是 | 仅安全摘要 |
| Paper 元数据 | 否，保存引用 | 可记录 ID，不记录完整摘要 |
| 原始 Chunk 和 Evidence quote | 否 | 否 |
| Claim/Verification | 否，保存引用 | 仅 ID、状态和短摘要 |
| 报告 | 否，保存引用 | 否 |
| RunManifest | 否，保存引用 | 可记录非敏感字段 |

## 7. 版本兼容

- 相同主版本 Schema 允许新增可选字段，但 Consumer 必须显式升级后才接受；
- 删除、重命名、类型变化或语义变化需要新的 Schema 主版本；
- 上游返回未知字段时当前 Consumer fail closed，避免静默使用错误证据；
- RunManifest 保存实际服务版本和契约版本，不能只保存“成功”。

## 8. 示例与生成

```powershell
uv run python scripts/export_schemas.py
uv run pytest tests/test_contract_models.py
```

`contracts/examples/m0_bundle.json` 覆盖核心对象；`contracts/schemas/` 同时包含 M1 搜索与 M2 证据对象的独立 Schema。M3 TypedDict 是 LangGraph 内部控制契约，不作为跨服务 JSON Schema。M3 的脱敏 Fixture 指标位于 `evaluation/reports/m3_workflow_fixture_smoke.json`；Checkpoint 和完整业务 Artifact 不进入 Git。

## 学术来源与访问约束

以下为来源接入规范，来源是否已经接入及其实际验证范围仍以本版本基线为准。

- arXiv 连续请求至少保守间隔 3 秒，并缓存同日查询；大规模元数据获取优先考虑 OAI-PMH，而不是高并发逐条搜索。
- OpenAlex 请求使用必要字段裁剪、批量 ID 或 cursor；分页以 `per_page=100` 为基线，记录可用的 `X-RateLimit-*` 与 `meta.cost_usd`，各来源独立限流。
- Crossref 采用 `mailto` 的 polite pool、缓存和退避；可选 Semantic Scholar 不依赖共享匿名池的吞吐，Key 初始通常 1 RPS，实际执行遵循服务端限制。
- 论文生命周期记录来源、时间、状态、失败原因和重试；全文不可合法获取时保留摘要、元数据与官方链接，不伪造全文分析或重新分发受限原文。
- 上游字段与论文内容均为不可信数据，先进行结构校验与展示转义；来源 URL、访问时间和文档哈希用于追溯，不让文本内容改变工具权限。
