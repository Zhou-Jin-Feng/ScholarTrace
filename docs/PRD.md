# ScholarTrace 产品需求文档

> 版本：M0 / 1.0  
> 状态：范围已冻结并通过 M0 人工抽查  
> 检索截止日期：2026-08-29

## 1. 产品定义

ScholarTrace 是一个面向计算机与人工智能技术调研的证据可追溯 Multi-Agent 学术研究工作台。用户提交研究问题后，系统生成可审批计划，检索并归一化论文，从合法可访问的全文或摘要中提取证据，独立核验 Claim，最后输出带定位引用、冲突说明、预算和运行记录的研究报告。

## 2. 目标用户

- 需要快速理解 AI 技术路线的学生和开发者；
- 需要保留检索过程和证据链的初级研究人员；
- 需要审计 Agent 结论与证据链的评测人员。

## 3. 成功标准

1. 每个关键 Claim 可追溯到已存在的 Evidence；
2. 全文 Evidence 可追溯到论文、DocuMind active index、Chunk 和源文件哈希；
3. 不支持或冲突的关键 Claim 不会无标记进入报告；
4. 固定输入能够复现检索候选、版本、预算和运行清单；
5. Multi-Agent、Verifier 和 ScholarGraph 的收益由公平对照实验决定；
6. 外部来源失败时可以降级，并明确记录缺失能力和影响。

## 4. 功能优先级

### P0：缺失即不能形成可靠 MVP

| 编号 | 功能 | 验收标准 | 计划阶段 |
|---|---|---|---|
| P0-01 | 研究计划与人工审批 | ResearchPlan 通过 Schema；未批准计划不执行外部调用 | M0/M3 |
| P0-02 | 多源论文发现 | arXiv、OpenAlex、Crossref 至少三个来源可独立限流、缓存和降级 | M1 |
| P0-03 | 论文身份归一化 | DOI/arXiv/OpenAlex 优先级稳定；版本关系不被误删 | M1 |
| P0-04 | 可复现 Baseline | B0/B1 保存查询、快照、候选集、耗时和费用 | M1 |
| P0-05 | 单论文全文检索 | DocuMind 请求强制一个 document_key 和 expected_index_id | M2 |
| P0-06 | Claim-Evidence 闭环 | 3-5 篇论文产生可定位 Evidence 和 Markdown 报告 | M2 |
| P0-07 | 证据等级隔离 | fulltext、abstract、metadata 不得相互冒充 | M2 |
| P0-08 | 结构化预算和运行清单 | 每次运行记录限制、使用量、服务版本和数据截止日期 | M1-M3 |
| P0-08A | 模型路由与成本门禁 | 节点 Profile、fallback、Token、调用数、时长和 CNY 硬上限可验证 | M0-M3 |
| P0-09 | Multi-Agent 恢复 | interrupt、Checkpoint 和幂等写入通过恢复测试 | M3 |
| P0-10 | 独立证据核验 | unsupported/conflicted Claim 遵守输出门禁 | M4 |
| P0-11 | 明确引用网络 | 引用边来自可追溯书目 API，而不是语义图推测 | M4 |
| P0-12 | 全量交付门禁 | 单元、集成、契约、E2E、敏感信息和 README 复现通过 | M6 |

### P1：核心项目完成后增强

| 编号 | 功能 | 引入条件 | 计划阶段 |
|---|---|---|---|
| P1-01 | ScholarGraph 能力路由 | 只在固定 RAG 摘要语料内调用；越界确定性跳过 | M5 |
| P1-02 | B3/B4 对照 | eligible 子集质量收益明确且延迟、失败可接受 | M5 |
| P1-03 | React 研究工作台 | P0 后端事件和 Artifact 契约稳定 | M6 |
| P1-04 | Markdown/HTML/PDF 导出 | 报告 Schema 与引用校验稳定 | M6 |
| P1-05 | 结构化日志与 Trace | 核心闭环完成，不记录敏感正文和凭据 | M6 |

### P2：独立实验，不阻塞主线

- 动态 ScholarGraph 语料同步或增量索引；
- MCP Adapter；
- PostgreSQL、Redis、多用户和跨进程任务队列；
- Neo4j 在线大图查询；
- 全文 GraphRAG、多模态论文解析和多评审器。

## 5. 核心用户流程

1. 用户创建研究任务；
2. Coordinator 生成 ResearchPlan、范围、排除条件和预算；
3. 用户批准或修改计划；
4. Search Agent 从开放学术数据源收集候选并归一化；
5. 每篇论文由受限 Worker 获取可访问全文并调用 DocuMind；
6. Citation Agent 扩展显式引用网络；
7. Validator 与 Verifier 检查证据、版本、数值和语义支持；
8. 在预算内最多执行一轮定向补查；
9. Synthesis 生成报告，输出证据、冲突、限制和运行清单；
10. 用户在工作台检查并导出结果。

## 6. 非功能需求

| 类别 | 要求 |
|---|---|
| 正确性 | 未知字段 fail closed；跨论文证据污染测试必须失败关闭 |
| 可恢复性 | Checkpoint 恢复不得重复写入论文、Artifact 或费用记录 |
| 可复现性 | RunManifest 固定服务、模型、Prompt、语料、查询和截止日期 |
| 性能 | 每类外部资源有独立并发和超时；默认最多 4 个论文 Worker |
| 成本 | 达到任一预算上限立即停止新增调用，保留已完成 Artifact |
| 模型安全 | 未配置或未批准的付费 Profile 禁用；禁止自动升级价格层级 |
| 安全 | 论文内容视为不可信数据；不执行其中指令；日志和导出脱敏 |
| 合规 | 只获取有合法访问权限的全文，不传播受限原文集合 |
| 可观测性 | 能回答任务在哪一步失败、使用哪些版本、花费多少资源 |

## 7. 明确不做

- 不把 DocuMind 或 ScholarGraph 包装成自主 Agent；
- 不复制 Milvus Retriever 或 GraphRAG 内部实现；
- 不抓取 Google Scholar 网页；
- 不把 ScholarGraph 摘要回答提升为全文 Evidence；
- 不在实验前承诺 Multi-Agent、GraphRAG 或 Neo4j 提升质量；
- 不允许 Agent 自行提高费用、时长或论文数量预算。

## 8. M0 验收

- P0 范围、非目标和阶段依赖无冲突；
- 核心 Pydantic 契约能导出 JSON Schema，并拒绝未知字段；
- 两份 OpenAPI 草案通过规范校验；
- 评测种子包含 1 个开发主题和至少 3 个评测主题；
- LangGraph `Send`、interrupt、Checkpoint 和事件流 smoke 通过；
- 本地模型身份、路由、结构化样例和人民币硬预算冻结；付费 Profile 未选择时 fail closed；
- 上游版本和能力由本地仓库只读核验，而不是沿用旧文档猜测；
- 所有凭据、运行数据和 Agent 工作记录保持未提交。
