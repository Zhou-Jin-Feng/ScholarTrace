# ScholarTrace 模型与成本策略

> 状态：M6 api-strong 结构化兼容性 smoke 与 12 题 B3/B4 受控评测完成；生产付费 Profile 继续禁用，ScholarGraph 默认关闭
> 日期：2026-08-30
> 原则：确定性逻辑不用 LLM；高频低风险任务优先本地模型；少量关键判断使用 API 强模型；所有模型选择由评测决定

## 1. 决策结论

ScholarTrace 首版采用混合模型路线，不采用“全部本地”或“全部付费 API”：

```text
确定性处理                    -> 普通 Python/规则，不调用 LLM
Embedding 与向量检索          -> DocuMind 现有本地 qwen3-embedding
高频、可复核、低风险生成任务   -> 本地 Qwen 7B/8B 级模型
少量、关键、高判断难度任务     -> API 强模型
ScholarGraph                  -> 保持已评测的本地 qwen3:8b 基线
```

M0 实机核验后，本地生成模型冻结为 Ollama `qwen3:8b`（模型 ID `500a1f067a9f`、8.2B、Q4_K_M、上下文上限 40,960），Embedding 冻结为 `qwen3-embedding:latest`（模型 ID `64b933495768`、7.6B、Q4_K_M、上下文上限 40,960）。GPU 基线为 8 GB RTX 3070 Ti Laptop。

M0 时用户确认不配置付费 API。M6 已由用户批准自定义 Provider、`gpt-5.6-terra`、有界 smoke 和 B3/B4 独立预算，并完成 Responses 严格结构化调用、12 题受控评测和人工盲审。该结果不自动打开生产 Profile：Provider 实际倍率/账单不可观测，eligible 的 B4-B3 平均质量差为 0 且 B4 延迟更高，Coordinator、关键 Verifier 和 Synthesis 继续 fail closed；不得静默改用任意 API，也不得自动升级到更贵价格层级。

## 2. 节点分配

| 节点或能力 | 首版模型策略 | 原因 |
|---|---|---|
| PDF 下载、解析、分块 | 不使用 LLM | 复用 DocuMind 确定性处理 |
| 论文 ID 归一化、去重、版本合并 | 不使用 LLM | 规则和数据源 ID 更可靠 |
| Citation Graph、图指标 | 不使用 LLM | OpenAlex/Semantic Scholar + NetworkX |
| Evidence 字段、哈希、页码、版本校验 | 不使用 LLM | 必须确定性通过或失败 |
| Embedding | 本地 `qwen3-embedding` | DocuMind 已有验证基线，避免重复付费 |
| DocuMind `/retrieve` | 不调用生成模型 | 只返回真实召回 Chunk |
| Search Query Rewrite/扩展 | 本地 `qwen3:8b` | 高频、输出短、可通过检索结果验证 |
| 标题/摘要相关性初筛 | 本地 `qwen3:8b` | 批量调用量大，错误可由后续全文阶段纠正 |
| PaperCard 字段提取 | 本地 `qwen3:8b` | 只处理单篇论文的受限 Evidence，可结构化验证 |
| Claim 初步提取 | 本地 `qwen3:8b` | 输出必须绑定 Evidence ID，失败可重试或返回 unknown |
| Coordinator 研究计划 | `api-strong`，当前禁用 | 规划错误会影响全部下游工作；未配置时 fail closed |
| 关键 Evidence Verifier | `api-strong`，当前禁用 | 需要判断支持范围、实验条件、冲突和过度概括 |
| 非关键 Claim 初筛 | 本地 Qwen，可选 | 先筛选，再将高风险项送强模型 |
| Synthesis 最终综述 | `api-strong`，当前禁用 | 需要跨论文平衡观点并控制证据强度 |
| ScholarGraph 查询 | 当前本地 `qwen3:8b` | 保持既有固定评测的可比性 |
| 单元测试、CI、离线开发 | Mock/固定响应，必要时本地模型 | 避免测试产生费用和非确定性 |

## 3. 本地模型使用边界

本地 Qwen 适合：

- 短上下文、单论文、结构明确的抽取；
- 查询扩展、分类、摘要初筛；
- 有确定性 Schema 和 Evidence ID 门禁的任务；
- 开发调试、批量实验和失败样本初筛。

以下情况升级到 API 强模型：

- 本地模型连续两次不能生成合法结构化输出；
- 需要跨多篇论文判断结论是否冲突；
- Claim 涉及关键数值、因果、SOTA、优越性或安全结论；
- Evidence 只部分支持 Claim，或限定条件容易遗漏；
- 最终报告需要综合支持、反对和证据不足观点；
- 评测发现本地模型使关键无支持结论率超过阶段门槛。

本地结构化输出最多尝试 2 次。只有 `invalid_structured_output`、`critical_claim`、`cross_paper_conflict` 或 `partial_support` 可以触发白名单升级；API Profile 禁用时升级请求必须停止并返回明确状态。不得为了追求“全本地”而降低 Claim-Evidence 正确性，也不得在未评测前把所有节点默认切到高价 API。

## 4. 首版运行预算

M2 冒烟与 MVP 默认限制：

- 全文论文：3-5 篇；
- Paper Worker 并发：2；
- 单篇论文 DocuMind 检索：最多 6 次；
- 证据补查：最多 1 轮；
- ScholarGraph：关闭；
- API 节点：Coordinator、批量关键 Verifier、最终 Synthesis；
- 输入 Token：最多 160,000；输出 Token：最多 40,000；总 Token：最多 200,000；
- 模型调用：最多 60 次，其中付费 API 最多 16 次；
- 墙钟时间：最多 1,800 秒；人民币总预算：最多 10 元；
- API 调用最多尝试 2 次，不自动升级到更昂贵模型；
- 达到硬上限时生成带未完成项的部分报告，不继续隐式消费。

进入 M3-M6 后，论文数、并发和预算只能依据小规模运行数据逐级提高。

## 5. 成本计算与记录

每次任务必须使用实际 Provider 价格和调用日志计算，不能把 10 元硬上限当作预计花费，也不能把示例金额写成项目事实：

```text
LLM 成本 = Σ(输入 Token / 1,000,000 × 输入单价
           + 输出 Token / 1,000,000 × 输出单价)

总成本 = LLM 成本 + Embedding 成本 + 其他付费 API/基础设施
```

RunManifest 至少记录：

- 节点、模型、Provider、模型版本和本地量化版本；
- 输入/输出 Token；
- 成功、失败、重试和结构化修复次数；
- 缓存命中；
- 墙钟耗时与排队时间；
- API 账单成本；
- 本地模型 GPU 时间；
- PDF、Chunk、Paper、Claim 和 Verification 数量。

原始币种账单通过同一价格快照中的汇率换算为 CNY；RunManifest 同时保留原始币种、原始成本、CNY 成本、价格快照时间和哈希。本地模型账单为零不代表没有成本，报告必须同时呈现运行时间、GPU 占用和失败重试。并发只缩短墙钟时间，不降低 Token。

## 6. 现有项目基线

### DocuMind

- Embedding 继续使用本地 Ollama `qwen3-embedding`；
- `/retrieve` 只负责检索，不调用生成模型；
- ScholarTrace 不调用 `/chat/stream` 构造 Evidence；
- 更换 Embedding 模型会改变向量维度和索引身份，必须重建并发布新版本，不能运行时静默切换。

### ScholarGraph

- 保持当前 GraphRAG 3.1.2、`qwen3:8b` 和 `qwen3-embedding` 评测基线；
- M5 真实 Basic 一次调用的 Provider 耗时为 35.043 秒、端到端 36.857 秒、Provider 报告成本 0 CNY；这只是单次兼容 smoke，不是延迟容量或质量基准；
- Basic 是正常路径候选，Local 仅用于实体邻域；Global/DRIFT 在 M5 禁用；
- ScholarGraph 输出只作为 abstract-level 辅助上下文，不替代 DocuMind 全文 Evidence；失败或越界回退 B3；
- 真实同条件 B3/B4 和人工盲审已完成；B3/B4 总体均分为 4.000000/3.916667，eligible 平均质量差为 0，因此生产默认保持关闭；
- 若替换 ScholarGraph 的补全模型，既有 B4 对比结论不可直接沿用，必须重新评测。

### api-strong

- 历史候选 `gpt-5.6-terra` 曾通过自定义 Provider 完成兼容性检查；该 Provider 已停用，不能视为当前运行配置；
- 2026-08-30 Responses 严格结构化 smoke 一次成功，无重试/回退，输入 5,005、输出 406 Token，耗时 20.161 秒；
- 按官方 Terra $2/$12 每百万输入/输出 Token 和 7.5 规划汇率估算 0.111615 CNY；Provider 响应未返回实际账单或倍率，因此该数值不是账单；
- `OpenAICompatiblePlanGenerator` 只允许模型生成计划草稿；任务身份、截止日期、Budget 和审批状态由确定性代码注入；
- 生产 Profile 在实际计费口径和人工质量证据确认前继续禁用。

## 7. 模型选择验收指标

固定样例位于 `evaluation/seeds/model_comparison_cases.json`，覆盖 query rewrite、Claim-Evidence 绑定和摘要相关性初筛。M0 本地 smoke 结果位于 `evaluation/reports/m0_local_model_smoke.json`：3/3 结构化成功、3/3 语义规则通过、P95 8.466 秒、API 成本 0 元；这只是小样本兼容 smoke，不是质量或容量基准，GPU 时间尚未与墙钟时间分离测量。

同一 Prompt、Schema、温度和上下文下，候选模型至少比较：

- 结构化输出成功率；
- PaperCard 字段准确率；
- Claim Support Rate；
- Unsupported Critical Claim Rate；
- Evidence/引用准确率；
- 冲突识别准确率；
- 报告覆盖率与人工评分；
- P50/P95 延迟；
- 单任务 Token、API 费用和本地 GPU 时间；
- 重试率、失败率和降级率。

M0 三例 smoke 的门槛为结构化成功率 100%、语义规则通过率 100%、关键无支持结论率 0、P95 不超过 120 秒、单例 API 成本不超过 0.5 元。正式节点选型必须扩展样本和人工复核，不能用三个样例证明质量。只有本地模型在对应节点达到质量门槛时，才保留本地路由；只有 API 强模型带来可复现收益时，才承担额外费用。

## 8. 模型配置与启用条件

M0 需要冻结：

- [x] 本地模型真实名称、量化版本和上下文上限；
- [x] M0 付费配置决策：保持禁用，Provider、模型版本和价格快照不填；未来启用前仍需独立预算授权；
- [x] 每个 Agent/Node 的默认 Profile 与允许的 fallback；付费 Profile 未配置时禁用；
- [x] 结构化输出和升级路由规则；
- [x] 单任务 Token、调用数、耗时和人民币预算上限；
- [x] 模型对比的固定样例与验收阈值；
- [x] RunManifest 的模型、Token、成本和 GPU 时间字段。

M0 结束时 API Profile 仍禁用。进入需要付费强模型的业务阶段前必须重新完成 Provider、模型版本、上下文和价格快照的人工确认；在此之前不运行付费节点，不进行 20-50 篇论文的批量运行，也不执行完整 B0-B4 评测。
