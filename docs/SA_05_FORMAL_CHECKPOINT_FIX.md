# SA-05 Formal Checkpoint 修复

> 历史记录：本文件描述对应轮次；当前实现、状态与评价见 [核验消融结果](VERIFICATION_ABLATION_RESULTS.md)。
状态日期：2026-09-22
修复状态：离线完成，需独立配置新 formal round

## 根因

首次 formal 入口直接调用 `VerificationAblationRunner`，没有传入 `on_attempt` / `on_checkpoint`，所以 runner 在 unknown usage 后停止时，没有可恢复的 formal checkpoint。该运行不能被安全重放，也不能把未归档调用写成 0。

## 修复

- 新增 `FormalCheckpointRecorder`：保存 manifest/config hash、run ID、attempt intent、attempt 状态、rows 和 usage。
- 请求前写入 `intent`；Verifer/Report 完成后更新为 `succeeded`、`failed`、`unknown` 或 `skipped`。
- formal 入口接入 `existing_rows`、`existing_attempts`、`on_attempt` 和 `on_checkpoint`；恢复不重置预算，不自动重发未知操作。
- 新增合成输入回归，验证 intent 先于 row 落盘，重载 checkpoint 后 rows/attempts/hash 完整恢复。

## 验证

- `tests/test_sa05_formal_checkpoint.py`：通过。
- SA-03/SA-04/SA-05 checkpoint 与 Provider adapter 相关回归：通过。
- Ruff、strict Mypy、Python 编译：通过。
- 没有发起新的 Provider 请求。

## 下一步

上次 formal 运行的未知调用仍不能恢复，因此不能直接 `--resume`。需要基于修复后的入口重新冻结 manifest、额度和当前 Luna Provider 配置，确认预算与显式付费开关后，才可开始新的 formal round。

当前修复后的无调用预检已完成：`evaluation/reports/sa_05_formal_preflight.json`，10 道 formal、56 次 Verifier + 20 次报告、76 次预期请求、shared hard cap 24 CNY，preflight calls=0。此前未知调用不纳入新轮次，正式重跑使用独立 run ID 和预算。
