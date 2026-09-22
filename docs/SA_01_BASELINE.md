# SA-01 真实链路与演示版本基线

> 历史记录：本文件描述对应轮次；当前实现、状态与评价见 [核验消融结果](VERIFICATION_ABLATION_RESULTS.md)。
状态日期：2026-09-21
阶段结论：`PASS WITH NOTES`

## 结论

本阶段选择以下版本组合：

- ScholarTrace 固定为 `v1.0.2`，提交 `3d095a79feff257b57b041de40b7b5ed73021f9e`。
- 冻结输入的核验实验以该 ScholarTrace 版本为实现基线；开发期 Fixture 不依赖 DocuMind 运行实例，正式 V-on/V-off 质量实验仍需 SA-02 冻结输入和预算、SA-04 明确调用配置。
- 真实联合演示的上游固定为 DocuMind `v3.0.0`，提交 `3a9bf0c9980f4109482f078ee697c527a99e44ae`。离线契约检查通过，但 SA-01 未执行在线 readiness、上传、检索或模型调用。
- DocuMind 当前本地工作区含未提交改动。这些改动不属于上述版本组合，也不能视为 3.1.0 或兼容证据。
- DocuMind 3.1.x 及其他 3.x 版本不在当前支持组合内；若 SA-07 需要切换，必须先明确适配范围并通过版本、契约、绑定与就绪语义测试。

## 当前能力与证据强度

| 能力 | 当前证据 | 可以说明 | 不能说明 |
|---|---|---|---|
| 确定性工作台 Demo | 本次重新运行 `scripts/run_m6_demo.py`，结果 `passed=true`，11 个事件、4 个工件、4 种导出格式 | 审批、事件、导出和终态控制流可复现 | 真实搜索、全文、语义核验或报告质量 |
| 真实研究链路 | `v1.0.2` 的 `CHANGELOG.md` 记录指定样本完成规划、检索、取全文、DocuMind、分析、远端核验、综合与报告 | 历史版本曾完成一条真实链路 | 当前环境已重新跑通；任意来源全文都可获取 |
| 真实链路结果 | 同一发布记录将结果标为 `degraded`，只使用可获取的 arXiv 全文，未启用 ScholarGraph 和引用扩展 | 系统能显式保留完成状态与限制 | 结果无边界或生产就绪 |
| M4 核验门禁 | `evaluation/reports/m4_reliability_fixture_smoke.json` 为 `passed=true`、`outcome=degraded` | 冲突、缺失引用和失败关闭控制流可验证 | Fixture 能证明语义 Verifier 质量 |
| 历史 B3/B4 对照 | 12 题公开聚合报告中 B3/B4 均分为 4.0/3.916667，eligible 增益为 0 | 既有盲审、预算、私有答案隔离和聚合机制可复用 | 该实验等同于 V-on/V-off 核验消融 |
| DocuMind 兼容性 | 对精确 `v3.0.0` 提交执行离线契约检查，Schema、有效/无效样例和未知字段拒绝均通过 | 已提交 Consumer/Provider 契约相容 | 在线服务就绪、真实检索或部署 SHA 已验证 |

默认 Demo 与真实模式必须继续分开表述。默认 Web/API Demo 使用确定性执行路径；真实模式由生产装配、授权、计量、研究流水线和模型适配器组成，需要显式配置与审批。README 中“当前可直接运行的是确定性 Demo”描述默认体验，不等于代码库没有真实生产装配入口。

## 版本与依赖分层

| 路径 | ScholarTrace | DocuMind | 模型/外部服务 | 当前状态 |
|---|---|---|---|---|
| 零费用控制流开发 | v1.0.2 | 不需要运行实例 | Fixture；不联网 | 可用 |
| V-on/V-off 输入与工具开发 | v1.0.2 | 使用冻结 Evidence，无运行依赖 | Fixture；不联网 | 可进入 SA-02/SA-03 |
| V-on/V-off pilot/正式质量实验 | v1.0.2 | 使用 SA-02 冻结 Evidence | 强 Verifier 与报告模型 | 历史基线尚未运行 |
| 真实联合演示 | v1.0.2 | v3.0.0 精确提交 | DocuMind/Ollama，必要时远端模型 | 仅离线契约通过；在线联调待 SA-07 |

冻结输入实验不等待 DocuMind 前端或未发布版本收尾。联合演示只接受上表中的精确组合，或经过单独适配和测试的新组合。

## 代码入口

| 目的 | 入口 |
|---|---|
| 确定性 Demo | `scripts/run_m6_demo.py` |
| 生产研究装配 | `src/scholartrace/delivery/production.py` |
| 研究流水线 | `src/scholartrace/delivery/research.py` |
| 确定性 Evidence 校验 | `src/scholartrace/verification/validator.py` |
| 独立语义核验 | `src/scholartrace/verification/verifier.py` |
| 正式报告门禁 | `src/scholartrace/verification/gate.py` |
| 严格报告生成 | `src/scholartrace/model_provider/report_generator.py` |
| 既有配对实验与预算记录 | `src/scholartrace/scholargraph/experiment.py` |
| 盲审与防篡改导入 | `src/scholartrace/scholargraph/blind_review.py` |

V-off 必须在后续独立实验入口中实现，不能向正式 `M4ReliabilityPipeline` 添加绕过核验的产品开关，也不能伪造 `supported` 状态。

## 证据索引

| 证据 | 身份/结果 | 用途 |
|---|---|---|
| `pyproject.toml`、`README.md`、`CHANGELOG.md` | 应用版本 1.0.2 | 当前版本和历史真实链路摘要 |
| Git Tag `v1.0.2` | `3d095a79feff257b57b041de40b7b5ed73021f9e` | ScholarTrace 实现基线 |
| DocuMind Tag `v3.0.0` | `3a9bf0c9980f4109482f078ee697c527a99e44ae` | 联合演示上游基线 |
| `scripts/verify_m2_documind_compatibility.py` | 离线检查 `passed=true`；契约 SHA-256 `06e5b06cb034a4bb179c6130b56c2f50ab8fb3cd40d90a5bc46b6fd1eda1618b` | 精确版本的离线兼容证据 |
| `artifacts/reports/m6_demo_smoke.json` | 本次确定性 Demo 通过；零模型和外部调用 | 当前可复现的工作台控制流 |
| `evaluation/reports/m4_reliability_fixture_smoke.json` | M4 失败关闭 Fixture 通过 | 核验控制流参考 |
| `evaluation/reports/m6_b3_b4_scored_comparison.json` | 12 题已评分公开聚合；不含原始回答 | 预算、运行记录和盲审机制参考 |
| `docs/M6_B3_B4_PROTOCOL.md` | 冻结输入、私有答案和预算规则 | SA-02/SA-03 协议参考 |

历史真实链路在当前仓库中以发布记录和代码/测试变更为公开证据；SA-01 不把私有原始模型回答复制到新材料，也没有将历史运行宣称为本次重跑。

## 本次验证

- `uv lock --check`：通过，72 个包的锁定解析一致。
- DocuMind `v3.0.0` 离线契约检查：通过；明确标记 Provider 工作区 dirty，未执行在线 readiness。
- 确定性 Demo：通过，未调用模型或外部 Provider。
- 核验、报告、B3/B4 与 DocuMind v3 契约定向测试：68 passed。
- 全量测试：669 passed、3 failed。3 个失败均来自 Windows 工作树中的 `evaluation/m9/p2_preregistration.json` 为 CRLF，而冻结 SHA-256 按 Git 中的 LF 字节计算。工作树原始哈希为 `77dccf923d7bb42b85b3a9ed7926fc6d74b81d09f7bc725b83a3e05653dbe7ce`；仅在内存中规范为 LF 后得到预注册常量 `2267c1fda78d98c67aad051901a76cf3fbd6a7448c5ae551dc9fe8b187f84899`。Git 索引为 LF、工作区无内容改动，因此该问题记录为本地检出 note，不改写冻结预注册文件。

## 遗留项与停止点

1. 当前环境未重新执行付费或在线真实全链路，历史 `degraded` 结论只能按冻结版本引用。
2. SA-02 需要选择 8-12 个正式问题、2-3 个 pilot，并冻结核验前 Claim、原文依据和预算。
3. SA-04 前必须明确实际模型、调用范围和额度；本文件不构成付费授权。
4. SA-07 若执行联合演示，需重新检查 DocuMind 部署 readiness、精确版本和真实检索链路。
5. Windows 全量测试的 CRLF 检出问题不影响 SA-02 的协议设计，但在宣称全量绿色前必须单独处理并复跑。

SA-01 已能区分既有能力、新实验和联合演示依赖，满足进入 SA-02 的信息条件；是否开始 SA-02 仍由人工指令决定。
