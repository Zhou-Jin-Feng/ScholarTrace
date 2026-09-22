# SA-04 Pilot 预检与预算配置

> 历史记录：本文件描述对应轮次；当前实现、状态与评价见 [核验消融结果](VERIFICATION_ABLATION_RESULTS.md)。
状态日期：2026-09-21
状态：`prepared`
本次调用：0 次模型调用，0 次 Provider API 调用，0 次外部网络调用

## 1. 冻结对象

SA-04 pilot 只使用 SA-02 已冻结的两道 pilot：

| 题目 | 核验前 Claim | Evidence |
|---|---:|---:|
| `sa02-pilot-01` | 4 | 3 |
| `sa02-pilot-02` | 8 | 8 |
| 合计 | 12 | 11 条唯一 Evidence |

数据集指纹保持：

`c2e017a11df5677d1363a27a10f3361f00d5478d8b7b6a8a52c878185b48dddf`

逐题输入哈希和原始 Claim/Evidence 仍只从 `agent/verification-ablation/SA-02/` 读取；不能替换为历史 M6 `sg-eligible-02` 或历史 Gate 清洗结果。

## 2. 计划运行规模

| 条件 | Verifier | 报告 | 预计总模型调用 |
|---|---:|---:|---:|
| V-on | 12 | 2 | 14 |
| V-off | 0 | 2 | 2 |
| 合计 | 12 | 4 | 16 |

运行顺序按预注册约定：`sa02-pilot-01` 为 V-off → V-on，`sa02-pilot-02` 为 V-on → V-off。两条件共享输入、Evidence 身份、确定性校验、报告 Prompt/Schema、模型、Responses 协议、长度和无自动重试规则；差异只有语义核验及其筛选/标记。

## 3. 配置指纹

- 候选模型：`gpt-5.6-terra`；模型是否当前可用尚未在线复核。
- Provider 协议：Responses；当前账户是否支持本 adapter 的严格 Schema 尚未复核。
- SA-02 报告 Prompt hash 已与独立真实 adapter 对齐。
- 报告 Schema 使用实际 `AblationReportDraft.model_json_schema()` 的 SHA-256，不再使用 Fixture 名称字符串 hash。
- V-on/V-off 使用独立 adapter；M6 B3/B4 Report Generator 和 ScholarGraph 不进入本实验。
- 运行时指纹包括脱敏 Provider hostname、结构化输出模式、timeout、最大响应大小和预算 scope；API Key 永不写入 manifest、checkpoint 或报告。

## 4. 预算与成本口径

初始预算配置：

- 规划参考上限：3 CNY；
- 失败关闭硬包络：5 CNY；
- 参考费率仍沿用历史本地记录，不能视为当前价格或 Provider 账单；
- Provider 实际 billing 字段若不可见，报告必须区分 actual billing=`unknown`、usage 推算参考成本和 reserve 参考成本上界；
- Verifier 与报告失败调用的 attempted/reserved/actual/unknown usage 分开记录；未知值保持 `null`。

## 5. 已完成的无调用准备

- `scripts/prepare_sa_04_pilot.py` 已校验两题覆盖、Claim/Evidence 计数、输入指纹、Prompt hash 和 adapter Schema hash。
- 新增 `OpenAICompatibleAblationReportGenerator`，不复用 M6 B3/B4 Prompt/Schema；支持 Responses/Chat Completions 的严格结构化输出、Evidence ID 白名单、无 retry/fallback、幂等键和 timeout 分类。
- Runner 已记录 Verifier attempted/successful calls、Token、reserve/actual cost、耗时和失败行；支持 shared/per-condition budget scope。
- `httpx.MockTransport` 离线测试覆盖成功、状态漂移、timeout 和非法 JSON；不产生外部调用。
- 预检公开产物位于 `evaluation/reports/sa_04_pilot_preflight.json`，不含 API Key、Claim 原文、Evidence quote 或 Provider response。

## 6. 运行要求

真实运行需配置 Provider、确认当前模型和费率并显式启用付费调用。pilot 与 formal 使用独立 manifest、预算和归档；预检本身不派发模型请求。当前运行说明见 [操作手册](VERIFICATION_ABLATION_RUNBOOK.md)。
