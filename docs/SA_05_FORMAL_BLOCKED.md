# SA-05 Formal 阻断记录

> 历史记录：本文件描述对应轮次；当前实现、状态与评价见 [核验消融结果](VERIFICATION_ABLATION_RESULTS.md)。
状态日期：2026-09-21
状态：`blocked`

## 现象

SA-05 formal 运行按 10 道题、V-on/V-off、Luna max 配置开始执行。Verifier/Report runner 在出现 unknown cumulative usage 后按 fail-closed 规则停止了后续派发，但 formal 脚本没有接入 SA-04 已有的 `on_checkpoint` / `on_attempt` 持久化回调。

结果：

- 没有生成 formal 最终 archive；
- 没有生成可靠的逐题公开报告；
- 当前工作目录没有 formal checkpoint；
- 已派发的部分请求、成功/未知边界和实际成本无法从本轮归档恢复；
- 不能安全重跑，也不能把形式上“没有归档”解释为 0 次调用。

## 影响

SA-05 的 formal 结果、A/B 审阅包和成本结论均未形成，内容比较缺少完整配对输入。此前 SA-04 的完整 pilot 结果仍然有效，不受本次 formal runner 阻断影响。

## 根因

正式脚本复用了 SA-04 runner，但没有把请求前 intent、请求后 attempt 状态和逐行 checkpoint 接入正式执行入口。SA-04 已有的 attempt ledger 只在 SA-04 pilot 脚本使用，formal 脚本遗漏了这一接缝。

## 恢复条件

1. 为 formal 入口接入每次请求前 intent、请求后状态和私有原子 checkpoint。
2. 让 Verifier/Report usage、reserve/actual/unknown 和 shared budget 在 checkpoint 中持久化。
3. 增加中途异常恢复测试，证明成功子步骤不会重复发送，未知步骤不会自动重发。
4. 重新生成 formal manifest，重新确认当前 Luna Provider、reasoning=max、24 CNY hard cap 和剩余额度；不能假设本轮未知调用已经计入或免费。
5. 修复后重新申请/确认 formal 运行范围，再开始新的 formal round；不复用本次没有可靠账本的运行结果。

当前不提供质量结论，不进入 SA-06。

## 修复进展

已完成 formal runner 的离线修复：新增 `FormalCheckpointRecorder`，formal 入口现在在每个 Verifier/Report 条件派发前保存 intent，在完成/失败/unknown 后保存 attempt、usage、row 和 manifest/config hash；恢复时加载已有 rows/attempts，不会重置预算或自动重发未知操作。

新增回归 `tests/test_sa05_formal_checkpoint.py` 已通过；当前没有重跑上次缺少账本的 formal 请求。后续需要重新生成 formal manifest，使用独立调用范围和预算。

## Formal 第二次运行状态

修复 checkpoint 后重新启动 formal，checkpoint 已保存 17/20 条条件结果：16 条成功、`sa02-formal-09 / V-off` 1 条 `ProviderInferenceError`，另有 3 条尚未派发。attempt 记录共 34 条，失败行和未执行行均保留，未自动重跑成功条目。

该轮次尚未形成完整配对集。公开运行阻断摘要：`evaluation/reports/sa_05_formal_runtime_blocked.json`。下一步先诊断该 formal 报告合同/Provider 响应，根据账本仅恢复失败/未执行条件；不能把 16 条成功结果拼成 formal 完成。
