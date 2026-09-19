# M9-P2 前瞻 Gate A 最终复审

> 日期：2026-09-04
> 结论：**INCONCLUSIVE**
> ScholarGraph：`keep_disabled`；未进入 DocuMind Evidence Gate

## 1. 复审范围

本复审覆盖算法冻结后按真实样本标签登记的观察、Gold 冻结、B5/B7 条件快照、Gate A1 完整性和 Gate
A2 候选收益计算。历史采集器按标签与排除哈希处理既有题集及 fixture；这些检查不独立证明样本来源，后续抽核限制见第 6 节。
本轮没有修改 B5、B7、冻结语料或评分门禁，也没有启动索引写入、模型、DocuMind 或付费调用。

## 2. 冻结结果

| 项目 | 结果 |
| --- | --- |
| 历史账本登记观察 | 51 条（当时标记为真实样本，来源限制见第 6 节） |
| 当时标记为 eligible | 50 条 |
| 边界排除 | 1 条 `fulltext_required` |
| Gold 冻结 | 50/50，全部 `ambiguous` |
| B5/B7 条件快照 | 50/50 |
| B5 miss opportunity | 0 |
| B7 recovery | 0 |
| 可评分 confirmed record | 0 |
| 公开报告 | `evaluation/reports/m9_p2_status.json` |
| 报告 SHA-256 | `8632ddf31f51f3f6ef0480917f5bf9dbe40b3db9b830047939ebd5f44138e76d` |

## 3. 门禁结果

Gate A1 的 capture/gold/condition 链、Gold 先于条件、B5 seed 保留、单 GraphHint、一跳
GraphPath、source ref、确定性重放、boundary 零候选、正式 Parquet 只读和零外部调用均通过。

Gate A2 没有可评分分母：50 条 Gold 均被登记为 ambiguous，没有已确认的冻结语料
候选，因此账本中没有可计分的 B5 漏检机会。预注册规定在最多 50 条 eligible 后机会仍少于 6 条时冻结为
`INCONCLUSIVE`。这不是正增益，也不是负增益，不能据此启动 Evidence 或产品评测。

## 4. 停止与后续边界

本轮按停止规则结束连续采样，不继续为了制造机会而挑题、改题、扩充 198 篇语料或回改
Gold。ScholarGraph 保持默认关闭。未来若产品需求仍要求验证图候选收益，必须另立独立题集、
语料/全文方案和预算，并重新取得阶段批准；不得把本轮 `INCONCLUSIVE` 改写为 `NO_GO` 或
`GO_EVIDENCE_GATE`。

Evidence Gate、盲审、线上 Shadow Mode 和付费质量评测均未启动。

## 5. 复审证据

- 机器状态：`evaluation/reports/m9_p2_status.json`；其输出由账本 `status` 命令独立重算，
  哈希一致；
- 协议与停止规则：`docs/M9_REAL_MISS_PROTOCOL.md`、`docs/M9_P2_RUNBOOK.md`；
- 私有逐题问题、Gold 审核备注、回执和完整 GraphPath：忽略的 `agent/m9-p2/`，不进入公开
  产物或版本库。

## 6. 后续来源抽核与解释限制（2026-09-16）

原有数值、冻结 JSON、Gold 和门禁结果保持不变。后续对初始批次和扩展批次各固定抽取三条，
只读核对保存的任务数据库、事件及审核材料：两条有任务创建记录，但停在待批准状态，
没有研究运行产物；四条在保存的任务库中未找到对应创建记录或事件。未找到记录不证明
从未在其他环境创建，但现有材料不足以将整批描述为已经验证的自然连续生产样本。

扩展批次存在模板化任务与审核备注；部分初始记录则保留了具体论文及输入哈希的审核依据。
不能把两者混为一谈，也不能把代理审核或本次再判断表述为独立人工盲审。
六条摘要级再判断未发现足以推翻原 ambiguous 标注的直接证据；这不证明全部 Gold 正确，
也不构成图检索失败率。哈希链、来源回链及条件快照的工程完整性不等于采样来源与标注语义有效。

本批没有可评分分母，JSON 中空分母默认的 recall=0、precision=1 不应解释为质量百分比。
“寻找研究图检索增益的论文”与“用图检索寻找某主题论文”是不同任务，不能外推其结论。
因此仍为 INCONCLUSIVE，默认关闭；未来新实验需要可追溯需求、可判定 Gold 与独立评审，
不通过回改本批题目或分数制造收益。
