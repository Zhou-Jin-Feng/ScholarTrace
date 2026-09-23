# SP-01 简单基线

验证状态：通过（含限制）\
日期：2026-09-22\
范围：固定 Evidence 与 Claim 输入上的单次综合基线入口、配置、资源记录和脱敏输出。

## 1. 目标与边界

本阶段建立一个不加入多阶段研究编排、不执行独立语义核验的单次综合方案。基线读取与完整方案相同的冻结 Evidence 和 Claim 数据包，每道问题只发起一次报告请求，并保留来源身份、确定性校验、引用白名单和未核验状态。

本阶段只完成离线 Fixture 验证，没有发起模型或外部 Provider 请求。因此，本阶段证明入口、输入身份、状态隔离、资源记录和脱敏边界，不证明模型生成质量，也不提供与完整方案的质量差异结论。

## 2. 实施内容

| 内容 | 入口或文件 |
|---|---|
| 单次综合基线模型、运行器和资源记录 | `src/scholartrace/verification_ablation/simple_baseline.py` |
| Fixture 运行入口 | `scripts/run_sp01_simple_baseline.py` |
| 行为测试 | `tests/test_sp01_simple_baseline.py` |
| 公开配置 | `evaluation/seeds/sp_01_simple_baseline_manifest.json` |
| 公开脱敏结果 | `evaluation/reports/sp_01_simple_baseline_fixture.json` |
| 私有原始归档 | `agent/verification-ablation/SP-01-simple-baseline/pilot_archive.json` |

## 3. 基线合同

- 输入来自 SA-02 冻结输入；问题身份、输入哈希和 Evidence 身份必须与 manifest 一致。
- 每道问题仅执行一个报告请求，不执行 Verifier 调用。
- 所有 Claim 保留实验专用 `unverified` 状态，不转换为 `supported`。
- 报告配置、上下文长度、输入范围和来源身份均写入 manifest。
- 原始报告和 Claim/Evidence 内容只写入 `agent/` 私有归档；公开结果只保留哈希、计数、状态和资源摘要。
- 运行失败仍保留逐题行，不静默删除失败结果。

## 4. Pilot Fixture 结果

| 指标 | 结果 |
|---|---:|
| Pilot 问题 | 2 |
| 报告请求 | 2 |
| Verifier 请求 | 0 |
| 模型调用 | 0 |
| Provider API 调用 | 0 |
| 参考费用 | 0 CNY |
| 成功行 | 2 |
| 失败行 | 0 |
| 公开原始报告 | 否 |
| 公开 Claim 文本 | 否 |
| 公开 Evidence 引文 | 否 |

Pilot 使用 `sa02-pilot-01` 和 `sa02-pilot-02`，共覆盖 12 条 Claim 和 11 条 Evidence。两道问题的 Claim 均保持 `unverified`，结果仅用于确认入口和记录合同。

## 5. 验证

- `tests/test_sp01_simple_baseline.py`：3 passed。
- 新增入口 Ruff：通过。
- 新增入口 strict Mypy：通过。
- Fixture 运行：通过，2 条结果全部 `succeeded`。
- 公开结果脱敏检查：通过。
- 当前全仓离线检查：705 tests passed，Ruff、Mypy、compileall 和 `git diff --check` 通过。

## 6. 限制与后续

1. Fixture 不代表真实模型输出质量；Provider pilot 需要独立核对模型可用性、结构化输出、费用和调用额度。
2. 本阶段没有改变正式研究工作流，也没有改变报告门禁。
3. 阶段二下一步为 SP-02：冻结 6～10 个新问题，默认 8 个，覆盖普通问题与跨论文综合/限制核对问题，并根据阶段一 `INCONCLUSIVE_CEILING` 结果固定一个专项假设。
4. 新问题不得用于反向调整基线规则；规则调整只能使用旧题开发。
