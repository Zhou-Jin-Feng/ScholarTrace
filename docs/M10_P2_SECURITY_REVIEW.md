# M10-P2 访问控制与部署边界复审

> 日期：2026-09-04
> 结论：**PASS WITH NOTES**
> 范围：M10-P2 完成；M10-P3 不在本报告验收范围内

## 1. 范围

本阶段为 ScholarTrace 本地单用户工作台增加明确的部署边界和最小访问控制，不改变 M9
评测结果、ScholarGraph 默认关闭决策或 M10-P1 的单进程队列语义。范围包括：

- `loopback` 和 `trusted_private` 两种部署模式；
- Compose 默认回环绑定和可显式覆盖的绑定地址；
- 任务状态、事件、工件和报告路径的 Bearer 认证；
- 原生浏览器 `EventSource`/下载兼容的受限查询 token；
- token 校验、启动 fail-closed、应用日志脱敏和前端会话存储；
- `.env.example`、运行手册、接口契约和架构文档同步。

本阶段不提供公网安全、TLS 终止、账号体系、租户隔离、细粒度 Artifact 授权、跨进程队列
或审计级身份追踪。共享 token 只适合可信私网中的单用户/小范围临时访问。

## 2. 实施结果

| 项目 | 结果 |
| --- | --- |
| 默认部署 | Compose API/UI 绑定 `127.0.0.1` |
| 私网部署 | `trusted_private` 必须配置至少 16 个非空白字符 token |
| 保护范围 | 创建、状态、审批、取消、事件、工件、报告 |
| 保持可探测 | `health/live`、`health/ready` 和静态前端 |
| 认证方式 | 普通请求使用 `Authorization: Bearer` |
| 浏览器兼容 | 仅事件/报告 `GET` 接受 `access_token` 查询参数 |
| 前端凭据 | 当前会话 `sessionStorage`，不可用时退回内存 |
| 日志 | 应用只记录方法、路径、状态码、请求 ID；Compose 关闭 Uvicorn/Nginx 原始访问日志 |
| 配置错误 | 模式非法、token 过短/含空白、私网缺 token 时启动失败 |

## 3. 自动验证

已执行并通过：

- `tests/test_m10_security.py`：模式校验、私网认证、错误 Bearer、查询 token、报告/事件
  保护、loopback 兼容；
- M3 SSE 回归：回放、heartbeat、终态关闭、游标边界；
- 全量 Pytest：`207 passed`；
- `npm run build`：TypeScript/Vite 生产构建；
- `docker compose config --quiet`：Compose 插值、端口绑定和环境变量配置；
- `uv run ruff check src tests scripts`：通过；
- `uv run ruff format --check src/scholartrace/api/security.py tests/test_m10_security.py`：通过；
- `uv run mypy src`：通过；
- `git diff --check`：通过。

`uv lock --check` 也已通过。仓库整体 `ruff format --check src tests scripts` 仍会报告本阶段
之前已有的历史格式差异；本阶段未批量重排无关文件，新增安全文件和本阶段直接修改的
`src/scholartrace/api/app.py` 已单独通过 formatter 检查。

## 4. 人工复审清单

- [ ] 在默认 Compose 启动后确认端口只监听本机回环地址；
- [ ] 使用 `trusted_private` 缺 token/弱 token 启动，确认进程直接失败；
- [ ] 使用合法 token 从工作台创建任务、观察 SSE、下载四种报告；
- [ ] 删除或更换会话 token 后，任务、事件和报告请求均返回 `401`；
- [ ] 检查浏览器、反向代理和监控日志不保存完整 `access_token`；
- [ ] 确认共享 token 的所有持有者可见同一任务数据，接受其非多租户性质；
- [ ] 确认不把 `trusted_private` 端口直接暴露到公网，TLS/防火墙由部署层负责。

## 5. 限制与残余风险

- 查询参数 token 是原生 EventSource 无法设置 Header 的兼容折衷，可能被客户端历史或外部
  代理记录；生产化前应改为支持 Header 的流式客户端或短时一次性票据；
- Compose 关闭自身访问日志，但外部反向代理、云负载均衡和浏览器扩展仍可能记录 URL；
- 当前认证是共享密钥，不绑定用户、任务或 Artifact，不能作为公网或多租户安全证明；
- API 与 UI 的 TLS、CSRF、速率限制、密钥轮换和撤销机制留给后续独立阶段；
- 认证中间件不改变单进程 SQLite 的并发边界，跨进程部署仍不受支持。

## 6. 结论

工程验收为 `PASS WITH NOTES`。默认本地部署已收敛为回环边界，可信私网模式会对任务数据
fail closed，并且前端、SSE、报告下载和文档均保持一致。合法全文 acquisition 与 DocuMind 闭环不属于本报告范围；共享 token 不代表公网安全或多用户授权。
