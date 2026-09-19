# 验证与恢复

## 本地检查

Python 3.11 与 Node 20 为 CI 基线。在独立工作副本中执行：

```sh
uv sync --locked --all-groups
uv run pytest --basetemp artifacts/pytest
uv run ruff check src tests scripts
uv run mypy src
uv lock --check
uv run python scripts/export_schemas.py
git diff --exit-code -- contracts/schemas
cd frontend
npm ci
npm test
npm run build
npx playwright install --with-deps chromium firefox
npm run test:e2e
```

导出 Schema 后应检查是否漂移，不能只以脚本退出成功代替契约一致性。
测试前将 `SCHOLARTRACE_DATA_DIR` 指向专用临时目录，并清除真实供应方配置。
应用模块导入时会创建默认服务；不要让测试进程复用日常工作数据库。

## 浏览器测试

`frontend/e2e/scenarios.mjs` 包含可复用的真实页面流程：两道确认、修改后待审、
执行审批、刷新后任务保持、键盘跳转、分页、拒绝及三种视口重排。
拒绝任务仍可导出审计摘要，但不能出现搜索、生成等执行事件；审计导出不是研究成功。

Playwright 配置使用专用的 `tests/browser_server.py`：仅监听回环地址，
使用新建临时数据库，清除供应方环境配置，关闭付费路由，不复用已有服务器。
启动前须构建前端。使用确定性 Demo，不调用真实模型。
退出后删除的只有测试程序自己创建的临时目录。

CI 的 browser job 配置 Chromium / Firefox 两个客户端，包含 PDF 下载落盘与文件头尾检查。
该配置存在不代表远端 CI 已执行；本地连接浏览器的共用流程结果也不能替代完整跨客户端结果。
720 CSS 像素测试是重排检查，**不是原生 200% 浏览器缩放**。
浏览器 trace、报告、下载样例仅写入忽略的 `artifacts/`，只对合成测试数据启用录制。

## 备份、迁移与回退

1. 停止接收新任务，等待运行任务结束后停止服务。SQLite 在线备份能保证单库一致性，
   但不能保证多个数据库处在同一个业务时点，跨库恢复演练应在静止状态下执行。
2. 使用当前版本的 `scholartrace-backup --data-dir <数据目录> --output <目录外备份.zip>`。
3. 使用 `scholartrace-restore --archive <备份.zip> --target-dir <尚不存在的新目录>`。
   工具核验版本、清单、大小、SHA256、SQLite 完整性及表计数，拒绝覆盖现有目录。
4. 用同一应用版本指向恢复的新目录启动；核对任务、计划版本/指纹、预算、事件、证据归属和导出。
5. 数据库 schema 4 不承诺原地降级。升级前保留旧应用版本及其备份；需回退时停止服务，
   用匹配版本恢复升级前备份。不要把恢复目录直接覆盖到运行中的数据库上。

备份只包含约定的 SQLite 状态，不包括原始 PDF、缓存、凭据和日志；数据库内的证据摘录、
任务问题、报告仍可能含敏感内容，备份需要按私有数据管理，不上传仓库或公开制品。
DocuMind 索引、向量库及本地模型需各自的恢复方案。

## 验证边界

- 离线 fixture 验证工程契约与恢复，不代表真实研究质量或供应方账单核对。
- 生产装配（真实执行器、协调器与统一调用计量）已在 N1–N3 实现并通过工程验证；但真实在线全链路（真实搜索、全文获取、在线 DocuMind、Provider 账单）尚未验证，不能仅配置密钥后放行。
- 真实调用需要另行确定供应方、模型、价格、预算上限和数据外发范围。
- 中文 PDF 使用阅读器 CJK 替代字体，并非嵌入字体 PDF/A。
- Docker 验证须使用独立项目名、回环端口与合成卷，不复用正式数据。
