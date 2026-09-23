# SP-04 三方案对照预检

状态：历史预检；三方案已完成，结果见 `SP_04_THREE_SCHEME_COMPARISON.md`\
日期：2026-09-22（结论更新于 2026-09-23）\
范围：固定新题集上的简单基线、原完整核验方案和选择性核验候选方案的运行前资源与授权核对。

## 1. 当前输入

- 问题：8 个。
- Claim：42 条。
- 候选强核验 Claim：5 条。
- 候选跳过 Claim：37 条。
- 阶段二题集和选择规则均已冻结。

## 2. 预期方案资源

| 方案 | 报告请求 | 强核验请求 | Provider 请求合计 | run ID |
|---|---:|---:|---:|---|
| 简单基线 | 8 | 0 | 8 | `sp04-simple-baseline-production-v1` |
| 原完整核验方案 | 8 | 42 | 50 | `sp04-full-verification-production-v1` |
| 选择性核验候选 | 8 | 5 | 13 | `sp04-selective-verification-production-v1` |
| 合计 | 24 | 47 | 71 | 分方案隔离 |

当前预检不估算费用。实际费率、倍率、输入上限、输出上限和余额必须以派发前的当前 Provider 配置为准，不能直接复制历史 SA-05 费用。只读 `GET /v1/models` 预检于 2026-09-22 失败，因此当前模型目录、能力和实时费率仍未确认。

## 3. 历史预检配置

- Provider 来源：当时使用的 cc-switch Luna 配置；Provider ID 和主机属于本机环境身份，不作为当前配置声明。
- 计划模型：`gpt-5.6-luna`。
- 计划协议：Responses。
- 计划 reasoning：`max`。
- 自动重发：关闭。
- 未知 usage：停止派发并保存检查点。
- 正式 M4 工作流：不修改。
- 当前外部调用：0 次。

上述模型和协议是 2026-09-22 的历史预检目标，不代表当前活动配置，也不代表已完成本轮费率或余额核对。

## 4. 必须完成的授权核对

在派发任一 Provider 请求前，必须确认：

1. 当前 Provider 地址、模型名称、协议和结构化输出能力。
2. 当前实际费率、账户倍率、hard cap、可用余额或调用额度。
3. 本轮 71 次请求是否在授权范围内；失败和未知请求是否计入资源上限。
4. 三方案的独立 manifest、run ID、检查点和输出目录。
5. 未知结果不自动重发，显式重试另行记录并重新核对预算。

## 5. 预检产物

- 机器可读预检：`evaluation/reports/sp_04_comparison_preflight.json`
- 阶段二清单：`docs/SP_VALIDATION_CHECKLIST.md`
- 阶段一至阶段三成果：`docs/SP_01_SIMPLE_BASELINE.md`、`docs/SP_02_NEW_QUESTIONS.md`、`docs/SP_03_SELECTIVE_VERIFICATION.md`


## 6. Luna max 直接预检

2026-09-22 改用当前 Codex 的 cc-switch Luna 配置后，在 `gpt-5.6-luna`、Responses、reasoning=`max` 下执行 1 次有界报告 smoke，返回 HTTP 200 并生成有效报告。输入 6,309 tokens，输出 883 tokens，参考成本 0.0174105 CNY；Provider 实际账单仍不可见。

## 7. 当前结论与下一步

本文件保留运行前预检内容。三方案已完成并形成正式结果；`max` 困难题预检出现 HTTP 524，正式运行使用 `high`，两者不得混合表述。

执行结果、资源对账、跳过 Claim 审计和采用边界见 `docs/SP_04_THREE_SCHEME_COMPARISON.md`。
