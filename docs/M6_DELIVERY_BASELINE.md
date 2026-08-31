# M6 工作台、完整评测与交付基线

> 状态日期：2026-08-31
> ScholarTrace：`0.5.0`
> 阶段结论：`PASS WITH NOTES`

## 1. M6 交付范围

M6 将已完成的 M0-M5 工程能力装配为可运行的交付切片：Research Task API、持久事件、工件列表、Markdown/HTML/PDF/JSON 导出、React 工作台、B0-B4 证据矩阵、Compose、CI、运行手册和脱敏 Demo。

API 的确定性演示模式用于验收控制流和交付体验，不代表生产模型质量。`gpt-5.6-terra` 已通过一次有界 Responses 严格结构化兼容性 smoke，但 Provider 实际账单/倍率和真实规划质量仍未建立，因此生产 `api-strong` Coordinator、关键 Verifier 和 Synthesis 继续保持 fail closed。

## 2. B0-B4 评测矩阵

命令：

```powershell
uv run python scripts/run_m6_evaluation.py
```

输出：`evaluation/reports/m6_b0_b4_delivery_matrix.json`

| 阶段 | 状态 | 证据 | 质量证据 |
|---|---|---|---|
| B0 | validated | M0 本地模型 smoke | limited |
| B1 | validated | M1 本地 Baseline | limited |
| B2 | validated | M2 DocuMind 在线 Evidence smoke | limited |
| B3 | validated | M6 12 题正式盲审聚合 | measured |
| B4 | validated | M6 同输入盲审聚合 + 6 次 eligible Basic | measured |

矩阵保留失败分析，不将 Fixture 或历史 ScholarGraph 成绩当作 B3/B4 质量收益。正式盲审已证明链路可执行且可比较，但 eligible 的 B4-B3 平均质量差为 0、B4 延迟更高，因此 M6 继续输出 `delivery_ready_with_notes`，并明确阻止 ScholarGraph 默认启用。

## 3. Research Task API

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/health/live` | API 存活检查 |
| GET | `/api/v1/evaluation/m6` | 读取脱敏 B0-B4 矩阵 |
| POST | `/api/v1/research/tasks` | 创建等待审批的任务，支持 `Idempotency-Key` |
| GET | `/api/v1/research/tasks/{task_id}` | 查询任务状态、阶段、预算摘要和降级 |
| POST | `/api/v1/research/tasks/{task_id}/approve` | approve/modify/reject 任务 |
| GET | `/api/v1/research/tasks/{task_id}/events` | SSE 事件及 `Last-Event-ID` 补发 |
| GET | `/api/v1/research/tasks/{task_id}/artifacts` | 工件元数据和 hash |
| GET | `/api/v1/research/tasks/{task_id}/report?format=...` | 下载 JSON/Markdown/HTML/PDF |

任务元数据、事件和导出工件写入独立的 M6 SQLite 目录；默认位于 `artifacts/m6-delivery`，Compose 通过 `SCHOLARTRACE_DATA_DIR=/var/lib/scholartrace` 挂载到持久卷。中间状态不保存正文；响应通过 `X-Request-ID` 关联，结构化日志只记录方法、路径、状态和请求 ID。

## 4. React 工作台

前端位于 `frontend/`，提供：

- 研究问题输入和三种执行 Profile；
- 任务状态、阶段进度、事件/工件/证据/引用边计数；
- 成功、降级和生产门禁状态的显式标记；
- B0-B4 评测矩阵及质量证据缺口；
- Markdown、HTML、PDF 和 JSON 下载入口。

本地构建：

```powershell
Set-Location frontend
npm ci
npm run build
```

## 5. 验证证据

- M6 API、Schema、导出、评测测试：7 项通过；
- 全量 Pytest：177 项通过；
- Ruff：通过；
- strict Mypy：74 个源码文件无问题；
- `uv lock --check`：通过，72 个包；
- Frontend `npm run build`：Vite 6.4.3 构建通过；
- M6 Demo：成功，9 个事件、4 个导出工件、SSE 终态事件可见；
- api-strong 在线 smoke：Responses 一次成功，5,005/406 输入/输出 Token，20.161 秒，官方参考估算 0.111615 CNY；Provider 实际账单不可观测；
- B3/B4 人工盲审：12 题、24 个合法评分，不可变单元与 hash 校验通过；B3/B4 均分 4.000000/3.916667，eligible 平均差 0；
- ScholarGraph Basic 重复：三次顺序调用全部成功，HTTP P50/P95 32.826/34.2426 秒，无重试、无付费调用；
- Compose `config`：通过；
- Docker 镜像实际构建：受 Docker Hub token 网络阻断，尚未取得基础镜像，需在可联网环境重试。

## 6. 运行和安全边界

- Compose 只启动 ScholarTrace API 与 UI，不自动启动 DocuMind/ScholarGraph；
- `production_unavailable` 明确降级，不静默换用本地模型；
- 导出不包含 API Key、Prompt、原始模型回答、论文全文或内部路径；
- `docker compose down -v` 会删除本地任务卷，必须显式执行；
- 真实 Research Task 的通用全文 acquisition 和多用户认证不在本交付切片内；三次顺序 Basic 不代表并发负载能力。盲审已显示 B4 无增益，因此没有继续消费预算重复完整付费 B3/B4；Provider 实际账单和独立 GPU 时间仍不可观测。

## 7. 阶段结论

M6 的交付装配、可视化、导出、评测矩阵、CI、本地 Demo、api-strong 结构化适配、12 题真实付费 B3/B4、人工盲审和三次顺序 Basic 重复均已完成。阶段结论为 `PASS WITH NOTES`：B4 未取得 eligible 质量增益且延迟更高，ScholarGraph 继续默认关闭；Docker 构建仍需在能访问 Docker Hub 的环境补验，Provider 实际账单、并发负载与独立 GPU 时间不可观测，SSE 持续 tail/heartbeat、认证和多用户不在本交付切片。M6 基线已冻结，M7 尚未开始。

## 8. B3/B4 保留项关闭进展

2026-08-30 补充了 `docs/M6_B3_B4_PROTOCOL.md` 对应的零付费执行与盲审基础设施：

- 独立 manifest 冻结精确模型、Provider 协议、Prompt、论文池、逐题 DocuMind Evidence 输入、预算、题集和 ScholarGraph 语料 hash；
- B3 禁用 ScholarGraph，B4 eligible 只允许 Basic，boundary 在网络前 skip/reject；
- 原始报告和 A/B 映射只写入忽略的 `agent/`，公开报告只保存 hash、状态、延迟、Token、调用审计和聚合用量；
- 盲审 CSV 支持 0-4 整数评分，不可变单元、报告 hash、题集或 manifest 漂移时拒绝导入；
- 12 题 Fixture 通过，6 个 eligible 模拟调用、6 个 boundary 零调用、0 个模型/付费 Provider 调用；Fixture 本身不承担质量评分，正式结论来自后续完整盲审。
- 生产 `api-strong` ReportGenerator 已实现并离线验证，Responses/Chat Completions 均使用严格 JSON Schema，无自动重试或协议回退；Evidence ID 白名单、报告长度和预算超限均 fail closed；
- 真实输入准备器只接收通过 M4 Report Gate 的 Claim、Verification 和 DocuMind Evidence，剔除 unsupported Claim，并冻结逐题输入 hash；
- 最终报告生成的 24 调用参考估算为 2.678760/7.128000/13.392000 CNY；用户批准增加 50% 余量后，pilot 上限为 3 CNY、全量最终报告上限为 22.5 CNY；均非 Provider 实际账单，且不含 Coordinator/Verifier。

一次真实 Basic 重检曾因其他前台程序占用大部分 8 GiB 显存，导致 `qwen3-embedding` 冷加载 CUDA OOM。释放显存后同一有界复验已成功：readiness 四项通过，Provider 32.806 秒、HTTP 32.832 秒、总耗时 34.059 秒、答案 1,834 bytes、可见 Token 下界 19，6 个 boundary 零调用且无付费调用。

随后一题付费 pilot 通过：12 次强模型 Evidence 核验后纳入 11 Claim / 10 Evidence；B3/B4 报告分别使用 9,621/703 与 9,981/811 Token，参考成本合计 0.430290 CNY，B4 Basic 成功；包含首次失败尝试的保守总参考成本上界为 1.755300 CNY。

2026-08-31 进一步完成 12 题全量运行：其余 5 个 eligible 使用 11 篇唯一公开 arXiv 全文、56 个 Claim 和 45 个 Evidence；新增 Verifier 56 次全部成功，参考成本 4.633200 CNY；B3/B4 各 12 份报告全部成功，参考成本 1.732410/1.784520 CNY；6 个 eligible Basic 全部成功，6 个 boundary 零 ScholarGraph 调用。新调用参考成本合计 8.150130 CNY，公开脱敏审计位于 `evaluation/reports/m6_b3_b4_paid_full.json`。

人工盲审随后完成：12 行、24 个分数均通过不可变单元和 hash 校验；B3/B4 总体均分分别为 4.000000/3.916667，6 个 eligible 的平均质量差为 0，B3/B4 P50 为 36.321667/69.737458 秒，P95 为 93.164801/168.084500 秒。默认决策为 `keep_disabled_no_clear_benefit`，脱敏聚合位于 `evaluation/reports/m6_b3_b4_scored_comparison.json`。

同日对冻结 ScholarGraph 运行三次顺序 Basic，3/3 成功、每次一次尝试、HTTP P50/P95 为 32.826/34.2426 秒，Provider 滚动队列 P50/P95 为 0.000060/0.000064 秒，付费调用为 0。报告位于 `evaluation/reports/m6_scholargraph_basic_capacity.json`；它只代表并发 1 的本机单 GPU 有界可靠性。
