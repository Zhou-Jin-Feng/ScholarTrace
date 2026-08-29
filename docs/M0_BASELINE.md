# M0 基线与复审报告

> 日期：2026-08-29  
> 工程复审：PASS  
> 状态：M0 基线通过，M1 尚未开始

## 1. 输入与范围

- 输入：产品需求、架构与接口契约，以及两个上游仓库的冻结基线；
- 范围：主题种子、核心契约、OpenAPI、架构决策、模型路由和兼容 smoke；
- 非范围：论文搜索实现、Agent 业务工作流、前端、付费模型调用。

## 2. 交付物

| 类别 | 路径 |
|---|---|
| 产品与范围 | `docs/PRD.md` |
| 数据契约 | `docs/DATA.md`、`src/scholartrace/contracts.py`、`contracts/schemas/` |
| 服务接口 | `docs/INTERFACE.md`、`contracts/openapi/` |
| 架构决策 | `docs/ARCHITECTURE.md`、`docs/ADR.md` |
| 模型与成本 | `docs/MODEL_STRATEGY.md`、`evaluation/seeds/model_comparison_cases.json` |
| 评测主题 | `docs/EVALUATION_SEEDS.md`、`evaluation/seeds/m0_topics.json` |
| 自动验证 | `tests/`、`scripts/export_schemas.py`、`scripts/verify_upstreams.ps1` |
| 本地模型 smoke | `scripts/run_local_model_smoke.py`、`evaluation/reports/m0_local_model_smoke.json` |
| 可复现环境 | `.python-version`、`pyproject.toml`、`uv.lock` |

## 3. 冻结版本

| 组件 | 冻结值 |
|---|---|
| Python | 3.11.15 |
| LangGraph | 1.2.11 |
| Pydantic | 2.13.4 |
| FastAPI | 0.141.1 |
| 本地生成模型 | Ollama `qwen3:8b`，ID `500a1f067a9f`，8.2B，Q4_K_M，context 40,960 |
| 本地 Embedding | `qwen3-embedding:latest`，ID `64b933495768`，7.6B，Q4_K_M，context 40,960 |
| GPU | RTX 3070 Ti Laptop，8,192 MiB |
| DocuMind | 2.1.0，commit `32c5eb8d755065c7e8db9dae80973263aa77f196`，retrieve Schema 1.0 |
| ScholarGraph | 1.0.0，commit `953e40b155fe4d2e15002afcddc0e628f1524bf9` |
| GraphRAG | 3.1.2 |
| ScholarGraph 语料 | 198 篇 2020-2025 RAG 摘要 |

DocuMind 在复审时存在另一项并发本地迭代。M0 不读取未提交内容作为协议、不修改或回退上游，而是通过 Git 对象验证稳定 commit `32c5eb8`。

## 4. 默认硬预算

| 限制 | M0 冻结值 |
|---|---:|
| 输入 Token | 160,000 |
| 输出 Token | 40,000 |
| 总 Token | 200,000 |
| 模型调用 | 60 |
| 付费 API 调用 | 16 |
| DocuMind 检索/论文 | 6 |
| Paper Worker 并发 | 2（M2 初始值；通用契约最大值 4） |
| 定向补查 | 1 轮 |
| 墙钟时间 | 1,800 秒 |
| 总预算 | 10 CNY |

任何限制先到即停止新增调用，生成带未完成项的部分报告。10 CNY 是硬上限，不是预计花费；未配置价格快照时付费路由禁用。

## 5. 自动检查

```text
uv run pytest
21 passed

uv run ruff check .
All checks passed!

uv run mypy
Success: no issues found in 2 source files

powershell -ExecutionPolicy Bypass -File scripts/verify_upstreams.ps1
PASS upstream baselines match M0 freeze
```

覆盖内容：Pydantic 示例、JSON Schema 一致性、OpenAPI 3.1、未知字段 fail closed、全文 provenance、ScholarGraph 固定语料边界、LangGraph Send/interrupt/Checkpoint/事件流、模型路由、CNY 预算、评测种子、敏感信息和忽略规则。

## 6. 本地模型 smoke

固定三个样例覆盖 query rewrite、Claim-Evidence 绑定和摘要相关性初筛：

| 指标 | 结果 | 门槛 |
|---|---:|---:|
| 结构化成功率 | 100% | 100% |
| 语义规则通过率 | 100% | 100% |
| 关键无支持结论率 | 0% | 0% |
| P95 墙钟延迟 | 8.466 秒 | 不超过 120 秒 |
| API 成本 | 0 CNY | 单例不超过 0.5 CNY |

这是本机三例兼容 smoke，不是模型质量或容量基准。原始模型回答未保存，GPU 时间尚未与墙钟时间单独测量。

## 7. 已知限制

- 没有启动 DocuMind 在线依赖；核验针对冻结 Git commit、路由和 Schema；
- ScholarGraph HTTP API 仍是 M5 草案；
- 部分评测论文只确认 arXiv 身份，M1 需要多源复核；
- ScholarGraph 快照中的 arXiv:2310.11511 存在标题/作者身份冲突；
- Evidence 样例来自公开摘要，不代表已核对全文；
- M0 已明确选择不配置付费 API Profile，关键节点继续 fail closed；启用前仍需单独确认 Provider、模型和价格快照。

## 8. 基线确认

2026-08-29 冻结的基线为：

1. 接受 DEV-01 与三个 EVAL 主题、问题、排除条件和 CRAG 标注样例；
2. M0 暂不启用付费 Profile，`api-strong` 保持 `enabled=false`，不配置 API Key，不产生付费调用。

阶段结论为 `PASS`，M1 尚未实施。启用付费强模型前仍须核验 Provider、模型版本、上下文、价格快照并取得预算授权。
