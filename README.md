# ScholarTrace

ScholarTrace 是一个面向计算机与人工智能技术调研的证据可追溯 Multi-Agent 学术研究工作台，服务学生、开发者和初级研究人员，使关键结论能够回溯到真实论文、页码或 Chunk。

当前仓库已完成 **M0：范围与契约**、**M1：多源搜索**、**M2：DocuMind 证据闭环**和 **M3：LangGraph Multi-Agent 编排**。M3 的动态路由、人工审批、SQLite 恢复、受限 `Send` 并行、幂等副作用和 SSE 补发已通过工程复审，结论为 `PASS WITH NOTES`。

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

M2 增加了单论文范围的证据闭环：

- DocuMind Schema `1.0` Consumer、retrieval readiness、错误分类、响应大小和有界重试；
- `canonical_paper_id -> document_key/index_id/source_sha256` SQLite 绑定与显式 CAS；
- document/index/source、Chunk hash、连续 rank 和跨论文污染 fail-closed 校验；
- 本地 `qwen3:8b` PaperCard/Claim 提取，使用短引用确定性映射真实 Chunk 与逐字 quote；
- 三篇论文 Evidence 报告、检索审计、模型用量、预算和 RunManifest 原子持久化；
- DocuMind `2.2.0` 三篇版本化公开 arXiv PDF 的 upload/status/retrieve 在线验收；
- `docs/M2_EVIDENCE_BASELINE.md`：M2 契约、实测指标、在线限制与阶段复审。

M3 增加了可恢复 Multi-Agent 控制面：

- 可注入 Coordinator、严格付费 Profile 门禁和 approve/modify/reject `interrupt`；
- 基于中间覆盖调整 query 的 Search Agent，以及覆盖/饱和/轮数/预算停止；
- `Send` 论文 Worker、并发信号量、独立超时和确定性 ArtifactRef reducer；
- 分离的 SQLite Checkpoint、Artifact Store、Runtime Ledger 和稳定幂等 key；
- 持久业务事件、SSE 与 `Last-Event-ID` 补发；
- `docs/M3_WORKFLOW_BASELINE.md`：恢复、并发、预算、Fixture 指标和阶段限制。

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

M2 冻结 Provider 契约只读验证：

```powershell
uv run python scripts/verify_m2_documind_compatibility.py
```

M2 三论文 Fixture + 真实本地 `qwen3:8b` smoke：

```powershell
uv run python scripts/run_m2_fixture_smoke.py
```

该 smoke 的 DocuMind 响应来自公开摘要片段构造的契约 Fixture，不代表在线检索质量；完整 Artifact 写入已忽略的 `artifacts/m2-evidence-fixture/`，只提交脱敏指标。

M2 三论文 DocuMind 在线全文 smoke（要求 DocuMind `2.2.0`、Milvus、Ollama `qwen3-embedding` 和 `qwen3:8b`）：

```powershell
uv run python scripts/run_m2_live_smoke.py
```

M3 确定性编排 smoke（不访问网络，不调用本地或付费模型）：

```powershell
$env:LANGGRAPH_STRICT_MSGPACK = "true"
uv run python scripts/run_m3_fixture_smoke.py
```

该 smoke 在 `interrupt` 后关闭并重开 SQLite 资源，再执行审批恢复、动态两轮检索和三个受限 `Send` Worker。临时数据库位于已忽略的 `artifacts/`，只提交脱敏指标。

脚本只下载 Fixture 锁定版本的公开 arXiv PDF，限制响应类型和大小；原文、绑定和完整 Evidence Artifact 写入已忽略的 `artifacts/`。默认只删除本次新建的 DocuMind 文档，传入 `--keep-documents` 才保留索引。

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

- DocuMind 最低基线为 `2.1.0`，兼容验证覆盖 `2.1.0/32c5eb8` 与 `2.2.0/212f60a`，`/api/v1/retrieve` Schema 为 `1.0`；
- ScholarGraph 基线为 `1.0.0` / GraphRAG `3.1.2`，主仓当前没有 HTTP API；
- ScholarGraph 只覆盖固定的 198 篇 RAG 摘要，不能替代全文证据；
- 本地模型冻结为 `qwen3:8b`；M0 已确认保持付费 API Profile 禁用，未来启用前仍需独立预算授权；
- M1 的 B0/B1 只使用元数据和摘要，不代表已经核对论文全文；
- M2 已用三篇公开全文完成在线工程验收，但样本规模有限，且独立语义蕴含核验按计划延后到 M4；
- M3 已验证编排可靠性，但 `api-strong` 仍禁用，真实 Coordinator 规划质量和成本尚未在线评测；
- M3 SSE Router 只补发当前持久事件；持续 tail、heartbeat、认证和完整 Research Task HTTP 装配延后到 M6；
- 不提交凭据、运行数据、论文全文、模型原始回答或 `agent/` 工作记录。

产品范围见 [`docs/PRD.md`](docs/PRD.md)，架构与演进条件见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。
