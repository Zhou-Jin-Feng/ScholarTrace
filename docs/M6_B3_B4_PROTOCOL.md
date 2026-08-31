# M6 B3/B4 配对实验协议

> 状态日期：2026-08-31<br>
> 当前状态：12 题真实付费 B3/B4、人工盲审和三次 Basic 重复测试均已完成；ScholarGraph 继续默认关闭

## 1. 实验定义

- B3：ScholarTrace Multi-Agent、DocuMind Evidence、确定性 Validator 和 Verifier 流程，ScholarGraph 完全禁用；
- B4：使用与 B3 相同的模型、Prompt 模板、论文池、逐题 Evidence 输入、预算和报告长度，仅对 eligible 问题增加 ScholarGraph Basic 摘要上下文；
- eligible 6 题必须调用 Basic；boundary 6 题必须在网络调用前 skip/reject；
- ScholarGraph 输出始终是 abstract-only 辅助上下文，不能成为全文 Evidence，也不能覆盖 DocuMind 的来源校验；
- B3/B4 每个变体只有一个 run ID，题目覆盖或条件不一致时 fail closed。

DocuMind 已在 M2 真实验收。正式实验不会让本模块重新上传论文，而是读取冻结论文池对应的已验证 Evidence 工件；执行清单逐题保存 Evidence 输入 SHA-256，运行前重新计算并拒绝输入漂移。公开报告不保存论文全文、Chunk、Prompt 或原始模型回答。

## 2. 执行清单与预算

`B3B4ExecutionManifest` 在既有跨仓 `EvaluationConditions` 之外冻结：

- 精确模型 Profile、模型标识和 Provider 协议；
- Prompt 模板 SHA-256；
- 论文池 SHA-256 和逐题 Evidence 输入 SHA-256；
- 每变体模型调用、Provider API 调用、ScholarGraph 调用、输入/输出 Token、人民币参考成本和墙钟时间上限；
- 报告字符上限、eligible/boundary 文件哈希、ScholarGraph 服务和语料清单哈希。

跨仓 `B3B4EvaluationResult` 契约保持不变，避免修改已经冻结的 ScholarGraph P1 Consumer/Provider 边界。更严格的本地条件通过 manifest hash 绑定私有运行归档、盲审映射和公开审计。

生产 `OpenAICompatibleReportGenerator` 已实现 `ReportGenerator`：Responses/Chat Completions 均使用严格 JSON Schema，调用前执行 Token、调用数和费用预检，不自动重试或切换协议；外层运行器再次累计实际回报用量并在每题后 fail closed。报告只能引用输入白名单中的 Evidence ID，ScholarGraph 上下文只作为 abstract-only 辅助信息，最终 Markdown 由确定性代码渲染。

正式输入由 `prepare_evidence_input` 从 M4 Report Gate 允许的 Claim、匹配 Verification 和 DocuMind Evidence 构造。unsupported Claim 会被剔除；缺失 Evidence、未检查引用、Gate 不安全、逐题输入 hash 漂移或上下文超限都在模型调用前失败。Fixture Generator 仍只用于零调用控制流验证，不能用于质量结论。

`evaluation/reports/m6_b3_b4_budget_estimate.json` 给出最终报告生成的参考预算：

| 估算形状 | 24 次调用参考成本 |
|---|---:|
| 早期 plan smoke 形状（5,005/406 Token） | 2.678760 CNY |
| 计划形状（15,000/800 Token） | 7.128000 CNY |
| 硬包络（30,000/1,200 Token） | 13.392000 CNY |

这些数字按公开参考单价算术估算，不是自定义 Provider 的实际账单，也不包含 Coordinator 和关键 Verifier 的额外调用。用户已批准在原建议上增加 50% 余量：一题 B3/B4 pilot 总参考硬上限 3 CNY，最多 24 次最终报告的参考硬上限 22.5 CNY。预算扩大不放宽 Evidence、输入覆盖、盲审或失败关闭门禁。

全量运行保留最多 25,000 字符的逐题 Evidence 上下文，并以 40,000 序列化字符作为 Provider 前的保守输入预检阈值。这里的字符阈值不是 Provider Token 计费口径；实际 Token 和参考成本仍按响应 usage 记录，并受 22.5 CNY 总上限约束。

## 3. 私有答案与盲审

原始 B3/B4 报告只允许写入已被 Git 忽略的 `agent/` 目录。公开运行报告只包含：

- manifest 及其 hash；
- 每题 report hash、状态、延迟、Token 下界和 ScholarGraph 调用审计；
- 每变体聚合调用数、参考成本和墙钟时间；
- 不含答案文本和 A/B 标签映射的限制说明。

盲审工具按固定种子逐题随机交换 A/B，把题目和两个候选答案写入私有 CSV，把标签映射写入另一个私有 JSON。评分只接受 0-4 整数。导入时重新校验 manifest、题集、报告 hash、完整覆盖和所有不可变 CSV 单元；任何答案、题目或 hash 被修改都会拒绝揭盲。评分成功后才调用既有 `B3B4Evaluator` 输出脱敏聚合。

## 4. 零付费验证

```powershell
uv run python scripts/run_m6_b3_b4_fixture.py
uv run pytest tests/test_m6_b3_b4_experiment.py
```

公开输出：`evaluation/reports/m6_b3_b4_fixture_infrastructure.json`。

Fixture 结果：12 题完整覆盖，6 次模拟 eligible Basic、6 个 boundary 零调用、0 次模型/付费 Provider 调用、12 行盲审表生成成功。该零成本 Fixture 本身不承担质量评分，Fixture-local 决策保持 `keep_disabled_insufficient_quality_evidence`；正式付费运行的评分结论见第 8 节。

真实私有运行完成后可重新生成盲审表并导入分数：

```powershell
uv run python scripts/prepare_m6_b3_b4_review.py `
  --private-run agent/<run>/private_run.json `
  --review agent/<run>/blind_review.csv `
  --mapping agent/<run>/private_mapping.json

uv run python scripts/import_m6_b3_b4_review.py `
  --private-run agent/<run>/private_run.json `
  --review agent/<run>/blind_review.csv `
  --mapping agent/<run>/private_mapping.json `
  --output evaluation/reports/m6_b3_b4_scored_comparison.json
```

## 5. 在线兼容复验

2026-08-30 释放显存后的有界复验成功：ScholarGraph `1.2.0` readiness 四项通过，Basic 单次请求成功，Provider 32.806 秒、HTTP 32.832 秒、总耗时 34.059 秒，答案 1,834 bytes，可见 Token 下界 19；6 个 boundary 问题均在网络前路由，0 次付费调用。该结果只关闭运行兼容性阻断，不证明 B4 相对 B3 有质量收益。

## 6. 付费 pilot 结果

2026-08-31 使用 `sg-eligible-02`、M2 三篇真实 DocuMind 全文 Evidence 和 `gpt-5.6-terra` Responses 完成一题付费 pilot：

- M4 确定性 Validator 通过 12 个 Claim；强模型 Verifier 返回 10 supported、1 partially_supported、1 unsupported，Report Gate 纳入 11 Claim / 10 Evidence；
- B3/B4 各完成 1 次报告调用，Token 分别为 9,621/703 与 9,981/811；
- B3 16.121 秒，B4 49.307 秒；B4 只调用 1 次 ScholarGraph Basic 并成功；
- 两份最终报告参考成本合计 0.430290 CNY；把首次失败尝试按保守预检上界计入后，总参考成本上界为 1.755300 CNY，低于 3 CNY；
- 原始报告、验证输入和 A/B 映射只在 `agent/`；公开 `evaluation/reports/m6_b3_b4_paid_pilot.json` 只保存 hash、用量和状态；
- 初次 B3 报告失败前的 Verifier 用量没有及时检查点，因此公开报告只能给出保守上界，不能恢复精确 Verifier Token 或实际 Provider 账单；
- 一题 pilot 的盲审表不用于默认启用决策；正式结论只来自第 8 节的完整 12 题盲审。

首次报告失败后没有重复 12 次 Verifier；恢复路径复用了已冻结的验证输入，并为报告 Schema 动态枚举允许的 Evidence ID。付费链路完成后，正式比较契约因 pilot 没有 boundary 样本而拒绝聚合；改由 pilot 专用的非质量汇总收口，没有再次调用 Provider。

## 7. 全量付费运行结果

2026-08-31 使用 `gpt-5.6-terra` Responses 完成 12 题全量执行：

- 5 个新增 eligible 问题使用 11 篇唯一公开 arXiv 全文、56 个 Claim 和 45 个 Evidence；`sg-eligible-02` 精确复用已付费 pilot 的 Verification；
- 新增 Verifier 56 次全部成功，283,974/4,151 输入输出 Token，参考成本 4.633200 CNY；
- B3/B4 各 12 次报告调用全部成功，分别使用 87,918/4,596 与 89,802/4,861 输入输出 Token；报告参考成本分别为 1.732410 与 1.784520 CNY；
- B4 的 6 个 eligible 均调用一次 Basic 并成功，6 个 boundary 均在网络前 skip/reject，ScholarGraph 调用数分别为 6 和 0；
- 新调用参考成本合计 8.150130 CNY，低于 Verifier 12 CNY和最终报告 22.5 CNY的独立上限；Provider 未返回实际账单或倍率；
- 12 行私有盲审 CSV 和独立 A/B 映射已生成；公开报告 `evaluation/reports/m6_b3_b4_paid_full.json` 不含题目、答案、Prompt、全文 Evidence 或标签映射。

首次运行在 45 个 Verifier 结果落盘后，因 `sg-eligible-05` 的安全 Evidence 上下文为 20,655 字符、超过原 20,000 字符限制而失败关闭。将本次上限提高到 25,000 字符并完成专项测试后，恢复路径复用全部 45 个结果，仅继续剩余 11 个 Verifier 和报告调用；模型、Prompt、Evidence、题集与人民币上限未改变。

全量执行通过本身不等于 B4 质量收益成立。人工评分完成后，导入器重新验证 manifest、题集、报告 hash、完整覆盖和所有不可变 CSV 单元，再生成不含原始答案或 A/B 映射的公开聚合。

## 8. 人工盲审与重复容量结论

2026-08-31 完成 12 行人工盲审并正式导入：24 个分数全部为 0-4 整数，其中 23 个为 4 分、1 个为 3 分；不可变单元和全部 hash 校验通过。脱敏报告位于 `evaluation/reports/m6_b3_b4_scored_comparison.json`。

| 指标 | B3 | B4 |
|---|---:|---:|
| 盲审均分 | 4.000000 | 3.916667 |
| P50 延迟 | 36.321667 秒 | 69.737458 秒 |
| P95 延迟 | 93.164801 秒 | 168.084500 秒 |
| 失败率 | 0 | 0 |
| ScholarGraph 调用 | 0 | 6 |

6 个 eligible 问题的 B4-B3 平均质量差为 0.000000，boundary 路由全部通过。B3 的 eligible 回答全部为 4 分，当前题集存在天花板效应；本轮能得出的结论是“没有观察到收益”，不是证明 GraphRAG 在所有接入位置都无效。默认决策为 `keep_disabled_no_clear_benefit`。

同日对冻结 ScholarGraph `1.2.0` 执行三次顺序 Basic：3/3 成功，每次一次尝试，HTTP P50/P95 为 32.826/34.2426 秒，Provider 滚动队列 P50/P95 为 0.000060/0.000064 秒，可见 Token 下界增量 57，付费调用和 Provider 报告成本均为 0。公开报告位于 `evaluation/reports/m6_scholargraph_basic_capacity.json`。这是并发 1 的单 GPU 可靠性与延迟证据，不是并发负载测试或独立 GPU 时间测量。

当前 B4 只在最终报告前提供不可引用的摘要辅助文本，不参与检索扩展、Evidence 缺口发现或证据选择。后续若验证 GraphRAG 增益，应在 M7 另设实验，把结构化图线索前移到 Search/FollowUp，并使用新的困难题集；不得回改本轮冻结问题、答案或评分。
