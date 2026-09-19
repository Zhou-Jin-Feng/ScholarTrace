# ScholarTrace

**面向学术技术调研的证据可追溯研究工作台。**

ScholarTrace 将论文身份、检索证据、Claim、引用关系和研究任务状态组织为结构化工件，支持审批、事件回放和报告导出。DocuMind 提供论文级全文检索，ScholarGraph 提供受限的摘要图谱辅助能力；各服务独立部署。

> **当前可直接运行的是确定性 Demo。** Web/API 已提供任务审批、有界队列、取消、SSE 和报告下载，但尚未把全部真实搜索、全文获取与模型组件装配成端到端生产研究任务。Demo 结果不能作为真实研究质量证据。

## 快速开始：本地 Demo

环境：Python 3.11、uv、Node.js 20+、npm。依赖安装需要网络；安装完成后的确定性 Demo 不需要 API Key、DocuMind、ScholarGraph 或 Ollama。

在项目根目录启动 API：

```powershell
uv sync --locked --all-groups
uv run uvicorn scholartrace.api.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

另开终端：

```powershell
Set-Location frontend
npm ci
npm run dev
```

打开 [工作台](http://127.0.0.1:5173)，创建任务并审批后查看事件和导出报告。API 的 [存活检查](http://127.0.0.1:8000/api/v1/health/live) 与 [就绪检查](http://127.0.0.1:8000/api/v1/health/ready) 只反映当前服务状态，不代表真实研究链路可用。

无浏览器的离线 Demo：

```powershell
uv run python scripts/run_m6_demo.py
```

### Docker Compose

```powershell
docker compose up --build
# 停止并保留任务数据卷
docker compose down
```

默认仅绑定本机回环地址。首次构建需要镜像和依赖源；不要用 `down -v` 停止服务，它会删除任务数据卷。可信私网部署、认证、备份和恢复见 [运行手册](docs/RUNBOOK.md)。

## 能力与边界

| 模块 | 已有能力 | 不能据此推断 |
|---|---|---|
| 任务工作台 | 审批、幂等、有界队列、取消、SQLite 持久化、SSE 续传、四种报告格式 | 已完成公网、多用户或跨进程生产部署 |
| 搜索与证据组件 | 多源元数据搜索、论文身份归一化、单论文检索、绑定与内容 hash 校验 | 默认 Demo 已调用真实论文搜索或模型 |
| 控制与核验组件 | 研究流程编排、恢复、预算门禁、引用网络、确定性校验和受控补查 | Fixture 通过等于语义准确率或模型收益已证实 |
| 全文获取 | 有界、版本化公开 arXiv PDF 获取和 DocuMind 入库组件 | 支持任意出版社、付费墙或无授权全文 |
| 图谱增强 | ScholarGraph 只读 Consumer、能力路由与安全回退 | 图谱增强必然优于基线；目前默认关闭 |
| 运维 | 本地一致快照备份、只写新目录的恢复、确定性源码打包 | 已覆盖上游向量库、模型、原始 PDF 或跨服务灾备 |

历史评测保留真实负面和不确定结论：B3/B4 对照未观察到 eligible 质量增益，M9-P2 Gate A 为 `INCONCLUSIVE`。这些是特定数据、版本和条件下的证据，不是当前环境的实时验证结果。M9-P2 的样本来源与 Gold 依据仍存在限制，不能将其视为已验证的自然连续生产样本；详见 [最终复审](docs/M9_P2_GATE_A_REVIEW.md)的后续来源抽核与解释限制。

## 上游联调与安全

- 当前 Consumer 保留 DocuMind 2.x（minor ≥ 1）并明确支持 **3.0.0**；不自动接受3.0.1、3.1或预发布版本。历史在线基线为2.1.0/2.2.0；3.0.0已增加离线契约及合成HTTP流程回归，**真实部署的全文/模型链路仍需独立验收**。
- ScholarGraph 的冻结基线为 1.2.0，仅覆盖固定的 198 篇 RAG 摘要；不能替代全文证据。
- 真实全文 smoke 会下载公开 PDF、调用 DocuMind/Ollama 并创建文档；它不是只读检查。默认只清理本轮新建文档，不应直接对重要业务部署试跑。
- 付费 Provider 需要独立配置、预算和明确授权；复制示例配置不会自动授予调用权限。
- 不提交凭据、论文全文、模型原始回答、运行数据库或私有工作记录。

当前上游的**离线契约检查**（将路径替换为实际 DocuMind 仓库）：

```powershell
uv run python scripts/verify_m2_documind_compatibility.py --provider-repo ../DocuMind --provider-ref HEAD
```

脚本解析分支/tag 可达的已提交版本和契约，默认不联网。版本不兼容返回非零；显式 `--base-url` 只增加 readiness 检查，不验证真实检索、模型质量或部署 SHA。历史旧对象重放必须用 `--historical`，与当前验证分开。3.0.0需明确提供retrieval组件就绪；整体HTTP 503而该组件ready时可以执行纯检索，不代表生成组件可用。

## 目录与数据

| 目录 | 用途 |
|---|---|
| `src/scholartrace/` | API、领域契约和研究/运维组件 |
| `frontend/` | React + Vite 工作台 |
| `tests/` | 确定性测试与公开 Fixture |
| `scripts/` | 验证、实验和运维入口 |
| `contracts/` | Schema、OpenAPI 快照与契约样例 |
| `docs/` | 正式接口、架构、运行手册和历史实验说明 |
| `evaluation/` | 版本化种子与冻结评测证据，不作为新运行默认输出 |
| `artifacts/` | 新报告、测试临时文件和运行工件；默认任务根为 `artifacts/m6-delivery` |
| `data/`、`logs/` | 可选数据根和人工重定向日志；只跟踪目录占位 |

运行数据不会随 Git 自动上传。新报告默认写入 `artifacts/reports/`；显式输出参数仍由调用者负责，勿指向冻结证据。为兼容既有数据，本轮不迁移默认任务根。宿主机可用 `SCHOLARTRACE_DATA_DIR=data/tasks` 选择新目录，相对路径按项目根解析；容器路径使用 `SCHOLARTRACE_CONTAINER_DATA_DIR`，不要混用。

## 开发验证

```powershell
uv run pytest --basetemp artifacts/pytest
uv run ruff check src tests scripts
uv run mypy src
uv lock --check
Push-Location frontend
npm ci
npm run build
Pop-Location
```

测试使用确定性 Fixture，不以本机私有历史工件为前提。Schema 变更时运行 `scripts/export_schemas.py` 并审查差异；不要用批量版本替换修改历史评测或协议快照。上述命令是复现入口，不代表本次发布已经全部通过。

## 文档导航

- [运行、部署、备份与恢复](docs/RUNBOOK.md)
- [产品范围](docs/PRD.md) · [架构](docs/ARCHITECTURE.md) · [设计决策](docs/ADR.md)
- [接口契约](docs/INTERFACE.md) · [数据契约](docs/DATA.md)
- [模型路由与预算](docs/MODEL_STRATEGY.md)
- [证据基线与历史在线条件](docs/M2_EVIDENCE_BASELINE.md)
- [B3/B4 评测协议](docs/M6_B3_B4_PROTOCOL.md)

应用版本为 `1.0.1`；服务版本、接口 Schema、数据格式和 Git tag 是不同概念。冻结报告中的旧版本与旧 SHA 表示当时的实验身份，不应改写成当前版本。

## 工作台界面

任务历史、两道计划确认、预算时间线、报告及证据阅读的使用方式与模块说明见
[工作台文档](docs/workspace.md)。当前页面明确区分 Demo 与已装配、但尚未通过真实在线全链路验证的研究能力。
