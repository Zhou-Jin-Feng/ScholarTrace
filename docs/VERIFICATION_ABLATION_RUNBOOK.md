# 核验消融操作手册

## 离线检查

在项目虚拟环境中运行：

```powershell
python -m pytest tests/test_ablation_dispatch_budget.py tests/test_ablation_review.py tests/test_sa05_formal_checkpoint.py
python scripts/run_m6_demo.py
python scripts/validate_verification_ablation.py
```

合成测试不读取真实论文数据。M6 使用确定性数据验证事件、证据与导出路径，不发出真实模型请求。

## 历史结果复核

具备冻结私有归档时执行：

```powershell
python scripts/build_verification_ablation_review.py
```

该命令仅读取现有数据并生成脱敏汇总、私有质量视图和映射，不调用模型。评价输入必须覆盖所有题内 Claim、最终 finding 和关键点；缺失、重复或来源哈希变化时拒绝汇总。私有输入及原文不随仓库发布。

查看 `evaluation/reports/verification_ablation_review.json`，依次检查逐题标签、覆盖、移除项和资源记录。第 6 题展示参考文献不能支撑具体攻击机制的边界；第 9 题展示核验未移除 finding 以及未来方案表述仍需限定的情况。第 10 题的成本限定语在质量视图中保留。

历史模型结果是回放，确定性测试是合成数据；二者均不能称为当前在线服务验证。

## 显式在线运行

正式入口使用环境或 `.env` 中的 `SCHOLARTRACE_API_BASE_URL` 与 `SCHOLARTRACE_API_KEY`，模型与预算由 manifest 冻结。凭据不得提交或打印。命令须显式传入 `--approve-paid-calls`；恢复还需 `--resume`，未知重发需明确选择 `--retry-unknown`。这些开关不能替代实际运行额度确认。

已有归档时入口拒绝启动同路径新轮次；缺检查点时 `--resume` 拒绝运行。新模型/Prompt/输入必须使用独立轮次和存储位置，不混入历史结果。

## 检查点与费用

每次请求记录 intent、发送状态、结果及 usage。历史失败不会因重试替换而消失。总执行费用和最终结果逻辑成本分别展示；未知 usage 预留是预算假设而非实际账单上界。无法保守估计 Token 的历史未知请求会阻断后续派发。

当前恢复验证采用合成输入和 MockTransport；没有在本轮重跑在线 DocuMind 联调或 Provider。协议偏离及其他实验限制见 [结果说明](VERIFICATION_ABLATION_RESULTS.md)。
