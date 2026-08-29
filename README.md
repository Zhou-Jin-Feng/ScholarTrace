# ScholarTrace

ScholarTrace 是一个面向计算机与人工智能技术调研的证据可追溯 Multi-Agent 学术研究工作台，服务学生、开发者和初级研究人员，使关键结论能够回溯到真实论文、页码或 Chunk。

当前仓库已完成 **M0：范围、契约和评测种子** 与 **M1：多源搜索、归一化和简单 Baseline**。项目停在 M2 开始前；DocuMind 全文证据闭环、Agent 工作流和前端尚未实现。

## 已冻结交付

M0 冻结了后续开发所依赖的数据模型、跨服务接口、评测主题、上游版本、模型路由和架构决策：

- `docs/PRD.md`：P0/P1/P2 产品范围和验收标准；
- `docs/DATA.md`：ResearchPlan、Paper、Evidence 等核心数据契约；
- `docs/INTERFACE.md`：ScholarTrace、DocuMind、ScholarGraph 接口边界；
- `docs/ARCHITECTURE.md`：技术方案比较、选型和模块职责；
- `docs/ADR.md`：关键架构决策记录；
- `docs/MODEL_STRATEGY.md`：本地/API 路由、硬预算和模型评测门禁；
- `docs/EVALUATION_SEEDS.md`：1 个开发主题、3 个评测主题和人工复核种子；
- `contracts/openapi/`：两个上游服务的 OpenAPI 契约；
- `contracts/schemas/`：由 Pydantic 模型导出的 JSON Schema；
- `contracts/examples/`：可机器验证的契约样例；
- `tests/`：契约、OpenAPI 和 LangGraph 兼容 smoke。
- `evaluation/reports/m0_local_model_smoke.json`：不含原始回答的本地模型 smoke 指标。

M1 增加了可复现的学术元数据搜索链路：

- arXiv、OpenAlex、Crossref 必选来源和 Semantic Scholar 可选来源；
- 每个来源独立的预算、缓存、限流、超时、响应大小、重试和错误分类；
- Canonical Paper ID、保守去重、版本关系、来源审计和固定黄金集；
- 确定性 B0/B1 控制组与本地 `qwen3:8b` 元数据/摘要 Baseline；
- SearchSnapshot、BaselineArtifact、请求审计和扩展 RunManifest；
- 固定 Fixture 测试、公开来源 smoke、缓存重放和离线快照重放；
- `docs/M1_SEARCH_BASELINE.md`：M1 使用方式、实测结果、限制与阶段复审。

## 环境

- Python 3.11；
- uv 0.12 或兼容版本；
- Windows PowerShell 示例命令，也可在其他平台使用等价的 uv 命令。

默认 Python 版本不是 3.11 时，uv 会按 `.python-version` 使用项目锁定解释器。

## 验证

```powershell
uv sync --all-groups
uv run python scripts/export_schemas.py
uv run pytest --basetemp artifacts/pytest
uv run ruff check .
uv run mypy src
```

M1 固定快照离线重放：

```powershell
uv run python scripts/run_m1_search.py --replay-snapshot artifacts/m1-search/search_snapshot.json
```

M1 本地模型 B0/B1（要求本机 Ollama 已有 `qwen3:8b`，不会读取 API Key）：

```powershell
uv run python scripts/run_m1_local_baselines.py `
  --summary-output evaluation/reports/m1_local_baseline_smoke.json
```

公开数据源联机 smoke 会访问外部学术元数据 API，并将原始运行 Artifact 写入已忽略的 `artifacts/` 和 `data/`：

```powershell
uv run python scripts/run_m1_search.py `
  --summary-output evaluation/reports/m1_live_search_smoke.json
```

上游只读基线验证：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_upstreams.ps1
```

该脚本只读取相邻的 `DocuMind` 和 `ScholarGraph` 仓库，不启动服务、不修改上游文件，也不读取或打印 `.env`。

## 当前边界

- DocuMind 基线为 `2.1.0`，`/api/v1/retrieve` Schema 为 `1.0`；
- ScholarGraph 基线为 `1.0.0` / GraphRAG `3.1.2`，主仓当前没有 HTTP API；
- ScholarGraph 只覆盖固定的 198 篇 RAG 摘要，不能替代全文证据；
- 本地模型冻结为 `qwen3:8b`；M0 已确认保持付费 API Profile 禁用，未来启用前仍需独立预算授权；
- M1 的 B0/B1 只使用元数据和摘要，不代表已经核对论文全文；
- M0 示例中的 Claim-Evidence 用于验证结构，正式研究结论必须在 M2 后由真实证据生成；
- 不提交凭据、运行数据、论文全文、模型原始回答或 `agent/` 工作记录。

产品范围见 [`docs/PRD.md`](docs/PRD.md)，架构与演进条件见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。
