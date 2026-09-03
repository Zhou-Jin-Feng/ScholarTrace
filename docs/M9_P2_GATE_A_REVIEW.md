# M9-P2 前瞻 Gate A 最终复审

> 日期：2026-09-04
> 结论：**INCONCLUSIVE**
> ScholarGraph：`keep_disabled`；未进入 DocuMind Evidence Gate

## 1. 复审范围

本复审覆盖算法冻结后的连续真实样本、Gold 冻结、B5/B7 条件快照、Gate A1 完整性和 Gate
A2 候选收益计算。P0 已知漏检、M8 holdout、fixture 题和合成题均未计入本次正式结果。
本轮没有修改 B5、B7、冻结语料或评分门禁，也没有启动索引写入、模型、DocuMind 或付费调用。

## 2. 冻结结果

| 项目 | 结果 |
| --- | --- |
| 真实观察 | 51 条 |
| eligible | 50 条 |
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

Gate A2 没有可评分分母：50 条 Gold 均无法依据冻结摘要和权威元数据形成明确的冻结语料
候选，因此不存在 B5 漏检机会。预注册规定在最多 50 条 eligible 后机会仍少于 6 条时冻结为
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
