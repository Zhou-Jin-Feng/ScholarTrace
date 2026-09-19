# M9 真实漏检审计与定向图修复协议

> 状态：P0 `GO_IMPLEMENT`；P1 单一 G3 排序修复已在 ScholarGraph `04f5327` 冻结；
> P2 已完成 50 条 eligible 前瞻样本与 Gate A；Gold `50/50` 均为 ambiguous，结论 `INCONCLUSIVE`，ScholarGraph 仍默认关闭
> 基线：ScholarTrace P0 冻结 `125fa1c`；冻结 B5 `37e00cf`；P1 B7 `04f5327`
> 前置结论：M6、M7-G、M8-G 结果保持冻结，ScholarGraph 继续默认关闭

## 1. 目标

M9 不再围绕既有题集调参，而是从连续发生的真实 ScholarTrace 研究任务中识别 B5 候选
漏检，判断缺口究竟来自语料、全文边界、实体/关系图还是候选排序。只有证据证明缺口可由
图结构解决，才允许 ScholarGraph 实施一个最小、可归因的修复。

M9 要验证的完整因果链仍然是：

```text
真实任务产生 B5 漏检
  -> 根因被确认且属于图可解决范围
  -> B7 定向修复在新的前瞻样本中补回候选
  -> 新候选经 DocuMind 形成有效 Evidence
  -> Claim 支持率或盲审质量改善且代价可接受
```

其中 `B5` 是 M8 冻结的 title/text top-3 基线；`B7` 是 M9 未来可能产生的定向图修复。
M9 不重定义或覆盖 M8 的 `B6`。

## 2. 非目标

- 不修改 M8 开发集、holdout、评分代码或正式结论；
- 不为获得正分而降低 B5 的 top-k、输入质量或预算；
- 不直接扩到 1000 篇，不自动重建 GraphRAG 索引；
- 不把 GraphHint、摘要或关系路径当作全文 Evidence；
- 不在 P0 实现 Consumer、调用模型、运行 DocuMind 回放或产生费用；
- 不把零散的已知漏检案例当作泛化收益证据。

## 3. M9-P0：真实漏检观察与根因审计

### 3.1 连续采样

- 采样单位是实际 ScholarTrace 任务中的唯一、可独立判断的研究子问题；
- 只纳入当前 198 篇 RAG 摘要语料能力边界内的 `graph_eligible` 子问题；
- 按发生顺序连续收集，初始窗口为 30 个 eligible 子问题；
- 若不足 6 个“gold 在语料内但 B5 漏检”的机会样本，可连续扩展一次，最多 50 个；
- 不得因 B5 成功、B7 失败或题目难度而删除、替换或改变顺序。

原始问题、人工备注和来源细节保存在忽略的 `agent/` 中。公开产物只保存稳定记录 ID、
脱敏标签、配置/输入哈希、候选 ID、聚合指标和必要的权威来源标识，不保存私密查询、
论文全文、模型原始回答或凭据。

### 3.2 漏检记录最小字段

| 字段 | 含义 |
|---|---|
| `record_id` | 不暴露用户或任务身份的稳定记录 ID |
| `sequence_id` | 证明连续采样顺序，不使用结果挑题 |
| `question_sha256` | 规范化问题的 SHA-256；原文默认私有 |
| `eligibility` | 语料主题、年份、语言和证据层级边界 |
| `b5_config_hash` | 冻结 B5 算法、top-k 和语料身份 |
| `b5_candidate_ids` | B5 返回的 OpenAlex ID |
| `gold_candidate_ids` | 经所有者确认或按已记录的所有者代理审核授权确认的相关论文 ID |
| `gold_basis` | OpenAlex、引用链、公开论文身份或已验证 Evidence |
| `in_frozen_corpus` | gold 是否存在于 198 篇冻结语料 |
| `graph_path_status` | 是否存在有效实体/关系/文档回链 |
| `root_cause` | 使用下述互斥主分类，可附一个次分类 |
| `review_status` | `pending / confirmed / rejected / ambiguous` |

默认情况下，Gold 不能只由生成模型自评产生，至少需要项目所有者人工确认论文相关性、
语料身份和主要根因。项目所有者可明确授权代理审核按冻结标准处理后续记录；此时必须在
私有 review notes 中标明授权，只依据冻结权威元数据生成 Gold，确认 Gold 前不得查看
GraphHint 或 GraphPath，并保留 P0 汇总进入 P1 前的所有者最终批准。代理审核不是独立盲审，
不得表述成额外人工一致性证据。有歧义的记录保留在审计中，但不进入修复机会分母。

### 3.3 根因分类

| 代码 | 根因 | 是否允许进入 ScholarGraph 修复 |
|---|---|---|
| `G1_ALIAS` | 查询术语与图实体别名/描述未正确连接 | 是 |
| `G2_RELATION` | 需要的实体关系缺失、方向不当或路径不可用 | 是 |
| `G3_RANKING` | 有有效图路径，但过滤或排序未选中候选 | 是 |
| `G4_GRAPH_DATA` | 文档、实体、关系或 text-unit 回链存在可修复质量问题 | 是，需先修数据完整性 |
| `N1_CORPUS_GAP` | gold 不在冻结 198 篇语料 | 否，另立定向语料方案 |
| `N2_FULLTEXT_ONLY` | 标题和摘要不足，只有全文能支持发现或判断 | 否，交给 DocuMind/搜索链路 |
| `N3_SOURCE_MISMATCH` | 论文身份、版本或 gold 来源不一致 | 否，先修来源与标注 |
| `N4_BOUNDARY_AMBIGUOUS` | 越界、意图不清或无法形成可靠 gold | 否 |

### 3.4 P0 决策门禁

在最多 50 个连续 eligible 子问题内，同时满足以下条件才得到 `GO_IMPLEMENT`：

- 至少 8 个按 3.2 节确认的 B5 漏检；
- 至少 6 个 gold 位于冻结语料内的 B5 漏检机会；
- 至少 4 个属于 `G1-G4`，且覆盖至少两类图侧根因；
- 所有纳入记录均能复现 B5 配置、候选和 gold 身份；
- 没有通过回改 M8 数据、压低 B5 或结果后挑题获得机会样本。

30 个样本尚不足但仍可能达到条件时为 `COLLECT_MORE`；50 个样本后仍不足则为
`NO_GO_INSUFFICIENT_NEED`，停止 ScholarGraph 代码修改。若 `N1_CORPUS_GAP` 占主导，只能
另行申请 20-50 篇定向语料扩展，不自动扩大到 1000 篇；若 `N2_FULLTEXT_ONLY` 占主导，
应优化 DocuMind/搜索路由，而不是修改图算法。

## 4. M9-P1：单根因最小修复

P1 只有在 P0 `GO_IMPLEMENT` 且项目所有者再次确认后才开始。ScholarGraph 根据占主导且
有证据支持的一类根因选择一个 treatment，例如确定性别名归一、关系/邻接修正或候选排序
修正。一次实验只改变一个主要能力，已知漏检集合只用于诊断、开发和回归测试。

- B5 实现、top-k、语料和边界不变；
- B7 保留全部 B5 seed，每题最多追加 1 个 GraphHint；
- 每个新增候选必须有稳定、有效、可回链的 `GraphPath` 和原因代码；
- 默认只读现有索引、零网络、零模型、零付费；
- 如需重新抽取关系、重建索引或增加语料，先提交单独的时间/费用/风险方案；
- 算法、阈值、测试和配置冻结后才允许进入前瞻评测。

## 5. M9-P2：前瞻 Gate A

正式评测使用算法冻结后出现的下一批连续 eligible 子问题，不能复用 P0 已知漏检作为
holdout。先收集 30 个；若 B5 语料内漏检机会少于 6 个，可连续扩展到最多 50 个。
Gold 在揭示 B5/B7 条件前按同一规则确认，题集、顺序、语料、算法和评分代码均以哈希冻结。

### Gate A1：完整性

- source ref 与 GraphPath 有效率 100%；
- boundary 零候选，B5 seed 不被替换；
- 重复运行候选、路径和排序签名一致；
- 正式 Parquet 运行前后哈希一致；
- 无模型、付费调用或索引写入。

### Gate A2：候选收益

- 至少存在 6 个 gold 在语料内的 B5 漏检机会，否则结论为 `INCONCLUSIVE`；
- B7 至少补回 3 个，且补回率不低于机会样本的 50%；
- 补回覆盖至少两个预注册查询分层，不能由单题或单模板决定；
- 全部前瞻样本的 candidate recall 严格高于 B5，question coverage 不下降；
- B7 precision 相对 B5 下降不超过 0.10；
- 所有新增误检、失败路径和未补回机会逐题公开。

任一门禁失败即 `NO_GO / keep_disabled`。样本机会不足不是正结果，也不能通过继续挑题转换
为 `GO`；只能冻结为 `INCONCLUSIVE`，重新评估是否存在真实产品需求。

## 6. M9-P3/P4：Evidence 与最终质量

### P3：DocuMind Evidence Gate

只有 Gate A 通过并再次取得人工确认后，ScholarTrace 才处理 B7 新增且 gold 有效的候选：

- 解析公开全文身份并闭合 `source_sha256`；
- 经 DocuMind ingest/retrieve、Validator 和 Verifier 形成全文 Evidence；
- 至少 2 个新增候选形成有效 Evidence；
- 前瞻题集的 Evidence 覆盖或 Claim 支持率严格高于 B5；
- 跨论文污染、stale index、hash 或定位错误继续 fail closed。

P3 调用数和人民币上限按 Gate A 实际补回候选数计算，运行前另行报价和申请；规划阶段的
授权不包含任何模型或付费调用。

### P4：盲审与产品 Gate

- B5/B7 使用完全相同的强模型、Prompt、Evidence 预算和报告长度；
- 预注册 0-4 分盲审量表，映射和原始答案保存在私有 `agent/`；
- B7 配对均分必须严格提高，胜题数多于负题数；
- 不允许出现新的关键事实错误、错误引用或安全边界回退；
- 单独报告延迟、GPU 时间、失败率、Token 和参考成本。

只有 P0-P4 全部通过并经人工复审，才讨论 Shadow Mode 或受控在线接入；否则 ScholarGraph
继续默认关闭。

## 7. 两仓职责

### ScholarTrace

- 定义并收集连续真实子问题、人工 gold 和根因审计；
- 冻结 B5 输入、前瞻题集、Evidence 和盲审门禁；
- 在 Gate A 通过后负责 DocuMind、Validator、Verifier 和最终质量评测；
- 决定是否允许 ScholarGraph 进入主流程。

### ScholarGraph

- 对已确认的语料内漏检提供只读路径诊断；
- 只针对通过 P0 的一类图侧根因实现最小修复；
- 输出候选 ID、标题、GraphPath、分数解释和确定性审计；
- 不生成最终答案、不把摘要升级为 Evidence、不自行选择真实题集。

## 8. 交付物与当前闸门

计划交付物及当前状态如下：

1. `docs/M9_REAL_MISS_PROTOCOL.md`：本协议，已完成；
2. 私有连续采样账本、人工审核修订链和公开脱敏 Schema：工具已完成；
3. `evaluation/reports/m9_p0_status.json`：13 个真实任务共采集 39 条连续观察，`30/30`
   eligible 已全部审核；已确认 10 条 `G3_RANKING`、1 条 `G1_ALIAS`、19 条 ambiguous，另 9 条
   按边界排除，决策为 `GO_IMPLEMENT`；
4. P0 根因分布和最终决策：已完成；11 条语料内图侧漏检覆盖两类根因，主导根因为
   `G3_RANKING`；
5. 条件性 ScholarGraph 修复、测试和算法冻结：已完成；独立 B7 在 P0 已知 G3 开发集上
   由 B6 1/10 提高到 4/10，算法清单 SHA-256 为
   `e673f2d7f405f1fec2741c4e36490fc47f7b4d35cb838853519ab1fb2df5ae58`；
6. P2 预注册、私有账本、Gold 冻结回执和 B5/B7 条件快照工具：已完成；
7. 前瞻 Gate A 历史账本登记样本：`50/50` eligible，另有 `1` 条边界排除；Gold 和条件快照均为 `50/50`，Gold 全部为 ambiguous；来源限制见后续补充；
8. Gate A 复审：Gate A1 完整性通过，但 0 个 B5 miss opportunity，按停止规则结论为 `INCONCLUSIVE`；
9. Evidence 与最终质量报告：未启动；仅当未来独立评测满足 Gate A 才能重新申请。

P0 阶段结论为 **PASS WITH NOTES**。工具、隐私边界和冻结 B5 快照已通过自动化与实际
198 篇索引只读 smoke；13 个真实任务产生的 30 条 eligible 观察已全部审核，确认 11 条可计入
门禁的图侧漏检（10 条 `G3_RANKING`、1 条 `G1_ALIAS`），另有 19 条 ambiguous。初始窗口、
冻结语料机会、图侧可修复漏检、confirmed miss、两类图侧根因、账本链和快照复现条件全部
满足，因此 P0 为 `GO_IMPLEMENT`。该结论只允许在项目所有者再次确认后提出一个以
`G3_RANKING` 为主的最小 P1 treatment；它不是正增益结论，也不启用 ScholarGraph。

P1 阶段结论为 **PASS WITH NOTES**。ScholarGraph `04f5327` 保留 B5 top-3、198 篇语料、
一跳和每题最多 1 个 GraphHint，只用查询相关性 + GraphPath 特异性重排最终候选。锁定容器
219 tests、改动文件 Ruff、旧 B6 稳定签名、正式六表只读性和脱敏边界均通过。P0 已知开发
补回率为 0.40，低于 P2 的至少 0.50 门槛且参与过算法设计；P1 只完成算法冻结，不能据此
宣称正增益。详细闸门见 `docs/M9_P1_FREEZE_REVIEW.md`。

P2 已获批并完成基础设施准备。预注册清单固定 47 个 P0/M8 排除哈希和批次级盲法：初始
30 条 Gold 全部冻结前不得揭示 B5/B7；机会不足时扩展批次必须收满 50 条后再揭示。正式
198 篇索引 fixture smoke 已验证跨仓条件 Schema、B5 seed、单 GraphHint、GraphPath、boundary、
确定性和 Parquet 只读性，但 fixture 不计入门禁。当前历史账本状态为 `50/50` eligible、`50/50`
Gold 和条件快照；Gate A 为 `INCONCLUSIVE`，因为 0 个 B5 miss opportunity。ScholarGraph 继续
默认关闭，未进入 Evidence Gate；操作手册和最终复审见 `docs/M9_P2_RUNBOOK.md`、
`docs/M9_P2_GATE_A_REVIEW.md`。

## 规划基线

2026-08-31 的规划检查仅冻结连续采样、Gold 审核、根因分类、诊断集与前瞻集隔离、分级门禁及停止条件；当时未采集真实查询、建立 Gold、修改算法或索引，也未运行 DocuMind、模型、网络查询、付费评测或在线 Consumer。这一规划状态不替代后续版本的实际实施和评测记录。

## 后续证据范围补充（2026-09-16）

本协议的自然连续采样、Gold 审核及盲法条款是预注册要求，不自动证明历史实施已满足全部要求。
M9-P2 的 51 条账本观察中，50 条当时标记为 eligible 且 Gold 均为 ambiguous，机器门禁仍为
INCONCLUSIVE。后续有界抽核发现部分任务创建事件和逐题审核依据无法由保存材料充分确证，
故不能将其整体称为已验证的自然连续生产样本。工程链完整性不等于来源真实性或 Gold 语义正确。
详见 [M9-P2 最终复审](M9_P2_GATE_A_REVIEW.md)的后续来源抽核与解释限制。
本补充不改变 P0/P1 结果、原协议门槛、旧 Gold 或冻结机器报告，也不证明图检索有正收益或负收益。
