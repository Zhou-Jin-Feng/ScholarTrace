# M1 多源搜索、归一化与简单 Baseline

> 日期：2026-08-29
> 工程复审：PASS
> 阶段状态：已冻结，停在 M2 开始前

## 1. 范围

M1 建立从公开论文元数据到可复现候选集的最小闭环：多源搜索、请求审计、身份归一化、保守去重、版本关系、确定性排序、B0/B1 和 RunManifest。它不下载论文全文，不调用 DocuMind，不启用付费模型，也不把摘要结论表述为全文证据。

必选来源为 arXiv、OpenAlex 和 Crossref；Semantic Scholar 是显式开启的可选增强，缺少 Key 或服务受限不影响必选链路。

## 2. 来源策略

| 能力 | 冻结行为 |
|---|---|
| 请求预算 | 每个来源单独计数，不允许一个来源耗尽其他来源预算 |
| 缓存 | 公开 URL 和公开参数组成键；私有 Key/邮箱不持久化 |
| 限流 | 每个来源独立最小请求间隔 |
| 超时与大小 | 单请求超时；响应超过 2 MB 默认上限时失败关闭 |
| 重试 | 只对 429、5xx 和网络/超时执行最多 3 次的有界重试 |
| 错误 | authentication、budget、client、invalid、network、rate limit、size、service、timeout 分类 |
| 降级 | 单源失败时保留错误审计并继续其他来源；空结果不是故障 |

联机 smoke 使用最低相关性分数 `4.0`。排序是确定性词法信号，只用于候选控制，不是论文质量评分。

## 3. 身份与去重

Canonical Paper ID 优先使用规范化 DOI，其次 arXiv、OpenAlex、Semantic Scholar，最后才使用保守 fallback。精确 ID 相同才直接合并；无强 ID 时要求规范化标题、第一作者和年份同时一致。两个不同独立 DOI 即使标题相同也保留为不同 Paper。

arXiv/独立 DOI 记录可以建立 `version_of` 候选关系，但自动推断必须标记 `review_required=true`。`evaluation/seeds/m1_dedup_gold.json` 冻结了精确合并、fallback、冲突保留和版本关系样例。

## 4. Baseline 定义

- B0：只使用第一个可用来源的元数据和摘要；
- B1：使用多源归一化、去重和排序后的元数据与摘要；
- deterministic_extractive：固定模板控制组，验证快照和内容复现；
- ollama_qwen3_8b：本地 `qwen3:8b` 结构化生成，关闭 thinking、temperature 设为 0、请求上下文 8,192；
- 本地生成的引用 ID 必须属于实际传给模型的最多 10 个候选，否则最多重试 2 次后失败关闭；
- 两类 Baseline 都不是全文证据。M2 才建立基于 DocuMind 的全文路径。

## 5. 可复现 Artifact

运行目录 `artifacts/m1-search/` 被 Git 忽略，包含 SearchSnapshot、B0/B1 JSON 与 Markdown、RunManifest 和本地模型扩展 RunManifest。可提交报告只保存非敏感汇总：

- `evaluation/reports/m1_live_search_smoke.json`；
- `evaluation/reports/m1_local_baseline_smoke.json`。

SearchSnapshot 的候选集哈希排除采集时间，因此相同来源内容在缓存和离线重放时保持同一身份。Markdown 固定使用 LF 写入，`content_sha256` 与磁盘文件字节一致。

## 6. 运行

离线测试和 Schema 校验：

```powershell
uv sync --all-groups
uv run python scripts/export_schemas.py
uv run pytest --basetemp artifacts/pytest
uv run ruff check .
uv run mypy src
```

固定快照离线重放：

```powershell
uv run python scripts/run_m1_search.py `
  --replay-snapshot artifacts/m1-search/search_snapshot.json
```

本地模型 Baseline：

```powershell
uv run python scripts/run_m1_local_baselines.py `
  --summary-output evaluation/reports/m1_local_baseline_smoke.json
```

脚本对本机 Ollama 使用直连，不继承系统 HTTP 代理。外部学术 API Client 保持环境代理兼容。

## 7. 冻结结果

公开三源 smoke：arXiv、OpenAlex、Crossref 各返回 3 条记录；9 条来源记录归一化并经最低相关性门槛后保留 6 篇 Paper。首次请求、缓存重放和离线快照重放均通过，候选集 SHA-256 为 `f0f8594e305095b3e1c4e3a20aee63c33df57b84bb2effcbb8df6cf747995494`。

OpenAlex 在响应中报告 `0.001 USD` Provider 成本，该值不是账单费用；本阶段付费 API 和模型账单成本均为 `0 CNY`。

本地 `qwen3:8b`（ID `500a1f067a9f`、Q4_K_M、模型上下文 40,960）实测：

| Baseline | 引用候选数 | 输入 Token | 输出 Token | 调用 | 墙钟时间 |
|---|---:|---:|---:|---:|---:|
| B0 | 2 | 967 | 414 | 1 | 13.851 秒 |
| B1 | 5 | 1,809 | 486 | 1 | 15.517 秒 |

两次生成均通过候选 ID 白名单和 Pydantic 校验；扩展 RunManifest 记录 2 次模型调用、2,776 输入 Token、900 输出 Token 和 0 CNY。GPU 时间在 M1 尚未从墙钟时间中独立测量。

## 8. 阶段结论

固定输入可重放、去重黄金集通过、单源故障可降级、连续非法模型引用会失败关闭，且提交内容不含 Key、原始 Provider 响应、原始模型信封或论文全文。M1 工程结论为 `PASS`。

阶段已停止。M2 的 DocuMind 全文证据闭环必须在用户明确确认后开始。
