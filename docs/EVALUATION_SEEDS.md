# ScholarTrace M0 评测种子

> 机器版本：`evaluation/seeds/m0_topics.json`  
> 状态：M0 结构与人工判断已于 2026-08-29 确认  
> 检索截止日期：2026-08-29

## 1. 使用纪律

- DEV 主题可用于 M1/M2 调试、Prompt 修改和错误分析；
- EVAL 主题不得用于针对性调 Prompt，只在预登记流程下运行；
- 关键论文是检索黄金种子，不是允许系统只返回这些论文；
- 每个主题先验证论文身份，再获取合法全文；
- 未找到全文时只能创建 abstract/metadata Evidence；
- 所有问题必须同时记录质量、延迟、失败和成本。

## 2. DEV-01：自适应检索与纠错 RAG

研究问题：自适应检索、检索质量评估和证据核验如何改善 RAG 的 faithfulness，它们的代价和失败边界是什么？

关键论文种子：

| 论文 | 身份 | 作用 |
|---|---|---|
| Corrective Retrieval Augmented Generation | arXiv:2401.15884 / OpenAlex W4391418506 | 检索评估与纠错动作 |
| Retrieval-Augmented Generation for Large Language Models: A Survey | arXiv:2312.10997 / W4389984066 | RAG 分类和评测背景 |
| Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection | arXiv:2310.11511 | 自反思检索与生成；需处理上游元数据异常 |
| Adaptive-RAG: Learning to Adapt Retrieval-Augmented LLMs through Question Complexity | arXiv:2403.14403 | 按问题复杂度路由 |

预登记问题：

1. 各方法用什么信号触发检索、纠错或停止？
2. 纠错动作处理的是检索相关性、生成忠实度还是两者？
3. 方法在哪些数据集、模型和指标上评测？
4. 与普通 RAG 相比，质量收益对应多少额外延迟和调用？
5. 哪些失败案例显示方法不能可靠判断证据质量？

排除：无外部检索的纯 Prompt 方法、无技术细节的产品文章、没有可验证实验的二手总结、仅使用私有不可审计语料且不报告设置的工作。

## 3. EVAL-01：GraphRAG 与全局/多跳问题

研究问题：图增强 RAG 在全局综合和多跳问题上相对向量 RAG 的收益是否有可复现实验证据？

关键论文种子：

- From Local to Global: A Graph RAG Approach to Query-Focused Summarization（arXiv:2404.16130）；
- HippoRAG: Neurobiologically Inspired Long-Term Memory for Large Language Models（arXiv:2405.14831）；
- LightRAG: Simple and Fast Retrieval-Augmented Generation（arXiv:2410.05779）。

预登记问题：

1. 每个方法的图节点、边和索引如何构建？
2. 哪类问题被定义为 local、global 或 multi-hop？
3. 图方法与向量 Baseline 是否共享模型、语料和预算？
4. 质量收益是否同时报告索引成本、查询延迟和失败率？
5. 哪些结果只来自摘要或模型 Judge，证据强度如何？

排除：没有向量 Baseline 的概念稿、把显式引用图和生成式语义图混为一谈的材料、未记录模型或数据集的演示。

## 4. EVAL-02：RAG 评测的可靠性

研究问题：现有 RAG 评测框架如何测量检索质量、faithfulness 和回答相关性，它们在哪些条件下可能给出误导结论？

关键论文种子：

- RAGAs: Automated Evaluation of Retrieval Augmented Generation（DOI:10.18653/v1/2024.eacl-demo.16）；
- ARES: An Automated Evaluation Framework for Retrieval-Augmented Generation Systems（arXiv:2311.09476）；
- Benchmarking Large Language Models in Retrieval-Augmented Generation（arXiv:2309.01431）。

预登记问题：

1. 指标需要黄金答案、黄金上下文还是 LLM Judge？
2. 检索与生成指标能否定位失败来自哪一层？
3. Judge 与生成模型同族时有哪些偏差风险？
4. 指标与人工判断的一致性如何验证？
5. 质量、延迟、Token 和费用如何组成公平对照？

排除：只报告单个自动分数、未公开 Judge Prompt 或评分尺度、用开发集同时做调参和最终结论的实验。

## 5. EVAL-03：RAG 安全与语料投毒

研究问题：攻击者如何通过恶意文档、间接 Prompt Injection 或检索排序操纵 RAG 输出，哪些防御经过了可复现实验？

关键论文种子：

- PoisonedRAG: Knowledge Corruption Attacks to Retrieval-Augmented Generation of Large Language Models（arXiv:2402.07867）；
- Benchmarking and Defending Against Indirect Prompt Injection Attacks on Large Language Models（按标题和正式标识检索后确认）；
- RAG 系统安全综述或基准论文（M1 多源检索后冻结正式 ID）。

预登记问题：

1. 攻击控制语料、查询、排序还是生成上下文中的哪一层？
2. 攻击成功率如何定义，是否影响正常问题质量？
3. 防御依赖文档信任、检索异常检测还是生成约束？
4. 防御在自适应攻击和跨模型条件下是否仍有效？
5. 系统日志如何保留审计证据而不泄露恶意正文？

排除：只展示单次越狱对话、没有攻击模型和成功标准、要求执行恶意指令的材料、无法合法获取的私有测试集。

## 6. Claim-Evidence 人工标注样例

来源：CRAG 的公开摘要，Evidence 等级为 `abstract`，不能写成已核对全文。

Evidence：

> Specifically, a lightweight retrieval evaluator is designed to assess the overall quality of retrieved documents for a query, returning a confidence degree based on which different knowledge retrieval actions can be triggered.

Claim：

> CRAG 使用轻量检索评估器评估查询对应的检索文档整体质量，并依据置信度触发不同检索动作。

标注：`supported`。该 Evidence 直接支持方法机制，但不支持“降低了多少幻觉”或“在所有数据集上优于其他方法”等更强结论。

反例：如果 Claim 写成“CRAG 的检索评估器总能正确识别错误文档”，应标记 `unsupported`，因为摘要没有提供“总能正确”的证据。

## 7. 人工复核结论与后续项

1. 四个主题及其研究范围已确认；
2. 用户已接受当前预登记问题、排除条件和 CRAG Claim-Evidence 标注样例作为 M0 基线；
3. M1 通过多源 API 核验所有关键论文的正式身份和版本；
4. ScholarGraph 正式语料将 arXiv:2310.11511 记录为 CareerX，但作者字段对应 Self-RAG 作者，必须作为身份冲突案例，不能自动覆盖；
5. EVAL-03 的第二、第三篇关键论文需要 M1 检索后冻结正式 ID。

## 对照评测一致性约束

以下为评测设计要求，不表示所有对照或完整重复实验已经完成；实际样本量、运行状态和限制以对应版本报告为准。

- B0 为简单检索生成基线，B1 为单流程/单 Agent 与 DocuMind，B2 为不含 Verifier 的 Multi-Agent，B3 包含 Verifier，B4 为 B3 加能力受限的 ScholarGraph；各版本实际装配范围须分别披露。
- 除被消融组件外，对照固定问题、候选论文池、全文访问条件、截止时间、模型与 Prompt 版本、温度、结构化输出策略、索引/语料身份、超时、报告长度和评分量表。
- 固定搜索、论文、Token 与费用预算；若预算不等则单独说明，不能把额外资源误认为架构收益。非确定性结果应报告重复次数、离散程度、成功率及失败样本。
- 开发主题与评测主题隔离；ScholarGraph eligible 题目与越界题目分开统计，不能把越界拒绝算成质量增益，也不能混算 B3/B4 的总体平均数来替代 eligible 结论。
- 隐藏方法身份并随机化答案顺序；关键论文、引用、数值和 Claim-Evidence 由人工或确定性规则复核，不能只依赖 LLM Judge。
- 同时记录搜索召回、去重、证据覆盖、关键无支持结论、冲突检测、延迟、失败与成本。未运行或不理想的结果如实保留，不预填收益数字。
