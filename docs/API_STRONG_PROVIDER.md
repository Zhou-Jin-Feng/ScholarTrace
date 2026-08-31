# 自定义 api-strong Provider 接入准备

## 当前状态

ScholarTrace 已实现 OpenAI-compatible `PlanGenerator`，并完成一次 `gpt-5.6-terra` 有界在线兼容性 smoke。生产 `api-strong` Profile 仍保持关闭：本次只证明 Responses 与严格结构化输出兼容，不证明规划质量，也没有取得 Provider 实际账单或倍率。M6 确定性 Demo 不需要 Key，也不会调用该 Provider。

已确认的自定义 Provider 信息：

| 项目 | 值 | 证据 |
|---|---|---|
| Provider | mxou.ai | 用户提供的自定义 OpenAI-compatible Provider |
| Base URL | `https://www.mxou.ai` | 项目根 `.env`（Key 不记录） |
| 模型列表端点 | `GET https://www.mxou.ai/v1/models` | 2026-08-30 使用本地 Key 成功 |
| OpenAPI 文档 | 未确认 | 早期旧域名 `/openapi.json` 匿名请求返回 HTTP 404 |
| 公开模型页 | `https://www.mxou.ai/models` | 当前域名，未在本轮重新核对 |

项目早期对 `https://sui-xiang.com/v1/models` 的匿名请求返回 401；当前配置已切换到 `https://www.mxou.ai`，带 Key 的只读请求成功。项目新增的 `scripts/list_custom_provider_models.py` 只执行该 GET 请求，错误信息不包含响应正文或 Key。

## 账户目录快照

2026-08-30 的账户级目录返回 17 个模型：

```text
codex-auto-review
gpt-5.2
gpt-5.3-codex
gpt-5.3-codex-spark
gpt-5.4
gpt-5.4-mini
gpt-5.4-openai-compact
gpt-5.5
gpt-5.5-openai-compact
gpt-5.6-luna
gpt-5.6-luna-compact
gpt-5.6-openai-compact
gpt-5.6-sol
gpt-5.6-sol-openai-compact
gpt-5.6-terra
gpt-5.6-terra-openai-compact
gpt-image-2
```

该响应没有返回 `owned_by`、创建时间、上下文窗口、价格或能力字段；因此这只是“账户可见模型清单”，不是能力或成本验证。

## 无密钥准备

复制 `.env.example` 的变量到项目根目录的未跟踪 `.env`，或只放在当前进程环境中。探针的配置优先级是 CLI 参数 > 进程环境变量 > `.env` > 默认值；Key 不会打印，也不会写入报告：

```powershell
Copy-Item .env.example .env
# 编辑 .env 后执行；默认只读 GET /v1/models
uv run python scripts/list_custom_provider_models.py
```

也可以使用一次性环境变量，或显式参数覆盖配置（不建议把 Key 放进可被 shell 历史记录保存的命令行）：

```powershell
$env:SCHOLARTRACE_API_KEY = Read-Host "Enter provider API key"
uv run python scripts/list_custom_provider_models.py --base-url "https://www.mxou.ai"
```

该脚本会把根地址规范化为 `/v1/models`；如果传入的地址已经以 `/v1` 结尾，不会重复拼接。没有 Key 时脚本直接退出，不发出网络请求。`.env` 只支持简单的 `KEY=VALUE` 和可选引号，不做变量插值；格式错误会 fail closed。

## 模型选择规则

账户级目录只能确认模型 ID；是否支持 Chat/Responses 兼容接口、结构化输出、最大输入/输出 Token 和供应商实际倍率仍需分别验证。不能仅凭模型名称推断上下文窗口或能力。

官方 OpenAI 文档可作为同名模型的参考基线，但自定义 Provider 可能裁剪上下文、改变价格或不完整实现接口，最终以 Provider 的模型详情和实测为准。

| 官方模型参考 | 官方上下文 | 官方标准输入/输出价格（每百万 Token） | 暂定用途 |
|---|---:|---:|---|
| GPT-5.6 Sol | 1,050,000；最大输入 922,000 | $4 / $20 | 质量上限，先用于小样本上界 |
| GPT-5.6 Terra | 1,050,000；最大输入 922,000 | $2 / $12 | 首选的质量/成本平衡候选 |
| GPT-5.6 Luna | 1,050,000；最大输入 922,000 | $0.20 / $1.20 | 高并发或成本敏感候选 |
| GPT-5.2 | 400,000；最大输出 128,000 | $1.75 / $14 | Provider 公开号码中已看到的候选，需确认实际可用 |
| GPT-5.1 | 400,000；最大输出 128,000 | $1.25 / $10 | 较低成本的强模型候选 |

当前选择 `gpt-5.6-terra`，理由是它属于最新 GPT-5.6 家族，预期在规划、证据核验和最终合成之间提供较好的质量/成本平衡；它已通过 Provider 的一次 Responses 严格结构化 smoke。`gpt-5.6-sol` 作为未来质量上界候选，`gpt-5.6-luna` 作为成本/延迟敏感候选，`gpt-5.6-*-compact` 暂不优先，因为目录没有说明压缩版本的上下文、质量和价格。`codex`、`codex-auto-review` 和 `gpt-image-2` 不适合作为 ScholarTrace 的通用文本强模型。一次 smoke 仍不能替代质量评测。

官方参考：

- [OpenAI Models](https://developers.openai.com/api/docs/models.md)
- [OpenAI Model guidance](https://developers.openai.com/api/docs/guides/latest-model.md)
- [OpenAI Pricing](https://developers.openai.com/api/docs/pricing.md)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs.md)

## 结构化在线 smoke

执行命令：

```powershell
uv run python scripts/run_m6_api_strong_smoke.py `
  --model gpt-5.6-terra `
  --max-output-tokens 900 `
  --max-cost-cny 5
```

2026-08-30 的结果保存在 `evaluation/reports/m6_api_strong_smoke.json`：

| 指标 | 结果 |
|---|---:|
| 协议 | Responses API |
| API 尝试 | 1，无重试/回退 |
| 严格 JSON Schema | 通过 |
| 请求/响应模型 | `gpt-5.6-terra` / `gpt-5.6-terra` |
| 输入/输出 Token | 5,005 / 406 |
| 总 Token | 5,411 |
| 墙钟耗时 | 20.161 秒 |
| 官方参考估算 | 0.111615 CNY |
| Provider 实际账单/倍率 | 响应未提供 |

适配器在联网前限制调用数、保守输入 Token 上界、最大输出 Token 和官方参考 CNY 预算；不自动重试，不记录 Prompt、原始回答、响应 ID 或 Key。模型只生成计划草稿字段，`task_id`、截止日期、Budget 和审批状态由代码确定。Provider 的实际倍率未知，因此 0.111615 CNY 只是按官方价格和 7.5 规划汇率计算的参考值，不是账单。

## 预算估算

实际费用按以下公式计算，并叠加 Provider 的公开倍率、汇率和重试：

```text
费用 = 输入 Token / 1,000,000 × 输入单价
     + 输出 Token / 1,000,000 × 输出单价
```

建议分层设置硬上限，而不是一次性打开完整评测：

| 阶段 | 内容 | 建议上限 |
|---|---|---:|
| 模型列表 | 1 次 GET `/v1/models` | 0 元推理预算 |
| 适配 smoke | 1-2 次结构化生成，验证接口/Schema/用量 | 5 CNY |
| B3/B4 首轮 | 6 个 eligible 问题，B3/B4 各跑一次；Coordinator、关键 Verifier、Synthesis 受限调用 | 50 CNY |
| 重复容量 | 在首轮通过后再做 3 次重复，记录 P50/P95、失败和排队 | 150-300 CNY，另行批准 |

上述是规划上限，不是账单预测。按每个任务约 8k 输入、2k 输出、每个任务 3 次强模型调用的中等假设，B3/B4 首轮约 36 次生成调用；实际费用会随上下文长度、输出长度、Provider 倍率和重试变化。当前 M0 的 10 CNY 任务上限不足以直接覆盖整轮评测，不能静默提高，必须在模型和价格核验后人工批准新的预算。

M6 实际执行使用了单独批准的更小门禁，参考成本为 8.150130 CNY。人工盲审显示 eligible 的 B4-B3 平均质量差为 0，因此没有继续申请 150-300 CNY 运行完整付费重复；M6 只补充了零付费的本地 ScholarGraph Basic 三次顺序可靠性测试。该取舍不把本地 Basic 延迟冒充完整强模型容量。

## 计费核验与生产启用条件

当前进度：

1. [x] 读取模型列表并保存脱敏的模型 ID；
2. [x] 选择 `gpt-5.6-terra`，按官方上下文和价格做参考；
3. [x] 人工批准 5 CNY smoke 预算；
4. [x] 实现 `PlanGenerator`，完成一次结构化在线 smoke；
5. [ ] 取得或确认 Provider 实际倍率/账单口径；
6. [x] 在独立预算门禁内运行固定条件 B3/B4 质量对照并完成人工盲审；
7. [x] 因 B4 无质量增益而停止扩大付费重复，保留 Provider 实际账单口径为未关闭项。

任何失败、协议漂移、上下文不一致或价格不明都保持 fail closed，不自动切换到其他模型或更高价格层级。
