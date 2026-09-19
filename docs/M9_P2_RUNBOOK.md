# M9-P2 前瞻 Gate A 操作手册

> 历史账本状态：登记 `51` 条观察，其中 `50` 条当时标记为 eligible、`1` 条边界排除；Gold `50/50` 和条件快照已冻结。样本来源与审核限制见下文。
> 当前决策：Gate A `INCONCLUSIVE`；ScholarGraph 继续 `keep_disabled`

## 1. 评测边界

预注册要求 P2 只接收 ScholarGraph P1 算法冻结时间之后自然发生的 ScholarTrace 研究子问题。P0 的
39 条记录和 M8 的 8 条 holdout 已通过问题 SHA-256 排除，不能通过改写大小写、空白或
Unicode 形式再次纳入。合成题和 fixture 只能测试工具，不能计入正式门禁。

预注册清单位于 `evaluation/m9/p2_preregistration.json`。它固定 30/50 样本停止规则、五个
查询分层、B5/B7 与语料身份、47 个排除哈希、指标定义和 Gate A 阈值。任何修改都会使
采集器因清单 SHA-256 不一致而 fail closed。

## 2. 盲法顺序

正式初始批次必须按以下顺序执行：

1. 从真实 Research Task 原始需求自然拆分独立子问题，按发生顺序 `capture`；
2. 仅依据冻结 OpenAlex 权威元数据确认或否决 Gold，执行 `gold` 并保存回执；
3. 30 个 eligible 子问题及其 Gold 全部冻结前，禁止运行 B5 或 B7 条件快照；
4. 批次冻结后一次性生成全部 B5/B7 快照，再逐条 `condition`；
5. 机会数少于 6 时才允许盲采扩展批次，并且必须收满 50 条、冻结全部 Gold 后再揭示。

任一条件结果揭示后，已有记录的 Gold 不得修订。原始问题、任务来源、审核备注、回执和
完整 GraphPath 只保存在忽略的 `agent/m9-p2/`。

## 3. ScholarTrace CLI

私有 capture 输入示例：

```json
{
  "schema_version": "1.0",
  "source_event_id": "task:m6:<stable-id>:subproblem-01",
  "sample_origin": "real",
  "question": "<真实研究子问题>",
  "eligibility": "graph_eligible",
  "eligibility_reason": "within_frozen_corpus_scope",
  "stratum": "relation_bridge"
}
```

```powershell
uv run --python 3.11 --frozen python scripts/manage_m9_p2.py capture `
  --input agent/m9-p2/inputs/<event>.json `
  --public-report evaluation/reports/m9_p2_status.json
```

Gold 输入沿用 P0 的 `gold_candidate_ids`、`gold_in_frozen_corpus_ids` 和一对一
`gold_basis`，但 record ID 为 `m9-p2-NNNNNN`，命令为：

```powershell
uv run --python 3.11 --frozen python scripts/manage_m9_p2.py gold `
  --input agent/m9-p2/gold/<record>-r1.json `
  --receipt agent/m9-p2/receipts/<record>.json `
  --public-report evaluation/reports/m9_p2_status.json
```

## 4. ScholarGraph 条件快照

只有状态达到 `READY_FOR_CONDITIONS` 后，才把私有问题和对应 Gold 回执放入 ScholarGraph
工作树的 `agent/m9-p2/`，并在锁定容器中运行：

```powershell
$formalOutput = (Resolve-Path '<ScholarGraph-data-root>/corpora/formal/workspace/output').Path

docker run --rm `
  --volume "${PWD}:/project" `
  --volume "${formalOutput}:/formal-output:ro" `
  --workdir /project `
  --entrypoint python `
  graphrag-project:3.1.2 `
  -m src.m9_p2_snapshot `
  --output-dir /formal-output `
  --question-file /project/agent/m9-p2/questions/<record>.json `
  --gold-freeze /project/agent/m9-p2/receipts/<record>.json `
  --snapshot /project/agent/m9-p2/snapshots/<record>.json
```

快照器验证 B7 算法清单、198 篇六表、B5 seed 保留、单 GraphHint、一跳 GraphPath、重复
运行确定性、4 条 boundary 零候选及索引只读性。它不调用网络、模型、DocuMind 或付费 API。

将快照放回 ScholarTrace 私有目录后执行：

```powershell
uv run --python 3.11 --frozen python scripts/manage_m9_p2.py condition `
  --input agent/m9-p2/snapshots/<record>.json `
  --public-report evaluation/reports/m9_p2_status.json
```

## 5. 停止结论

- `GO_EVIDENCE_GATE`：Gate A1 全部通过，至少 6 个机会、3 个补回、恢复率至少 50%、
  覆盖两个分层、recall 严格提高、coverage 不下降且 precision 降幅不超过 0.10；
- `NO_GO`：完整性或候选收益任一门禁失败，保持默认关闭；
- `INCONCLUSIVE`：扩展到 50 条后仍少于 6 个机会，不能解释成正结果；
- `GO_EVIDENCE_GATE` 也不自动启动 P3，仍需项目所有者另行批准 DocuMind 回放和预算。

历史公开状态为 `evaluation/reports/m9_p2_status.json` 的 `INCONCLUSIVE`：账本登记观察 `51` 条，
其中 `50` 条 eligible、`1` 条 `fulltext_required` 边界排除；`50/50` Gold 和 `50/50` B5/B7
条件快照均已冻结，50 条 Gold 全部为 `ambiguous`，因此没有可评分的 B5 miss opportunity。
Gate A1 完整性、确定性、边界、Parquet 只读和零外部调用门禁全部通过；Gate A2 因机会数为 0
无法评分，不能解释成正增益或负增益。公开报告 SHA-256 为
`8632ddf31f51f3f6ef0480917f5bf9dbe40b3db9b830047939ebd5f44138e76d`。

按预注册停止规则，P2 在 50 条 eligible 后冻结为 `INCONCLUSIVE` 并停止，不继续采样、不修改
ScholarGraph、不启动 DocuMind Evidence Gate、不产生模型或付费调用。工具 fixture 与正式索引
smoke 仍只构成工程证据，不构成前瞻增益证据；详细复审见 `docs/M9_P2_GATE_A_REVIEW.md`。

## 6. 来源标签与后续抽核

sample_origin=real 是历史输入标签，不是自动验证过的生产任务来源。任务创建记录、
实际研究执行和自然发生的需求应分别核验；模板化备注不替代逐题 Gold 依据。
后续有界抽核发现部分来源链与审核依据不能由保存材料充分确认。
本批不能作为已验证的自然连续生产样本，也不能用于推断图检索正负收益。
详见 [最终复审](M9_P2_GATE_A_REVIEW.md)的后续来源抽核与解释限制。
本手册的采样要求仍是协议要求，并非证明所有旧记录已经满足要求。
