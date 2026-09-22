# SA-04 Pilot 实测结果与阻断

> 历史记录：本文件描述对应轮次；当前实现、状态与评价见 [核验消融结果](VERIFICATION_ABLATION_RESULTS.md)。
状态日期：2026-09-21
阶段状态：`blocked`
执行模型：当前目录可见的 `gpt-5.6-sol`（历史候选 `gpt-5.6-terra` 当前不可见）
Provider 协议：Responses

## 1. 实际执行范围

本轮只执行 SA-02 冻结的两道 pilot，未进入 formal：

| 条件 | 题数 | Verifier 尝试/成功 | 报告尝试/成功 |
|---|---:|---:|---:|
| V-on | 2 | 12 / 12 | 2 / 2 |
| V-off | 2 | 0 / 0 | 2 / 1 |
| 合计 | 2 | 12 / 12 | 4 / 3 |

总请求数为 16，符合 pilot 配置上限。V-off 没有 Verifier 调用；两条件使用相同冻结输入和报告 adapter。

## 2. 资源结果

- 报告参考成本：`0.323895 CNY`。
- Verifier 参考成本：`0.923880 CNY`。
- 总参考成本：`1.247775 CNY`。
- 含 reserve/失败保守上界：`2.163675 CNY`。
- 本轮硬上限：`8 CNY`；未触发预算停止。
- Provider 实际账单/账户倍率：未从响应获得，保持 `unknown`。
- Verifier 输入/输出 Token：`57,302 / 715`。
- 报告输入/输出 Token：`14,627 / 1,161`。
- V-on Verifier 墙钟累计约 `241.514s`；报告累计约 `34.307s`。

## 3. 失败行

`sa02-pilot-01 / V-off` 的报告请求返回了无法通过独立 `AblationReportDraft` 严格校验的结构化结果，记录为：

- 状态：`failed`
- 阶段：`execution`
- 错误类型：`ProviderInferenceError`
- 原始响应 body：未保存到公开产物；私有归档也只保留脱敏错误信息
- 自动重试：无

其余三条条件记录成功。由于 V-off pilot 有一条失败，当前不能把 formal 配置标为冻结，也不能据此做质量优劣结论。该失败可能与单题输出形状、模型遵循状态字段或偶发结构化输出不稳定有关；现有脱敏记录不足以断言唯一根因。

## 4. 产物与隐私

- 公开结果：`evaluation/reports/sa_04_verification_ablation_pilot.json`。
- Provider 目录预检：`evaluation/reports/sa_04_provider_preflight.json`。
- 私有完整归档和 checkpoint：`agent/verification-ablation/SA-04/`。
- 公开产物不含 API Key、Authorization、Claim/Evidence 原文、Provider response body 或原始报告。

## 5. 停止与下一步

SA-04 已完成本轮授权范围内的 pilot 执行，但被一条 V-off 严格 Schema 失败阻断。当前停止，不进入 SA-05。

如需继续，下一步只能是重新明确授权一次受控重试/修复验证：最多补跑失败的 `sa02-pilot-01 / V-off` 一条报告请求，仍使用同一输入、模型、Prompt、Schema 和预算；不能重跑已成功的 15 条请求，也不能借此授权 formal。

## 6. 故障与兼容性历史

初始 pilot 有一条 V-off 报告未通过严格 Schema 校验。单条重试前曾发生模型目录预检失败，未派发 inference；随后一次报告重试仍失败。初始和重试共 17 次 inference 尝试。

后续兼容性检查经历模型目录连接失败、严格 Responses 请求失败和 HTTP 524 网关超时。目录可见、交互客户端可用均不代表独立 adapter 的严格输出合同可用。失败和未知 usage 分别保留在 `evaluation/reports/sa_04_*` 历史记录中；未将缺失 usage 记为零。

## 10. 最终修复轮次验收

在固定 `gpt-5.6-luna` 并设置 `reasoning_effort=max` 后，修复轮次 v9 完成：

- 4/4 报告成功；
- 12/12 Verifier 成功；
- 8/8 attempt 记录完成，失败 0；
- 总参考成本：`2.092770 CNY`；
- reserve/失败保守上界：`4.634250 CNY`；
- Provider 实际账单：`unknown`；
- 本轮仅覆盖 pilot，正式集另行归档。

公开最终结果：`evaluation/reports/sa_04_current_luna_max_repair_v9.json`。本轮可证明 SA-04 的实验入口、计量、失败关闭、报告合同和当前 Luna Provider adapter 已完成一次有效 pilot；不证明核验对报告质量的因果收益。
