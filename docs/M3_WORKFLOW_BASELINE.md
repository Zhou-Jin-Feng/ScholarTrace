# M3 LangGraph Multi-Agent 编排基线

> 日期：2026-08-29
> 工程复审：PASS WITH NOTES
> 阶段状态：M3 工程验收通过，M4 尚未开始

## 1. 范围

M3 将 M2 单流程升级为可审批、可恢复、有预算的 LangGraph 编排：可注入 Coordinator、人工 `interrupt`、动态 Search Agent、`Send` 论文 Worker、SQLite Checkpoint、轻量 State、幂等 Artifact/预算 effect、持久业务事件与 SSE 补发。

本阶段不实现 M4 引用网络和独立 Evidence Verifier，不配置付费模型，不调用 ScholarGraph，也不把 Fixture 结果声明为真实研究质量。`api-strong` 仍按 M0 决策禁用；生产 Coordinator 明确失败关闭，不会静默改用本地 `qwen3:8b`。

## 2. 冻结结构

| 项目 | M3 冻结值 |
|---|---|
| ScholarTrace | `0.3.0` |
| LangGraph | `1.2.11` |
| SQLite Checkpointer | `langgraph-checkpoint-sqlite 3.1.1` |
| Checkpoint 序列化 | 严格 MessagePack，只显式允许 `ArtifactRef` |
| Worker 并发 | 运行配置与 ResearchPlan Budget 两者较小值；Fixture 为 2 |
| Search/Worker 超时 | 独立配置，超时写安全事件并降级 |
| Checkpoint 保留 | 目标窗口 7 天；M3 只允许终态显式清理，自动清理延后服务装配 |
| State 上限回归 | Fixture 序列化小于 16 KiB |

Checkpoint DB、Artifact DB 和 Runtime Ledger 是三个独立文件。State 只保存任务/线程 ID、状态、轮次、论文 ID 与 `ArtifactRef`；完整 ResearchPlan、SearchRound 和 Worker output 不进入 State。

## 3. 路由与恢复

1. Coordinator 生成 draft ResearchPlan 并以稳定 Artifact ID 写入；
2. `interrupt` 返回 plan ref 与 approve/modify/reject 动作；
3. 恢复使用相同 `thread_id` 和 `Command(resume=...)`；
4. approve 执行原计划，modify 持久化新 approved plan，reject 不调用 Search/Worker；
5. Search Agent 首轮使用全部子问题，后续选择尚未覆盖的子问题调整 query；
6. 覆盖完成、低新增比例且无新覆盖、轮数或查询预算耗尽时停止；
7. `Send` 按论文稳定排序分发 Worker，reducer 按 Artifact ID 去重排序；
8. 重复投递在执行前按 effect key 加锁，跨恢复由唯一 Artifact/effect/event key 去重。

## 4. 自动检查

测试覆盖：

- 禁用 `api-strong` 时 Coordinator fail closed；
- approve、modify、reject 三条人工审批路径；
- 资源关闭并重开后的 SQLite interrupt 恢复；
- 中间覆盖改变第二轮 query，以及检索饱和停止；
- `Send` Worker 并发上限、稳定合并和单 Worker 超时隔离；
- 同一 Worker 并发重复投递只执行、计费和写入一次；
- Search 超时、图级预算耗尽和无 Worker 降级；
- Artifact 内容冲突拒绝、预算 effect exactly-once；
- Event 稳定 ID、顺序、SSE 与 `Last-Event-ID` 补发；
- State 不含 Worker output/quote 且小于 16 KiB；
- 严格 MessagePack 自定义类型白名单恢复。

最终门禁：`97 passed`；Ruff PASS；Mypy 对 33 个源码文件 PASS；`uv lock --check`、Schema 导出、JSON/OpenAPI/Markdown/凭据和仓库卫生检查 PASS。

## 5. Fixture Smoke

可提交摘要：`evaluation/reports/m3_workflow_fixture_smoke.json`
运行数据：临时写入已忽略的 `artifacts/m3-workflow-fixture/`，结束后清理

| 指标 | 结果 |
|---|---:|
| 真实 Provider / 本地模型调用 | 0 / 0 |
| Coordinator Fixture 调用 | 1 |
| interrupt 后资源重启 | 通过 |
| 动态检索轮数 | 2 |
| 选中论文 / Worker 调用 | 3 / 3 |
| Worker 峰值并发 | 2 |
| Artifact / 业务事件 | 7 / 10 |
| 断点后补发事件 | 8 |
| State 序列化大小 | 3,247 bytes |
| Fixture 墙钟 | 0.342 秒 |

人工案例观察：首轮 Fixture 只覆盖 `subq:retrieval`；Search Agent 随后从初始组合 query 切换为包含 `subq:evidence` 的 evidence provenance query。第二轮覆盖完成后停止，证明至少一个路径由中间结果改变，而不是固定 DAG 顺序。

## 6. 阶段结论

M3 的工程验收项全部满足：存在中间结果驱动的动态路由，interrupt 跨 SQLite 重启恢复不重复 Coordinator/Artifact/预算/事件写入，受限并行结果稳定合并。阶段结论为 `PASS WITH NOTES`。

保留限制：真实 `api-strong` Coordinator 尚未配置或评测，因此当前可复现的是控制流与可靠性，不是生产规划质量；SSE Router 当前负责持久事件补发，持续 tail、heartbeat、认证和完整 Research Task HTTP 装配延后到 M6。进入依赖关键 Evidence Verifier 的 M4 前，应先单独确认强模型 Provider、版本、官方价格快照和预算，或明确授权仅继续 Fixture 工程开发。
