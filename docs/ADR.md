# ScholarTrace 架构决策记录

> M0 冻结日期：2026-08-29

## ADR-001：DocuMind 作为独立证据服务

- 状态：Accepted
- 决策：ScholarTrace 通过 HTTP/OpenAPI 调用 DocuMind，不复制 Retriever、Milvus 或解析器。
- 当前基线：最低 DocuMind `2.1.0/32c5eb8`；已验证兼容 `2.2.0/212f60a`，retrieve Schema `1.0`。
- 原因：DocuMind 已拥有文档生命周期、active index 和可追溯 Chunk。
- 代价：ScholarTrace 必须维护 Paper 到 DocuMindBinding 的唯一版本映射。

## ADR-002：ScholarGraph 是默认关闭的能力受限工具

- 状态：Accepted
- 决策：仅对固定 RAG 摘要语料范围内的问题开放 B4 实验。
- 当前基线：ScholarGraph `1.0.0`，commit `953e40b`，GraphRAG `3.1.2`。
- 原因：正式语料只有 198 篇 2020-2025 RAG 摘要；Basic 当前评测最好，DRIFT 延迟高。
- 代价：需要 Capability Router 和 eligible/boundary 两套评测。

## ADR-003：P0 使用 HTTP/OpenAPI，不先使用 MCP 或 A2A

- 状态：Accepted
- 决策：服务间协议为版本化 HTTP/JSON + OpenAPI 3.1。
- 原因：DocuMind 和 ScholarGraph 是工具服务，不拥有自主目标；HTTP 最贴近当前 FastAPI/服务层。
- 代价：未来需要外部工具互操作时再维护 MCP Adapter。

## ADR-004：Python 3.11 与 uv 锁定环境

- 状态：Accepted
- 决策：ScholarTrace 使用 Python `>=3.11,<3.12`，由 `.python-version` 和 `uv.lock` 固定。
- 原因：本机默认 Python 3.14，不应把未经上游验证的新解释器引入跨仓兼容面。
- 代价：开发命令必须使用 `uv run`，不能依赖系统 `python`。

## ADR-005：轻量 State 与 Artifact Store 分离

- 状态：Accepted
- 决策：LangGraph State 只保存控制状态、ID、引用和计数。
- 原因：避免长期 Checkpoint 重复存储大段 Evidence 和报告。
- 代价：节点需要通过 Repository/Artifact Service 解引用。

## ADR-006：Evidence Validator 与语义 Verifier 分离

- 状态：Accepted
- 决策：确定性校验先检查 ID、哈希、版本、页码、Chunk 和数值，再由独立 Verifier 判断语义支持。
- 原因：结构错误不应交给概率模型猜测，Analysis 也不应自证其 Claim。
- 代价：增加一个阶段和数据对象，但错误定位更清楚。

## ADR-007：引用网络使用学术 API + NetworkX

- 状态：Accepted
- 决策：引用边来自 OpenAlex/可选 Semantic Scholar，MVP 使用 NetworkX。
- 原因：显式引用图不同于 GraphRAG 语义图；小规模分析不需要 Neo4j。
- 代价：外部 API 缺边必须明确降级，不能由语义边补造。

## ADR-008：混合模型路由必须受评测和预算门禁

- 状态：Accepted
- 决策：确定性任务不用 LLM；高频受限任务使用冻结的本地 `qwen3:8b`；关键规划、核验和合成通过 `api-strong` Profile。付费 Profile 在用户确认 Provider、版本和价格前禁用。
- 原因：控制批量成本，同时不让关键 Claim-Evidence 正确性受“全本地”目标绑架。
- 约束：本地失败最多尝试两次，只按 allowlist 升级；禁止自动换到更贵模型；RunManifest 记录 Token、重试、墙钟、GPU 和 CNY 成本。
- 代价：API Profile 未配置时 M3 Coordinator 与后续关键 Verifier/Synthesis fail closed；M2 受限 PaperCard/Claim 提取仍可使用已冻结的本地 Profile。

## ADR-009：先完成证据闭环，再增加 Multi-Agent

- 状态：Accepted
- 决策：M1 建立无 Agent Baseline，M2 完成 DocuMind Claim-Evidence MVP，M3 才引入真正并行 Agent。
- 原因：否则无法判断 Agent 带来的收益，也难以定位检索与编排错误。
- 代价：M2 前不能宣传真正 Multi-Agent 能力。

## ADR-010：评测种子与开发主题隔离

- 状态：Accepted
- 决策：一个开发主题用于调试，三个评测主题只用于阶段评测；所有运行记录数据截止日期和种子 hash。
- 原因：降低反复调参导致的评测泄漏。
- 代价：评测主题需要人工确认关键论文、问题和排除条件。

## ADR-011：模型只选择短证据引用

- 状态：Accepted
- 决策：Paper Analysis 模型只返回本次单论文输入中的 `chunk-N` 与 `quote-N`；Consumer 确定性解析真实 64 位 Chunk ID 和逐字 quote。
- 原因：M2 实机 smoke 证明本地模型不能稳定复制 SHA-256 和长引文；让概率模型承担字节级完整性会造成不必要的结构化失败。
- 约束：短引用最多 6 组，只在单次单论文调用内有效；未知引用、Chunk/quote 不匹配、跨论文响应全部 fail closed；最终 Evidence 必须保存真实 provenance、内容哈希和字符偏移。
- 代价：M2 引文粒度暂为受限 Chunk excerpt；更细的句级切分必须由确定性解析器产生候选，不能回退为模型自由复制。

## ADR-012：单 GPU 本地模型使用阶段屏障

- 状态：Accepted
- 决策：M2 先完成全部论文的 DocuMind 检索与校验，再启动本地 `qwen3:8b` 分析；同一 Paper 并发上限仍为 2。
- 原因：DocuMind 的 `qwen3-embedding` 与 ScholarTrace 的 `qwen3:8b` 共享单 GPU。交错执行会触发换模和排队，使 Embedding 查询在生成期间超时。
- 约束：Embedding readiness 在在线 smoke 前显式预热且有界检查；阶段屏障不放宽检索、模型或总任务预算。
- 代价：不能把单篇检索与单篇分析完全流水化，但三篇实测墙钟稳定且避免跨模型资源争用。

## ADR-013：Checkpoint、Artifact 与运行账本物理分离

- 状态：Accepted
- 决策：M3 使用 `langgraph-checkpoint-sqlite==3.1.1` 保存短期控制状态，独立 Artifact Store 保存完整业务对象，Runtime Ledger 保存幂等预算 effect 和业务事件。
- 原因：Checkpoint 恢复、业务数据生命周期和 SSE 断线续传有不同的查询与保留需求；混入同一状态会放大快照并导致重复副作用。
- 约束：自定义 `ArtifactRef` 通过严格 MessagePack 白名单恢复；相同 task/node/paper/round 使用稳定 effect key；目标保留窗口为 7 天，M3 只允许终态通过显式入口删除，自动清理由后续服务装配实现。
- 代价：单进程 MVP 需要管理三个 SQLite 文件；出现跨进程并发写或多用户需求后再迁移 PostgreSQL。

## ADR-014：M3 Coordinator 保持付费门禁并使用 Fixture 验证编排

- 状态：Accepted
- 决策：Coordinator 通过可注入接口接入；`api-strong` 未启用时生产门禁抛出明确错误，离线测试和 smoke 使用确定性 ResearchPlan Fixture。
- 原因：用户尚未确认 Provider、模型、价格和预算；为完成控制流验证而静默替换为本地模型会破坏 M0 冻结策略。
- 约束：Fixture 只能证明 Schema、interrupt、恢复、路由、并发和幂等，不能作为真实 Coordinator 规划质量或付费模型成本证据。
- 代价：M3 工程能力可复现，但真实研究任务在配置并评测 `api-strong` 前不能自动生成生产计划。
