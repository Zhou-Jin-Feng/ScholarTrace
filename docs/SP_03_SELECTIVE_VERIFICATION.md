# SP-03 选择性核验候选

验证状态：通过（含限制）\
日期：2026-09-22\
范围：一个有界选择变量、候选运行器接缝、选择计划、状态保持和离线回归。

## 1. 候选规则

规则 ID：`sp03-selective-verification-v1`\
规则指纹：以 `evaluation/seeds/sp_03_selection_plan.json` 为准。

对 Claim 逐条执行确定性选择，不增加模型判断。满足以下任一条件时进入强核验候选集：

- Claim 类型为 comparison 或 inference；
- Claim 来源为跨论文综合；
- Claim 存在反证 Evidence；
- Claim 绑定多个 Evidence；
- Claim 重要性为 critical。

未被选中的 Claim 不会被标记为已支持，统一保持 `unverified`，并在结果中保留选择理由和题目身份。

## 2. 实施内容

| 内容 | 文件 |
|---|---|
| 选择规则与审计模型 | `src/scholartrace/verification_ablation/stage_two_selection.py` |
| 候选运行器选择接缝 | `src/scholartrace/verification_ablation/runner.py` |
| 选择计划入口 | `scripts/build_sp03_selection_plan.py` |
| 选择计划行为测试 | `tests/test_sp03_selection.py`、`tests/test_sp03_runner_selector.py` |
| 公开选择计划 | `evaluation/seeds/sp_03_selection_plan.json` |
| 私有逐 Claim 计划 | `agent/verification-ablation/SP-03/selection_plan.json` |

## 3. 离线结果

- 题目：8 个。
- Claim 总数：42 条。
- 选择强核验：5 条。
- 跳过强核验：37 条。
- 候选选择比例：约 11.90%。
- 模型调用：0 次。
- Provider API 调用：0 次。
- 正式 M4 工作流：未修改。

该比例只是规则在本题集上的观察，不是质量或成本收益结论。SP-04 必须审计全部 37 条被跳过 Claim，不能只查看 5 条入选 Claim。

## 4. 验证

- 选择计划测试：2 passed。
- 候选运行器状态测试：1 passed。
- 新增入口 Ruff：通过。
- 新增入口 strict Mypy：通过。
- Fixture 候选运行验证：V-on 仅对入选 Claim 调用强核验，未选 Claim 保持 `unverified`；V-off 调用数为 0。

## 5. 限制与下一阶段

本阶段只验证确定性选择、状态标记、输入绑定和运行器行为，没有发起真实模型请求。因此不能说明选择性核验的质量、成本或耗时收益。

下一阶段 SP-04 需要在固定的 8 个新问题上运行三方案对照：简单基线、原完整核验方案和本候选方案，并对全部跳过内容执行人工审计。真实 Provider 运行涉及新增调用范围、实际费用和当前模型配置，必须在派发前完成授权核对。
