# M10-P4 发布、备份与恢复复审

> 日期：2026-09-04
> 阶段：M10-P4
> 结论：`PASS WITH NOTES`，M10 已冻结

## 1. 范围与持久化盘点

M10-P4 冻结 ScholarTrace `0.5.0` 的本地安装、升级、回滚、数据目录、健康检查、备份、
恢复和发布方式。本阶段不改变 M9 评测证据，ScholarGraph 继续 `keep_disabled`。

当前在线交付 API 的 `SCHOLARTRACE_DATA_DIR` 包含：

| 路径 | 内容 | P4 处理 |
|---|---|---|
| `tasks.sqlite` | 任务、幂等键、报告及其他导出工件 BLOB | SQLite Backup API 快照、hash 和表计数校验 |
| `runtime.sqlite` | 持久事件和预算 effect | SQLite Backup API 快照、hash 和表计数校验 |
| 任意子目录中的 `*.db/*.sqlite/*.sqlite3` | 可注入的 M3 checkpoint、Artifact Store、Runtime Ledger 或 binding | 递归纳入同一清单 |
| `*-wal/*-shm` | SQLite 瞬态 sidecar | 不直接复制，由 SQLite Backup API 合并一致视图 |
| `.env`、原始 PDF、缓存、日志、临时文件 | 凭据、上游正文或可再生数据 | 明确排除 |

M3 的 checkpoint/Artifact Store/Runtime Ledger 当前仍是库级可注入持久化，尚未装配进在线
Research Task API；若部署方把这些数据库置于同一 `SCHOLARTRACE_DATA_DIR` 子目录，P4 工具会
一并备份。M2/P3 smoke 写在仓库已忽略 `artifacts/` 中的 PDF 和 binding 不属于在线数据卷。

## 2. 已实现行为

- `scholartrace-backup`/`scripts/backup_scholartrace.py`：递归发现允许的 SQLite，使用
  `sqlite3.Connection.backup()` 生成独立快照，执行 `PRAGMA quick_check`，记录每个文件的
  SHA-256、字节数和用户表行数，最后原子发布 ZIP；
- `scholartrace-restore`/`scripts/restore_scholartrace.py`：拒绝未知应用/Schema/版本、重复 ZIP
  条目、路径穿越、符号链接、未声明文件、大小/hash/表计数不一致和超限归档；
- 恢复只允许写入不存在的新目录，先在同级临时目录完成全部校验，再原子切换目录；不会覆盖
  当前数据目录，也不会自动删除旧数据；
- 备份清单不保存源机器绝对路径，只使用 `SCHOLARTRACE_DATA_DIR` 逻辑布局；
- 当前没有数据库迁移器，因此恢复要求 `application_version` 与当前程序精确匹配。跨版本恢复
  必须先用原版本恢复，再按运行手册升级；
- `scripts/build_local_release.py` 生成确定性源代码 ZIP，固定成员顺序、时间戳和权限，包含
  `uv.lock`、`frontend/package-lock.json`、Compose、源码、测试、契约和公开文档；
- API Docker 构建固定 `uv==0.12.3` 并执行 `uv sync --locked --no-dev --no-editable`；前后端
  `.dockerignore` 使用构建输入白名单，避免把本地依赖、缓存和运行数据送入 context；
- 发布包明确排除 `.env`、`agent/`、运行数据、缓存、`node_modules` 和构建输出。

## 3. 恢复与发布证据

当前本机的已忽略交付目录 `artifacts/m6-delivery` 已执行真实 SQLite 快照和隔离恢复：

- 2 个 SQLite，快照共 81,920 bytes；
- `tasks=2`、`events=18`、`artifacts=8`、`idempotency_keys=2`；
- 两个数据库 `quick_check=ok`，归档和清单 hash 校验通过；
- 任务、事件和 Artifact 内容 hash 的逻辑盘点在恢复前后一致；
- 恢复目标位于临时隔离目录，未覆盖源数据；
- 公开证据：`evaluation/reports/m10_p4_restore.json`。

发布包已完成两次独立构建的字节级 SHA-256 对比。最终包和最终 hash 在全部文档冻结后由
`scripts/run_m10_p4_release_drill.py` 生成，并记录到
`evaluation/reports/m10_p4_release.json`；ZIP 只保存在已忽略的 `artifacts/m10-p4/`。

## 4. 自动检查

- P4 专项：`12 passed`，覆盖在线 WAL 快照、报告 BLOB、嵌套 workflow SQLite、排除策略、
  篡改、禁止覆盖、应用/版本身份、路径穿越、CLI 和确定性发布；
- 全量 Pytest：`228 passed`；
- `uv run ruff check src tests scripts`：PASS；
- `uv run mypy src`：82 个源文件 PASS；
- `npm run build`：TypeScript/Vite 生产构建 PASS，1,576 个模块；
- `uv lock --check`、sdist/wheel、`docker compose config --quiet`、`git diff --check`：PASS；
- API/UI 镜像使用锁定安装方式完成构建。Docker Hub OAuth 首次超时后，从 Daocloud 代理拉取
  相同 `node:20-alpine`/`nginx:1.27-alpine` digest 并在本机打官方标签，源码未改源地址；
- 临时容器验收：API `0.5.0` 返回 `live/ready` 且 `accepting=true`；UI 首页 200 且通过 Nginx
  反向代理访问 API；容器内活跃 WAL 备份和新目录恢复 `verified=true`；
- 前端单独启动且无法解析 `scholartrace-api` 时按 Nginx 设计 fail fast；置于 Compose 等价的
  同一网络并设置 API 别名后通过。临时容器和网络已清理。

## 5. 安装、升级与回滚结论

完整命令见 `docs/RUNBOOK.md`。冻结顺序为：

1. 安装使用 Python 3.11、`uv sync --locked --all-groups`、`npm ci` 或 Docker Compose；
2. 升级前停止接收新审批并等待队列 drain，停止 API，生成并验证备份；
3. 从锁定发布包构建并启动，依次检查 API live、API ready 和 UI；
4. 数据 Schema 无迁移时可复用原目录；如未来出现迁移，必须恢复到新目录并显式切换；
5. 启动或健康检查失败时回到旧发布包和旧数据目录，不在原目录上尝试破坏性修复。

## 6. Notes 与产品边界

- 备份工具保证单个 SQLite 快照一致；跨数据库的强事务一致性仍要求先 drain 并停止 API。
  在线备份在技术上受支持，但不是发布/升级的首选流程；
- ScholarTrace 备份保存任务、事件和已生成报告，但不保存 DocuMind 的注册表、Milvus 向量、
  原始 PDF 或 Ollama 模型。需要在灾难恢复后继续检索时，必须独立恢复或重建 DocuMind；
- DocuMind 当前是未发布的 `2.2.0` 后续冷启动可靠性补充，仍需在其仓库独立提交和发布；
- 本地发布包锁定输入文件并保证本机两次构建字节一致，但首次 Docker/依赖安装仍需要可用的
  镜像和包源；本机 Docker Hub OAuth 曾超时，镜像代理可作为手动恢复方案，离线全依赖镜像
  包不在范围内；
- 当前是本地单用户/可信私网应用，不提供公网、多用户、租户、跨进程 worker、
  自动数据库迁移或一键跨服务灾备；
- ScholarGraph 仍因前瞻 Gate A `INCONCLUSIVE` 而默认关闭，P4 不改变其启用条件。
