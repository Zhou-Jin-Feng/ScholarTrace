# SA-02 核验消融协议与冻结题集

> 历史记录：本文件描述对应轮次；当前实现、状态与评价见 [核验消融结果](VERIFICATION_ABLATION_RESULTS.md)。
状态日期：2026-09-21
数据状态：`frozen_not_executed`
本阶段调用：0 次模型调用，0 次外部调用

## 1. 目标与边界

本实验只回答一个问题：在相同的核验前 Claim/Evidence 输入上，增加独立语义核验是否减少最终报告中的无支持事实论断，以及它会损失多少有效内容、增加多少调用成本。

本实验不比较搜索、全文获取、DocuMind 检索、Claim 生成或多 Agent 编排质量，也不把 Verifier 自己的状态当成人工真值。正式产品的 M4 门禁保持不变；V-off 只存在于后续独立实验入口。

预注册假设为：V-on 可能减少无支持事实论断，但也可能误拦正确内容或造成失败；没有预设 V-on 更优。10 道正式题只做配对描述性比较，不据此宣称总体显著性。

## 2. 冻结数据

公开脱敏清单位于 `evaluation/seeds/sa_02_verification_ablation_manifest.json`，数据集指纹为：

`c2e017a11df5677d1363a27a10f3361f00d5478d8b7b6a8a52c878185b48dddf`

数据基线为 ScholarTrace `v1.0.2` / `3d095a79feff257b57b041de40b7b5ed73021f9e`：

| 集合 | 问题 | 核验前 Claim | 唯一 Evidence | 用途 |
|---|---:|---:|---:|---|
| pilot | 2 | 12 | 11 | 验证入口、记录、输出可审阅性和实际用量 |
| formal | 10 | 56 | 45 | 冻结配置后的正式配对比较 |

正式题来自 5 份既有真实 Evidence 报告的主题拆分，pilot 来自另一份三论文真实 Evidence 报告。每份来源报告的 Claim 均被穷尽且互斥地分配，没有依据历史 Verifier 状态删题或删 Claim。历史 Verification、Report Gate 文本和报告回答没有进入新输入。

运行输入与人工参考分开保存在 Git 忽略区。运行输入包含问题、Paper/Binding/Chunk 身份、核验前 Claim、Evidence 和重新计算的确定性校验；人工参考另存必须覆盖要点与逐字原文定位。公开文件只保存题目、计数和哈希，不保存 Claim 文本、原文 quote、人工要点或原始模型回答。

私有运行输入文件 SHA-256 为 `4c8de9c4a401c948d816118b5660f1d78c239249dadb2ab84258dc21a7587a96`；私有人工参考文件 SHA-256 为 `be755ff71ca5c1ed4535ee61f2de032406f926e38f39f9de59dea82bd10328d5`。修改任何问题、Claim、Evidence、原文定位或要点都会改变逐题哈希和数据集指纹，必须显式创建新版本，不能覆盖本轮记录。

## 3. 冻结题集

| ID | 集合 | 类别 | 问题 | Claim / Evidence |
|---|---|---|---|---:|
| `sa02-pilot-01` | pilot | 普通事实、条件冲突 | How does CRAG map retrieval-evaluator confidence to Correct, Incorrect, or Ambiguous actions? | 4 / 3 |
| `sa02-pilot-02` | pilot | 跨论文比较、条件冲突 | How do the RAG survey and Adaptive-RAG describe when to skip retrieval, retrieve once, retrieve recursively, or use multi-step retrieval? | 8 / 8 |
| `sa02-formal-01` | formal | 条件冲突 | Under which task, corpus, and model conditions does GraphRAG report improvements over vector RAG? | 3 / 3 |
| `sa02-formal-02` | formal | 跨论文比较、条件冲突 | How do HippoRAG and LightRAG compare with iterative or chunk-based retrieval on quality, speed, cost, and dataset scale? | 6 / 5 |
| `sa02-formal-03` | formal | 普通事实、跨论文比较、证据不完整、条件冲突 | Which dimensions do RGB and Ragas evaluate, and does the supplied evidence actually separate retrieval quality from generation quality? | 7 / 3 |
| `sa02-formal-04` | formal | 普通事实 | How does ARES evaluate context relevance, answer faithfulness, and answer relevance, and what evidence supports its judge reliability? | 4 / 3 |
| `sa02-formal-05` | formal | 普通事实、条件冲突 | How does PoisonedRAG define the retrieval knowledge-corruption attack surface, and what defense limitations does it report? | 5 / 4 |
| `sa02-formal-06` | formal | 跨论文比较、证据不完整、条件冲突 | What does the supplied evidence support about indirect prompt-injection and adversarial-document mitigation, and where is it insufficient? | 4 / 3 |
| `sa02-formal-07` | formal | 普通事实、跨论文比较 | How do Ragas and the RAG survey define context relevance, answer relevance, and faithfulness, including the cost of irrelevant context? | 8 / 7 |
| `sa02-formal-08` | formal | 跨论文比较、条件冲突 | What trade-offs do CRAG, HippoRAG, and LightRAG claim among correction, retrieval quality, latency, and adaptation? | 8 / 7 |
| `sa02-formal-09` | formal | 普通事实、跨论文比较 | Which graph and hybrid retrieval mechanisms do GraphRAG, HippoRAG, and LightRAG use for global or multi-hop questions? | 8 / 8 |
| `sa02-formal-10` | formal | 证据不完整、条件冲突 | What scalability or resource costs are actually evidenced for GraphRAG, HippoRAG, and LightRAG? | 3 / 3 |

正式集类别覆盖为：普通事实 5 题、跨论文比较 6 题、证据不完整 3 题、条件/结论冲突 7 题；同一题可以属于多个类别。

## 4. 两个条件

两条件共同执行以下步骤：

1. 校验逐题输入 SHA-256、Paper/Evidence 身份和数据集指纹。
2. 重新执行确定性的引用、来源、Chunk、字符范围、页码、数值和白名单检查；失败 Claim 不进入任一条件。
3. 使用同一报告模型、Provider 协议、实验报告 Prompt、结构化 Schema、长度上限和超时。
4. 输出只能引用输入白名单中的 Evidence ID；报告保留输入语义状态，不能自行把 `unverified` 升级为已核验。

唯一处理变量如下：

| 条件 | 独立语义核验 | 进入报告前的 Claim 处理 |
|---|---|---|
| V-on | 对每个确定性校验通过的 Claim 调用强 Verifier | supported 保留；partially_supported/conflicted 显式弱化或标记；unsupported 排除；关键 Claim 被拦时按规则失败或证据不足 |
| V-off | 不调用强 Verifier | 所有确定性校验通过的 Claim 保留，但统一标记为 `unverified`；不得填造 supported 状态 |

V-on 与 V-off 都使用独立实验报告适配器和 `sa02-ablation-report-v1`，不会让 V-off 绕过正式 `M4ReliabilityPipeline`。报告生成模型固定为 `gpt-5.6-terra`，协议为 Responses，温度参数省略并使用相同 Provider 默认值，辅助上下文均为空，不自动重试。报告字符上限为 5,000，输出 Token 上限为 1,200。

Verifier Prompt SHA-256 为 `6fb7bd2fdffb62613e38feb07c02838972e677edf8e5844fecd2d59fbcab97cd`；共同报告 Prompt SHA-256 为 `01d2fd85f1a24427d6c53ff9ce07787b13f2a2011bb0818a9a8cd39c395f67db`。SA-03 必须实现并测试这些约束。SA-04 若根据 pilot 修改共同报告配置，只允许一次有记录的调整；正式题输入和人工参考不得查看或改动，并须在正式运行前生成新的配置哈希。

## 5. 执行与失败规则

- SA-03 只用 Fixture/离线输入验证两个条件、记录格式和失败关闭，不进行质量结论。
- SA-04 只运行两道 pilot；必须在显式付费调用配置和价格/模型可用性复核后开始。
- pilot 配置冻结并经人工确认后，SA-05 才能运行 10 道正式题；pilot 不并入正式质量指标。
- 每题两个条件各运行一次。奇数题先 V-off、偶数题先 V-on，降低固定顺序偏差；人审包再以独立私有映射随机显示为 A/B。
- 不自动重试、切换模型或切换协议。超时、未知 usage、Schema 错误、输入漂移和预算超限均记录为失败；恢复只能从已保存的同一输入/配置检查点继续，不能把第二次尝试静默替换第一次失败。
- 人审前不公开条件映射、Verifier 状态或处理动作。审阅者身份、是否见过条件标签、是否为项目所有者自评都必须记录；自评不称作独立盲评。

## 6. 人工标签与指标

人工审阅以原文和冻结参考要点为准，不以 Verifier 状态为准。

### 标签

- 报告事实论断：`supported`、`partially_supported`、`unsupported`、`indeterminate`。
- 关键要点覆盖：`covered`、`partially_covered`、`absent`；加权覆盖分别计 1、0.5、0。
- 核验前 Claim 真值：使用同样四级标签，并记录是否需要限定语。
- `indeterminate` 始终单列，不能并入 supported 或 unsupported。

### 主要质量指标

1. 无支持事实论断数与比例：分母为该条件所有可判定事实论断，逐题保留分子和分母。
2. 关键要点加权覆盖率：分母为该题预注册要点数，同时报告完整覆盖、部分覆盖和缺失的原始计数。
3. 误拦率：V-on 完全排除人工判为 supported/partially_supported 且本可经限定保留的核验前 Claim 数，除以这两类 Claim 总数；正确弱化不计误拦。
4. 有效拦截数：人工判为 unsupported 的核验前 Claim 被 V-on 排除或明确降为证据不足，且对应 V-off 报告仍把它作为事实呈现的数量。
5. 配对差值：逐题计算 V-off 减 V-on 的无支持论断数和覆盖率，再报告均值、中位数、范围及 10 题明细，不只给总均分。

### 可靠性与资源指标

- 每条件成功、失败、证据不足数量和失败原因；失败不得从分母消失。
- 确定性引用/来源检查失败数和非法 Evidence ID 数。
- Verifier、报告及总计的模型调用、Provider 调用、输入/输出/缓存/推理 Token、墙钟时间和参考成本。
- Provider 实际账单只有在响应或账单接口明确提供时记录；否则保持 `null`，不能用参考成本代替。
- 报告每减少一个无支持事实论断的增量调用数、增量秒数和增量参考成本；分母为 0 时写 `undefined`。

## 7. 预注册解释规则

以下是小样本的实践判定，不是统计显著性检验：

- 若 V-off 正式结果合计少于 5 个无支持事实论断，题集对本假设缺少可判定事件，结论为 `INCONCLUSIVE_CEILING`。
- 在至少 8/10 个正式配对成功的前提下，V-on 同时满足“无支持论断至少减少 2 个且相对减少不低于 30%”“关键要点加权覆盖下降不超过 5 个百分点”“误拦率不高于 10%”，才记为 `USEFUL_WITH_MEASURED_COST`。
- 若无支持论断没有下降，或覆盖下降超过 10 个百分点，或误拦率超过 10%，当前全量核验方案记为 `DO_NOT_ADOPT_AS_IS`。
- 其余结果记为 `MIXED_OR_INCONCLUSIVE`，保留逐题案例和资源数据，不通过改题、删失败或事后改分母制造正结论。

## 8. 预算

预算明细位于 `evaluation/reports/sa_02_verification_ablation_budget.json`。它按冻结调用数、`gpt-5.6-terra` 本地留存参考费率和 25% 余量计算，不是 Provider 账单，也不是付费授权。

| 阶段 | Verifier 调用 | 报告调用 | 总模型调用 | 规划额度 | 失败关闭硬包络 |
|---|---:|---:|---:|---:|---:|
| 2 题 pilot | 12 | 4 | 16 | 3 CNY | 5 CNY |
| 10 题 formal | 56 | 20 | 76 | 14 CNY | 24 CNY |
| 合计 | 68 | 24 | 92 | 17 CNY | 29 CNY |

调用前必须重新核对模型可用性、Provider 价格、账户倍率和结构化输出能力。pilot 与 formal 分开配置；formal 额度只能在 pilot 实际 Token、失败和耗时已知后重新估算。未知 usage、累计实际参考成本高于预留值或任一硬上限触发停止。

## 9. 已知限制

1. 10 道正式题是 5 次上游真实 Evidence 运行的穷尽主题拆分，不是 10 次独立检索；问题之间存在来源相关性。
2. 该设计只估计 Evidence 已生成后的核验价值，不能外推到端到端搜索召回或全文获取质量。
3. Verifier 与报告生成计划使用同一模型系列，错误可能相关；最终判断必须依赖原文人审。
4. 冻结要点已按逐字来源准备并与运行输入隔离，但尚未完成 SA-06 的正式内容复核；它们不冒称独立专家 Gold。
5. 单次生成和 10 题样本只能提供项目级工程证据。任何无收益、误拦或失败结果都必须保留。

SA-02 只冻结题集、输入、协议和预算，不执行模型调用。下一步 SA-03 是实现独立实验入口和结果记录格式，仍先做零费用验证。
