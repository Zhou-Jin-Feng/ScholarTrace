# ScholarTrace

ScholarTrace 是一个面向计算机与人工智能技术调研的证据可追溯 Multi-Agent 学术研究工作台，服务学生、开发者和初级研究人员，使关键结论能够回溯到真实论文、页码或 Chunk。

当前仓库已完成 **M0：范围与契约**、**M1：多源搜索**、**M2：DocuMind 证据闭环**、**M3：LangGraph Multi-Agent 编排**、**M4：引用网络与证据核验**、**M5：ScholarGraph 能力受限集成**和 **M6：工作台与交付装配**。M6 的 Research Task API、React 工作台、四种导出、B0-B4 证据矩阵、Compose、CI、本地 Demo、api-strong 结构化适配、12 题真实付费 B3/B4 盲审和三次 Basic 重复测试均已完成，阶段结论为 `PASS WITH NOTES`。M7-G 与 M8-G 的独立 Gate A 均未观察到可泛化的候选增益，因此 ScholarGraph 继续默认关闭；M9-P0 的私有连续采集、人工复核、脱敏报告和冻结 B5 快照工具已就绪，真实样本仍为 `0/30`，当前决策为 `COLLECT_MORE`。

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

M4 增加了引用网络与独立证据核验层：

- OpenAlex 两段式显式引用扩展，保留请求、响应 hash、边来源和缺边降级；
- `normalize -> relevance -> access -> acquire -> DocuMind ingest -> analyze` 顺序生命周期门；
- NetworkX 有向图、弱连通分量、社区、确定性 PageRank 和时间线候选；
- ID、绑定、版本、hash、页码、Chunk、字符范围、数值与引用边的确定性 Validator；
- `supported / partially_supported / unsupported / conflicted` 四态 Verifier 与 Profile 门禁；
- unsupported Claim 排除、partial/conflicted 可见标记和全局最多一轮 FollowUp；
- 匿名 OpenAlex 有界在线 smoke、真实边 provenance 和缓存重放；
- `docs/M4_RELIABILITY_BASELINE.md`：契约、Fixture 指标、测试证据和阶段限制。

M5 增加了 ScholarGraph 能力受限集成与对照评测框架：

- ScholarGraph `1.2.0` 五个只读端点的严格 Consumer、响应大小限制和错误分类；
- 固定 198 篇 2020-2025 英文 RAG 摘要语料的主题、年份、证据等级和只读门禁；
- Basic 默认、Local 仅实体邻域、Global/DRIFT 禁用的确定性 Capability Router；
- 越界、预算不足、超时、失败和契约漂移全部安全回退 B3；
- 6 个 eligible 与 6 个 boundary 问题的同条件 B3/B4 配对评测契约；
- 真实 Basic 一次调用成功，Provider 35.043 秒、端到端 36.857 秒、0 CNY；
- `docs/M5_SCHOLARGRAPH_BASELINE.md`：契约、路由、联调指标、评测限制与阶段结论。

M6 增加了可运行的研究工作台交付切片：

- `src/scholartrace/api/app.py`：Research Task API、健康检查、任务审批、事件和报告下载；
- `src/scholartrace/delivery/`：SQLite 任务元数据、脱敏报告、PDF 导出和 B0-B4 矩阵；
- `frontend/`：React + Vite 工作台，显示阶段、证据、冲突、图谱、预算和降级；
- `Dockerfile`、`docker-compose.yml`：API/UI 双容器交付；
- `.github/workflows/quality.yml`：Python 与前端质量门；
- `docs/RUNBOOK.md`、`docs/M6_DELIVERY_BASELINE.md`：运行、排障和阶段证据；
- `docs/API_STRONG_PROVIDER.md`：自定义 OpenAI 兼容 Provider 的模型枚举、官方上下文参考和分层预算；
- `docs/M6_B3_B4_PROTOCOL.md`：B3/B4 生产报告生成、Evidence 输入门禁、盲审、调用审计和参考预算；
- `evaluation/reports/m6_demo_smoke.json`、`m6_b0_b4_delivery_matrix.json`：脱敏 Demo 和完整评测矩阵；
- `evaluation/reports/m6_api_strong_smoke.json`：不含 Prompt/原始回答的 `gpt-5.6-terra` Responses 严格结构化兼容性指标。
- `evaluation/reports/m6_b3_b4_scored_comparison.json`：不含原始答案和 A/B 映射的正式盲审聚合；
- `evaluation/reports/m6_scholargraph_basic_capacity.json`：三次顺序 Basic 的脱敏可靠性与延迟指标。

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

M4 确定性引用与核验 smoke（不访问学术 API、DocuMind 或模型 Provider）：

```powershell
uv run python scripts/run_m4_fixture_smoke.py
```

该 smoke 使用显式冲突和缺失引用边 Fixture，验证冲突标记、关键 Claim 拦截与单轮 FollowUp；Fixture Verifier 只证明四态处理，不代表真实语义核验质量。

M4 OpenAlex 有界在线 smoke（访问公开学术 API，不调用 DocuMind 或模型）：

```powershell
uv run python scripts/run_m4_openalex_live_smoke.py
```

默认使用独立兼容性控制 seed、至多 10 个引用目标元数据和新建的已忽略缓存目录；公开报告不保存标题、摘要、响应正文或 API Key。该 smoke 验证在线兼容性，不是完整引用召回率基准。

M5 ScholarGraph Fixture smoke（不访问 Provider 或模型）：

```powershell
uv run python scripts/run_m5_fixture_smoke.py
```

M5 ScholarGraph 真实 Basic 联调（要求 `http://127.0.0.1:8002` 运行冻结的 ScholarGraph `1.2.0`）：

```powershell
uv run python scripts/run_m5_scholargraph_live_smoke.py
powershell -ExecutionPolicy Bypass -File scripts/verify_m5_scholargraph.ps1
```

联调公开报告不保存问题、答案、Prompt 或原始诊断；Global/DRIFT 不会被调用。该 smoke 只证明接口、路由和运行兼容，不能证明 B4 相对 B3 的质量收益。

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

M6 本地交付 Demo（不访问外部 Provider 或模型）：

```powershell
uv run python scripts/run_m6_demo.py
uv run uvicorn scholartrace.api.app:app --host 127.0.0.1 --port 8000
```

M6 B3/B4 零付费执行与盲审基础设施 smoke：

```powershell
uv run python scripts/run_m6_b3_b4_fixture.py
```

该命令只使用 Fixture，验证同条件 manifest、DocuMind Evidence 输入 hash、边界路由、私有答案和盲审闭环，不调用强模型，也不产生 B4 质量收益结论。完整协议见 [`docs/M6_B3_B4_PROTOCOL.md`](docs/M6_B3_B4_PROTOCOL.md)。

24 次最终报告调用的三档参考估算为 2.678760/7.128000/13.392000 CNY；用户批准增加 50% 余量后，pilot 上限为 3 CNY、全量最终报告上限为 22.5 CNY。12 题全量运行已完成：56 次新增 Verifier 和 24 次报告调用的参考成本合计 8.150130 CNY，6 个 eligible Basic 成功、6 个 boundary 零 ScholarGraph 调用；这些数字不是 Provider 实际账单。人工盲审导入后 B3/B4 总体均分为 4.000000/3.916667，eligible 的 B4-B3 平均质量差为 0，因此 ScholarGraph 不默认启用。

M6 ScholarGraph Basic 三次顺序重复（要求冻结服务运行在 `http://127.0.0.1:8002`，不调用付费模型）：

```powershell
uv run python scripts/run_m6_scholargraph_capacity.py --repeat-count 3
```

本机结果为 3/3 成功、HTTP P50 32.826 秒、P95 34.2426 秒、每次一次尝试。该结果是单 GPU、并发 1 的有界可靠性证据，不是并发负载测试。

已获独立预算批准时，api-strong 兼容性 smoke 使用项目根目录未跟踪 `.env`，最多执行两种协议尝试且不保存 Prompt/原始回答：

```powershell
uv run python scripts/run_m6_api_strong_smoke.py `
  --model gpt-5.6-terra `
  --max-output-tokens 900 `
  --max-cost-cny 5
```

另开终端进入 `frontend/` 执行 `npm ci` 和 `npm run dev`，浏览器打开 `http://127.0.0.1:5173`。完整运行手册见 [`docs/RUNBOOK.md`](docs/RUNBOOK.md)。

## 当前边界

- DocuMind 最低基线为 `2.1.0`，兼容验证覆盖 `2.1.0/32c5eb8` 与 `2.2.0/212f60a`，`/api/v1/retrieve` Schema 为 `1.0`；
- ScholarGraph 冻结基线为 `1.2.0/3aa5e2a`、GraphRAG `3.1.2`，提供五个只读 HTTP 端点；
- ScholarGraph 只覆盖固定的 198 篇 RAG 摘要，不能替代全文证据；
- 本地模型冻结为 `qwen3:8b`；自定义 Provider 的 `gpt-5.6-terra` 已通过 Responses 严格结构化 smoke 和受控 B3/B4 评测，但生产付费 Profile 继续禁用；
- M1 的 B0/B1 只使用元数据和摘要，不代表已经核对论文全文；
- M2 已用三篇公开全文完成在线工程验收，但样本规模仍有限；
- M3 已验证编排可靠性；`api-strong` Coordinator 适配协议已在线验证，但真实规划质量和 Provider 实际账单尚未评测；
- M4 已完成匿名 OpenAlex 有界在线兼容性验收，但 1 个控制 seed 不能代表完整引用覆盖率；
- M4 的 `api-strong` 关键 Verifier 仍禁用，生产调用 fail closed，Fixture 结果不能作为语义准确率或模型成本证据；
- M5 已完成历史真实 Basic 兼容联调；M6 已补齐 B3/B4 生产 ReportGenerator、Evidence 输入门禁、12 题真实付费执行、盲审导入和三次顺序容量复验。B4 未观察到 eligible 质量增益且延迟更高，ScholarGraph 因此默认关闭；
- M6 已装配 Research Task API 和当前持久事件补发，但 SSE 持续 tail、heartbeat、认证、多用户和完整全文 acquisition 仍未交付；
- 不提交凭据、运行数据、论文全文、模型原始回答或 `agent/` 工作记录。

产品范围见 [`docs/PRD.md`](docs/PRD.md)，架构与演进条件见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。
