# M10-P1 单机队列与负载复审

> 日期：2026-09-04
> 结论：**PASS WITH NOTES**
> 范围：M10-P1 完成；M10-P2 不在本报告验收范围内

## 1. 范围

本阶段只补齐本地单用户工作台的执行控制面：有界任务队列、背压、协作式取消、优雅停机和
并发证据。不引入 Redis、PostgreSQL、跨进程广播、认证、全文 acquisition、模型调用或
ScholarGraph 重新评测。

## 2. 已实现

- `BoundedTaskExecutor` 使用单进程 worker 和有界等待队列；默认 1 个 worker、4 个排队槽位；
- 审批后任务进入 `queued -> running`，队列满返回 HTTP 429 与 `Retry-After: 1`；
- `POST /api/v1/research/tasks/{task_id}/cancel` 支持等待审批、排队和运行中的任务；运行中
 任务在阶段边界协作式停止并生成 `task_cancelled`、四种导出和 `exports_ready`；
- 关闭时停止接收新任务，等待已接收任务排空；超时向未完成任务发送取消信号；
- `GET /api/v1/health/ready` 暴露 `accepting`、worker、容量、排队和活动任务快照；
- DeliveryStore 增加进程内可重入锁，和 RuntimeLedger 一起保护共享 SQLite 连接；
- React 工作台可取消 `queued/running` 任务并展示取消事件。

## 3. 验收证据

| 检查 | 结果 |
| --- | --- |
| 队列专项与 M6/SSE 回归 | 14 passed |
| 全量 Python 测试 | 204 passed |
| Ruff | PASS |
| 严格 Mypy | PASS，77 source files |
| 前端 TypeScript/Vite build | PASS |
| 本地负载档位 | 并发 1/2/4/8，各 12 任务 |
| 最终任务完成 | 48/48 |
| 终态 SSE 回放 | 48/48 含 `exports_ready` |
| 最终失败 | 0 |
| 初始 429 | 并发 1/2/4/8 为 0/4/6/7，重试后全部恢复 |
| 队列排空 | 每档 `queued=0, active=0, submitted=0` |
| 外部调用 | 0：模型、DocuMind、ScholarGraph、Ollama、付费 API |

可复现报告：`evaluation/reports/m10_p1_local_load.json`；命令：

```powershell
uv run python scripts/run_m10_p1_load.py --task-count 12
```

## 4. 人工复审清单

- 确认 `health/ready` 在正常启动时为 `ready`，关闭 drain 时为 `draining`；
- 确认队列满的客户端按 `Retry-After` 重试 approve，而不是重复创建任务；
- 确认取消后时间线包含 `task_cancel_requested`、`task_cancelled`、`exports_ready`；
- 确认关闭服务不会接受新的审批，已接收任务可排空或在超时后取消；
- 确认导出仍不含 API Key、Prompt、原始模型回答、论文全文或内部 Agent 记录。

## 5. 限制与后续

- 负载是进程内确定性 Demo 控制面，不是模型推理、GPU、PDF acquisition 或公网容量基准；
- 队列状态和 SQLite 锁只在单进程内有意义；多进程部署、多个 API 副本和多用户隔离仍未提供；
- 取消是协作式的，阻塞在不可中断的外部调用时只能在该调用返回后生效；
- 当前结果没有显示 SQLite 瓶颈或跨进程需求，因此暂不迁移 Redis/PostgreSQL；
- 回环/可信私网部署边界、最小认证和日志脱敏不在本报告的实现范围内。
