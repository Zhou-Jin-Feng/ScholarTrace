# M10-P0 持续 SSE 可靠性复审

> 日期：2026-09-04
> 结论：**PASS WITH NOTES**
> 范围：M10-P0 完成；M10-P1 不在本报告验收范围内

## 1. 交付范围

本阶段只补齐 ScholarTrace 本地单用户工作台的 SSE 长连接能力，不改变 M9 评测结果和
ScholarGraph 默认关闭决策。没有引入 Redis、PostgreSQL、跨进程队列、认证、全文获取或模型调用。

已实现：

- 默认 replay 行为保持兼容，显式 `follow=true` 才持续轮询；
- `Last-Event-ID` Header 和 `last_event_id` 查询参数均可用于续传；
- 游标必须属于当前任务，冲突参数、非法格式、未知任务均 fail closed；
- 空闲连接发送 SSE comment heartbeat 和 `retry` 提示；
- `exports_ready` 排空后发送 `stream_end` 并关闭连接；
- RuntimeLedger 使用进程内可重入锁，保护同一 SQLite 连接的并发读写；
- React 在创建任务后立即建立 `EventSource`，显示 connecting/live/reconnecting/complete/error
  和最近事件时间线；
- 修复前端根组件未挂载和 JSON 请求头被覆盖两个真实运行阻断。

## 2. 验收证据

| 检查 | 结果 |
| --- | --- |
| SSE/RuntimeLedger 专项测试 | 12 passed |
| 全量 Python 测试 | 200 passed |
| Ruff 新增/修改文件 | PASS |
| 严格 Mypy | PASS，76 source files |
| 前端 TypeScript/Vite build | PASS |
| Compose 配置 | PASS |
| 浏览器创建/审批/订阅任务 | PASS |
| 浏览器事件时间线 | 9 条事件，终态 `COMPLETE` |
| 浏览器控制台（干净新会话） | 0 error/warn |
| 外部模型、DocuMind、ScholarGraph、付费调用 | 0 |

全量测试、最终构建和 diff 检查在文档提交前再次执行；结果写入阶段过程记录，不把旧热更新
标签页的开发警告当作干净会话结论。

## 3. 已知限制

- SSE 使用固定低频轮询 SQLite，不提供跨进程广播或任务队列；并发上限留到 M10-P1 测量；
- heartbeat 不是业务事件，没有 event ID，不进入任务事件计数；
- 浏览器自动重连依赖标准 EventSource 行为，服务端不保存连接级会话；
- M10-P0 只证明本地单用户可靠性，不证明公网部署、多用户隔离或高并发容量。

## 4. 结论

阶段工程结果为 `PASS WITH NOTES`。本报告验证本地工作台的实时任务事件和断线续传，不包含 M10-P1 单机队列、取消、背压和负载验证。
