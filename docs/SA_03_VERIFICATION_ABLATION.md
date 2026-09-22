# SA-03 独立核验消融入口与结果记录

> 历史记录：本文件描述对应轮次；当前实现、状态与评价见 [核验消融结果](VERIFICATION_ABLATION_RESULTS.md)。
状态日期：2026-09-21
阶段结论：`PASS WITH NOTES`
执行模式：仅 Fixture/离线；真实模型和外部 Provider 调用为 0

## 1. 交付范围

SA-03 建立了独立的 V-on/V-off 实验包，不修改正式 M4 Reliability Pipeline、`Verification` 合同或 `VerificationReportGate`。

代码入口：

- `src/scholartrace/verification_ablation/models.py`：冻结输入、实验配置、逐题结果、用量和私有归档模型。
- `src/scholartrace/verification_ablation/runner.py`：共同确定性校验、V-on/V-off 编排、预算检查、失败记录和断点恢复。
- `src/scholartrace/verification_ablation/reporting.py`：`agent/` 私有原始产物与公开脱敏产物的路径门禁和序列化。
- `scripts/run_sa_03_verification_ablation_fixture.py`：零费用 Fixture 入口。
- `tests/test_sa_03_verification_ablation.py`：SA-03 行为与边界测试。

## 2. 两个条件的边界

两条件都先重新加载 SA-02 冻结输入，并重跑 `EvidenceValidator`，比较确定性 Validation 工件；输入内容、Evidence 身份、Evidence 白名单、报告配置指纹、长度上限和自动重试策略必须一致。

| 条件 | 语义核验 | 实验输入处理 | 正式 M4 Gate |
|---|---|---|---|
| V-on | 调用独立 `VerifierRunner` | supported 保留，partial/conflicted 显式标记，unsupported 排除 | 不调用正式 Gate；使用独立实验 disposition |
| V-off | 跳过 Verifier，调用数必须为 0 | 所有 Claim 显式标记 `unverified` 并保留 | 不构造正式 `Verification` 或 `ReportGateResult` |

`unverified` 是实验专用状态，故意不加入正式 M4 枚举。这样 V-off 不可能通过类型转换伪造 `supported`，也不会成为产品工作流的绕过开关。

## 3. 记录与恢复

每个问题、每个条件保存一条私有记录，包含：

- 运行 ID、条件、题目 ID、split、冻结输入哈希、Evidence 身份哈希、配置哈希；
- Claim/Evidence 计数、disposition 状态计数、Verifier 调用数；
- 报告状态、报告哈希、报告上下文哈希；
- 模型/Provider 调用、Token、耗时、参考成本；未知 usage 保留为 `null`，不替换成 0；
- 失败阶段、错误码和私有错误信息。

私有归档只能写入 `agent/verification-ablation/SA-03/`；公开归档只保留哈希、状态、计数和用量摘要，不包含 Claim 文本、Evidence quote、原始报告、Verifier 原因、人工要点或凭据。恢复只接受同一运行 ID、输入哈希和配置哈希；已有失败行不会被静默覆盖，缺失行才会执行。

## 4. 零费用 Fixture 验证

执行：

```powershell
.\.venv\Scripts\python.exe scripts\run_sa_03_verification_ablation_fixture.py
```

公开产物：`evaluation/reports/sa_03_verification_ablation_fixture.json`。本次结果：

| 项目 | 结果 |
|---|---:|
| 题目 | 12 |
| 条件记录 | 24 |
| V-on Fixture Verifier 调用 | 68 |
| V-off Verifier 调用 | 0 |
| 模型调用 | 0 |
| Provider API 调用 | 0 |
| 质量结论 | 不提供 |

Fixture 只证明入口、状态隔离、记录、哈希、失败关闭和私有/公开边界，不承担语义质量评分，也不能替代 SA-04 的 pilot。

## 5. 自动验证

- SA-03 行为测试：`6 passed`。
- SA-03 加 M4/M6 核验和强模型接口定向测试：`25 passed`。
- 新增包 Ruff：通过。
- 新增包 strict Mypy：通过，无 issues。
- 新增代码 Python 编译：通过。
- Fixture 12 题完整覆盖，24 行结果，公开隐私扫描通过。
- 输入漂移在首次 Verifier/Report 调用前失败；失败和未知 usage 都生成显式记录。
- 未执行 Git commit/push、付费模型调用或外部服务调用。

## 6. 限制与下一步

1. Fixture 的 Verifier 和 Report Generator 都是确定性替身，不能证明真实语义质量。
2. SA-03 尚未接入真实 Provider，也没有核对当前账户价格、倍率或模型可用性；这些必须在 SA-04 付费 pilot 前重新复核并显式启用付费调用。
3. 当前报告生成器是 SA-03 独立接口；M6 B3/B4 的报告 Prompt/Schema 没有被冒充为本协议的共同配置。
4. SA-04 需要在 2 道 pilot 上使用同一冻结输入，记录真实失败、Token、耗时和参考成本，再冻结 formal 配置。

该入口完成离线合同验证；后续 Provider 试运行结果见 SA-04 历史记录。
