# SP-01～SP-05 工程验证清单

验证状态：完成（含限制）\
日期：2026-09-23\
范围：固定问题集上的简单基线、完整核验、选择性核验、结果审计和离线复现。

## 1. 验证目标

在相同问题、Evidence、Claim 和输出约束下验证三件事：强语义核验是否产生可解释的质量收益；选择性核验能否保留该收益并降低调用量；失败、未知和恢复状态能否保持可追溯。

## 2. 固定输入与运行身份

- 问题：8 个。
- Claim：42 条。
- 预先标注关键要点：每题 3 个，共 24 个。
- 模型：`gpt-5.6-luna`。
- Provider：本轮正式运行使用的 cc-switch Luna 配置；该冻结运行身份不代表当前活动配置。
- 协议：Responses，非流式。
- reasoning：`high`。
- 上下文：紧凑上下文。
- 运行时配置指纹写入 `evaluation/reports/sp_04_comparison_summary.json`。

`max` 仅在简单请求上完成预检；困难请求出现 HTTP 524，流式路径未收到终止事件。正式比较不将 `max` 结果与 `high` 混写。

## 3. 方案与结果

| 阶段 | 验证内容 | 结果 |
|---|---|---|
| SP-01 | 单次报告简单基线 | 8/8 成功；所有 Claim 保持 `unverified` |
| SP-02 | 新问题、Evidence、Claim 与关键要点冻结 | 8 个问题、42 条 Claim、24 个关键要点 |
| SP-03 | 确定性选择性核验规则 | 选中 5 条，跳过 37 条；无模型判断 |
| SP-04 | 三方案对照与跳过内容审计 | 24 份报告；选择性候选未通过质量门禁 |
| SP-05 | 专项成果、案例和复现入口 | 已完成 |

三方案结果：

| 方案 | 逻辑请求 | 加权关键要点覆盖 | 无支持论断 | 采用状态 |
|---|---:|---:|---:|---|
| 简单基线 | 8 | 91.67% | 2 / 42 | 仅保留为显式未核验场景的边界方案 |
| 完整核验 | 50 | 93.75% | 0 / 39 | 正式路径 |
| 选择性核验 | 13 | 93.75% | 2 / 42 | 实验路径，未采用 |

选择性核验的质量门禁失败原因是：相对完整核验增加 2 条无支持论断，超过预先设定的 `+1` 上限。该结果不得表述为“等质量降本”。

## 4. Claim 与报告审计

- 42 条 Claim 均完成来源判定。
- 跳过强核验的 37 条 Claim 全部审计，未被升级为已支持状态。
- 完整核验移除 2 条证据绑定不充分的论断，同时误拦 1 条正确的包级缺失陈述。
- 每方案 24 个关键要点均完成覆盖判定。
- 最终报告、Claim 状态、Evidence 引用和运行账本分别保存。

## 5. 资源与恢复账本

- 逻辑方案资源：简单基线 8 次、完整核验 50 次、选择性核验 13 次。
- 实际执行账本：成功 106 次、历史未知 1 次、尝试 107 次，包含 35 次历史重放；当前 full/selective attempt 均无未决请求。
- 本地已测参考费用：1.0947525 CNY。
- 未知请求预留：0.063 CNY。
- 计入未知预后的保守参考上限：1.1577525 CNY。
- Provider 实际账单：未知。
- 简单基线：仅有聚合归档，无逐请求 attempt journal。
- unknown 字段：当前 attempt unknown 为 0；恢复前历史 unknown 为 1。历史 unknown 属于 full verification，selective verification 的历史计数为 0。
- 检查点预算迁移：full checkpoint 的迁移已执行；selective checkpoint 的 `migrated=false` 表示其清单指纹已匹配当前预算策略，无需迁移。
- 历史恢复限制：历史 unknown 与恢复成功状态分别由不可变快照和最终归档保留，但恢复时沿用了同一 attempt ID；归档没有独立 successor ID 或 parent link，不能事后重建该关联。当前实现已修复并由回归测试覆盖，未产生新的 identity collision。

逻辑方案资源与实际执行账本分别保存在 `evaluation/reports/sp_04_comparison_summary.json` 和 `evaluation/reports/sp_04_three_scheme_review.json`，不得相互替代。

## 6. 离线复现入口

2026-09-23 的零网络恢复预览使用当前活动 Provider 身份。该身份与冻结 SP-04 运行不匹配：baseline 因 `provider_hostname` 不匹配而不可复用，full/selective 检查点因 manifest hash 不匹配而不可复用。预览记录 `network_requests_sent=0`；这不构成新的运行授权。

```powershell
.\.venv\Scripts\python.exe scripts\build_sp04_review.py
.\.venv\Scripts\python.exe scripts\run_sp04_comparison.py --preview --max-cost-cny 10 --ccswitch-provider-id "<matching-codex-provider-id>" --reasoning-effort high --compact-context --resume-dir "agent/verification-ablation/SP-04-comparison/20260922T161810Z"
```

第二条命令为零网络预览，只读取 checkpoint 和归档，不派发 Provider 请求。Provider ID 必须对应本机可用且与冻结 manifest 匹配的 Codex 配置；配置不匹配时结果不可复用。

## 7. 结果边界

- 固定 8 个问题、单轮运行，不支持统计显著性结论。
- 来源审计由执行环境辅助完成，不是独立人工盲评。
- 判定范围限于供给 Evidence 片段，不是论文全文审阅。
- `high` 是正式运行配置；未验证 `max` 的困难请求与流式终态。
- M4 工作流保持不变，选择性候选不接入默认报告路径。

## 8. P1 决策边界

本轮 P0 已完成，当前结论只覆盖固定的 8 个问题和单轮运行。P1 只有在同时满足以下条件时才启动：

- 建立未接触的新题集，并使用独立 fingerprint 和预先固定的质量门禁。
- 至少对 `unsupported`、`partially_supported` 和误拦案例引入一次独立或盲化来源复核。
- 明确区分 development 选择集与 final 验证集，不用新结果回填或改写冻结结论。
- 有新的 Provider 调用预算和可审计的资源账本。

若缺少新增预算、独立复核或新题集，保持当前固定单轮结论，不启动 P1，不通过扩展题集或改写门槛制造泛化结论。
