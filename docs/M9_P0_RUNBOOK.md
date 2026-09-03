# M9-P0 真实漏检采集手册

> 状态：真实 graph-eligible 样本 `30/30`，30 条已审，10 条 G3_RANKING、
> 1 条 G1_ALIAS、19 条 ambiguous；另有 9 条真实子问题按边界排除
> 当前决策：`GO_IMPLEMENT`；ScholarGraph 仍默认关闭，P1 尚未实施

## 1. 使用边界

P0 只观察真实 ScholarTrace 研究任务中的连续子问题，不运行 B7、不启用 ScholarGraph
Consumer、不调用 DocuMind 或模型，也不产生付费请求。`sample_origin=fixture` 仅用于测试，
永远不计入门禁。原始问题、来源事件和审核备注只保存在项目根 `agent/m9-p0/`；公开报告
不含这些字段。

私有 SQLite 账本是本地文件，不提供内容加密。采集前应确认当前 Windows 账户和磁盘访问
边界可信；不要把 `agent/` 同步到公开位置。CLI 按单操作员串行使用，不要同时启动多个
`capture` 或 `review` 进程。

## 2. 准备 B5 快照

每个 graph-eligible 问题先在 ScholarGraph 正式目录的私有 `agent/` 保存问题文件：

```json
{
  "schema_version": "1.0",
  "question": "<真实研究子问题>"
}
```

从 ScholarGraph 项目根使用锁定镜像读取正式索引；入口必须使用 Python 模块形式。Git
worktree 不共享被忽略的 `workspace/output`，因此先显式解析实际保存冻结六表的数据目录：

```powershell
$formalOutput = (Resolve-Path '<ScholarGraph-data-root>/corpora/formal/workspace/output').Path

docker run --rm `
  --volume "${PWD}:/project" `
  --volume "${formalOutput}:/formal-output:ro" `
  --workdir /project `
  graphrag-project:3.1.2 `
  python -m src.m9_b5_snapshot `
  --output-dir /formal-output `
  --question-file /project/agent/m9-p0/questions/<event>.json `
  --snapshot /project/agent/m9-p0/snapshots/<event>.json
```

脚本只接受 M8 冻结提交 `37e00cf`、198 篇语料、固定六表 hash 和 B5 top-3；运行前后
Parquet 任一变化都会失败；路径错误或空目录也会因缺少六表而失败，不能用其他实验索引
代替。生成的快照不含问题原文。将该快照复制到 ScholarTrace 的
`agent/m9-p0/snapshots/` 后再执行采集；不要把原始问题跨仓公开复制。

## 3. 追加观察

在 ScholarTrace `agent/m9-p0/inputs/` 创建 capture JSON。真实事件 ID 应来自现有任务，
同一事件重试必须保持内容一致；问题规范化后重复会被拒绝。

```json
{
  "schema_version": "1.0",
  "source_event_id": "task:<稳定任务ID>:subproblem:<序号>",
  "sample_origin": "real",
  "question": "<真实研究子问题>",
  "eligibility": "graph_eligible",
  "eligibility_reason": "within_frozen_corpus_scope",
  "stratum": "alias_bridge"
}
```

```powershell
uv run python scripts/manage_m9_p0.py capture `
  --input agent/m9-p0/inputs/<event>.json `
  --b5-snapshot agent/m9-p0/snapshots/<event>.json `
  --public-report evaluation/reports/m9_p0_status.json
```

越界问题仍按发生顺序记录，但使用 `eligibility=ineligible` 和对应原因，不附带 B5 快照；
它们不会增加 30 个 eligible 样本计数。

## 4. 人工审核

默认由项目所有者确认 gold 论文身份、是否位于冻结语料以及主根因。所有者明确授权代理
审核后，可按冻结标题/摘要和既定标准采用建议，但必须在私有 notes 记录授权，且 P0 汇总
进入 P1 前仍需所有者最终批准；这类记录不得描述为独立盲审。Gold 确认前不得查看
GraphHint 或 GraphPath。`authority_sha256` 是所用公开权威记录的规范化内容哈希，不保存
该内容本身。首次审核的 `expected_revision` 为 0，修订时使用当前 revision，旧审核不会
被覆盖。

```json
{
  "schema_version": "1.0",
  "record_id": "m9-p0-000001",
  "expected_revision": 0,
  "status": "confirmed",
  "gold_candidate_ids": ["W1234567890"],
  "gold_in_frozen_corpus_ids": ["W1234567890"],
  "gold_basis": [{
    "openalex_id": "W1234567890",
    "kind": "openalex_metadata",
    "authority_sha256": "<64位小写SHA-256>"
  }],
  "graph_path_status": "path_missing",
  "root_cause": "G1_ALIAS",
  "notes": "<私有人工说明>"
}
```

```powershell
uv run python scripts/manage_m9_p0.py review `
  --input agent/m9-p0/reviews/<record-id>-r1.json `
  --public-report evaluation/reports/m9_p0_status.json
```

## 5. 查看门禁

```powershell
uv run python scripts/manage_m9_p0.py status `
  --output evaluation/reports/m9_p0_status.json
```

- `COLLECT_MORE`：未完成初始 30 条，或 30 条后仍可在上限 50 条内继续观察；
- `GO_IMPLEMENT`：数量、根因、可复现性和账本完整性均通过，但仍需人工批准 P1；
- `NO_GO_INSUFFICIENT_NEED`：50 条完整且已审核的真实 eligible 窗口仍无足够图侧需求；
- 账本哈希链损坏或记录不可复现时保持 `COLLECT_MORE`，不得解释为需求不足。

公开报告中的 `root_cause_counts` 包含图侧和非图侧全部已确认漏检根因，以便判断应修改
ScholarGraph、定向扩语料，还是转向 DocuMind/搜索链路。
