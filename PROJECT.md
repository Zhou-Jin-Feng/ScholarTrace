# ScholarTrace 是一个面向计算机与人工智能技术调研的证据可追溯 Multi-Agent 学术研究工作台，服务学生、开发者和初级研究人员，核心价值是让关键结论能够回溯到真实论文、页码或 Chunk。

## 当前项目定位

- 产品名：ScholarTrace
- 英文副标题：Evidence-Grounded Research Agent
- 上游能力：`DocuMind` 原文 RAG、`ScholarGraph` GraphRAG 语义检索
- 当前阶段：M0-M6 已完成工程复审；M7-G/M8-G Gate A 均为 `NO_GO / keep_disabled`；M9-P0 已完成 30 条 eligible 审核并得到 `GO_IMPLEMENT`；M9-P1 已在 ScholarGraph `04f5327` 冻结单一 `G3_RANKING` 重排，P0 已知开发回放由 B6 1/10 提高到 B7 4/10，但尚无前瞻正增益；ScholarGraph 仍默认关闭，P2 连续新样本 Gate A 尚未实施

## 目标交付

- 可审批、可恢复、有预算的研究任务工作流；
- 多源论文搜索、身份归一化、去重与版本合并；
- 基于 DocuMind 的论文级原文证据检索；
- Paper、Claim、Evidence、Verification 结构化契约；
- 显式引用网络、独立证据核验和有限补查循环；
- 对 ScholarGraph 的能力受限、可评测集成；
- 单流程、单 Agent、Multi-Agent 与 GraphRAG 增强对照评测；
- 研究工作台、报告导出、测试、可观测性与可复现交付。

## 明确不做

- 不把 DocuMind 或 ScholarGraph 整体包装成“子智能体”；
- 不在首版重建 RAG、GraphRAG、Milvus 或语义索引；
- 不让 ScholarGraph 超出其 2020-2025 年、198 篇 RAG 摘要语料边界回答；
- 不依赖 Google Scholar 网页抓取；
- 不在实验前承诺 Multi-Agent、GraphRAG 或 Neo4j 必然提升质量；
- 不自动获取或传播无合法访问权限的论文全文。
