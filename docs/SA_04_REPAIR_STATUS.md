# SA-04 修复轮次状态

> 历史记录：本文件描述对应轮次；当前实现、状态与评价见 [核验消融结果](VERIFICATION_ABLATION_RESULTS.md)。
状态日期：2026-09-21
历史状态：`completed`（pilot）

## 第一批补修状态

| ID | 状态 | 证据 | 说明 |
|---|---|---|---|
| 04-F01 | `completed` | `evaluation/reports/sa_04_history_correction.json` | 已知成功成本 `1.447155 CNY` 与两次失败未知成本分开；17 次 inference 与目录预检分开；旧归档保留 |
| 04-F02 | `completed_offline` | `verification_ablation/runner.py`、`verification_ablation/provider.py`、`tests/test_sa_04_provider_adapter.py` | unknown usage 会阻止后续 dispatch；shared budget 与发送前 reserve guard 已实现；Verifier/Report usage 分开 |
| 04-F03 | `completed_offline` | `verification_ablation/models.py`、`verification_ablation/runner.py`、`run_sa_04_verification_ablation_pilot.py` | 请求前 intent、请求后状态、skipped/unknown 和 checkpoint attempts 已接入；缓存复用不重发已完成 Verifier |
| 04-F04 | `completed_offline` | `verification_ablation/provider.py`、`tests/test_sa_04_provider_adapter.py` | Prompt/Schema/状态/引用合同独立；错误分类保留安全诊断，不保存 response body |
| 04-F05 | `completed_offline` | 合成输入测试、`prepare_sa_04_pilot.py` | 默认 SA-03/04 单测不读取 `agent/`；真实题集只由显式本地预检入口加载 |
| 04-F06 | `completed_offline` | `evaluation/reports/sa_04_repair_round_preflight.json` | 12 条成功 Verifier 可作为候选缓存复用；旧报告/失败报告不复用；修复轮次预计仅 4 次报告请求 |
| 04-F07 | `completed` | `evaluation/reports/sa_04_current_luna_max_repair_v9.json` | cc-switch 当前 Luna、reasoning=max 下 4/4 报告与 12/12 Verifier 成功 |
| 04-F08 | `completed` | 本文、`docs/SA_04_PILOT_RESULT.md`、公开结果和 attempt archive | 费用、attempt、配置、隐私和回归均验收；仅覆盖 pilot |

## 已知实测历史

- 初始与一次受控重试共 17 次 inference 尝试；成功报告 3 次、失败报告 2 次。
- 已知成功报告成本 `0.523275 CNY`，已知 Verifier 成本 `0.923880 CNY`，已知成功调用成本合计 `1.447155 CNY`。
- 两次失败报告的精确 usage/成本未知，不能继续把 `1.247775` 或 `2.163675` 称为完整总额/完整上界。
- 修复轮次已完成；正式集使用独立轮次和预算。

## 离线验证

- SA-03/SA-04 合成与 MockTransport 测试：`14 passed`。
- 新增 provider adapter 的状态漂移、非法 JSON、timeout、usage 和错误诊断检查通过。
- Ruff、strict Mypy、Python 编译通过。
- F06 repair-round preflight：0 次调用，Verifier 缓存候选为 `true`，新轮次预期 0 次 Verifier + 4 次报告。

## 当前实现

后续复核发现共享发送前预算与重试累计仍有缺口，现已通过请求级账本修复；以上为历史轮次状态，当前验证见 [核验消融结果](VERIFICATION_ABLATION_RESULTS.md)。
