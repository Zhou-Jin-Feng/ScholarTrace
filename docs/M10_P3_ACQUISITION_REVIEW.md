# M10-P3 全文获取与 DocuMind 闭环复审

> 日期：2026-09-04
> 阶段：M10-P3
> 结论：`PASS WITH NOTES`

## 1. 范围与来源边界

本阶段只实现版本化公开 arXiv 全文的受限 acquisition。`Paper.sources` 中必须存在唯一、
带版本号的 arXiv `abs` 地址（例如 `2401.15884v3`）；Consumer 将其转换为 HTTPS PDF 地址。
允许的最终域名只有 `arxiv.org` 和 `export.arxiv.org`，不接受 HTTP、用户信息、非 443 端口、
跨域跳转或未版本化地址。

ScholarTrace 不绕过登录、付费墙、robots/访问控制或版权限制；DOI、OpenAlex、Crossref 和
ScholarGraph 摘要不是本阶段的全文下载地址。未能取得合法公开全文时，任务应保留结构化失败，
不得用摘要冒充 `fulltext Evidence`。

## 2. 已实现行为

| 领域 | 已验证行为 |
|---|---|
| 来源 | 版本化 arXiv 身份解析，固定 HTTPS PDF URL |
| 重定向 | 手动逐跳跟随，最多 3 跳；每跳重新校验 HTTPS、允许域名和端口 |
| 类型 | 要求 `Content-Type: application/pdf`（允许参数），并检查 `%PDF-` 魔数 |
| 大小 | `Content-Length` 预检查和流式累计检查，最大 30 MiB |
| 文件 | 同目录临时文件，完整校验后原子替换；失败自动清理临时文件 |
| 幂等 | 已有有效同版本文件直接复用并重新计算 SHA-256；损坏文件只在新文件校验成功后替换 |
| provenance | acquisition 产生 `PdfAcquisition.sha256`；ingest 前后均校验本地 hash，DocuMind 返回 hash 不一致时 fail closed |
| DocuMind | upload/status/retrieve 保持单 `document_key/index_id`；绑定写入使用 active index CAS |
| 清理 | 只清理本次新建文档；DELETE 404 视为已清理；其余失败会继续尝试并汇总失败键 |

实现位置：`src/scholartrace/evidence/live.py`。兼容旧调用的 `download_pdf()` 仍返回
`(Path, size)`；需要完整 provenance 时使用 `acquire_pdf()`。

## 3. 自动验证

- `uv run pytest --basetemp .pytest-tmp/m10-p3-final-special tests/test_m10_acquisition.py tests/test_m2_live_smoke.py`
  -> `14 passed`；
- 新增覆盖：fulltext access-level、有效 PDF hash 与复用、Content-Type、Content-Length
  不一致、流式超大响应、跨域跳转、跳转循环、Provider hash 不一致、清理 404/部分失败；
- `uv run ruff check src/scholartrace/evidence/live.py tests/test_m10_acquisition.py scripts/run_m2_live_smoke.py`
  -> PASS；
- `uv run mypy src/scholartrace/evidence/live.py scripts/run_m2_live_smoke.py` -> PASS；
- `git diff --check` -> PASS。
- 全量回归：`uv run pytest --basetemp .pytest-tmp/m10-p3-release` -> `216 passed`；
- 在线 readiness：DocuMind `2.2.0` 的 application/milvus/embedding/llm/registry/retrieval
  全部 `ready`；Ollama `11434/api/tags` 列出 `qwen3:8b` 与 `qwen3-embedding`。
- DocuMind 工作树已补充有界冷启动策略：Embedding 请求与 readiness 都发送
  `keep_alive=600`，Embedding readiness 使用独立 60 秒硬上限，检索自身 15 秒预算保持不变。
  强制 `keep_alive=0` 卸载并等待健康缓存失效后，readiness 在 7.739 秒恢复
  `embedding/retrieval=ready`；另一次独立冷加载观测为 38.662 秒，证明旧 2 秒和候选 15 秒
  探针都不足以覆盖本机冷路径。
- 真实在线 smoke：`uv run python scripts/run_m10_p3_smoke.py` -> `passed=true`；3 篇公开
  arXiv、2,907,739 bytes、4096 维 embedding、617 个索引 Chunk、15 个返回 Chunk、11 条
  Evidence、3 次检索全部成功、0 次结构修复、0 CNY；冷启动修复后的最终复跑端到端
  101.199 秒，纯检索 1.676 秒。
- SQLite binding 核对：3/3 `source_sha256` 与在线 summary 完全一致，3/3 active
  `index_id` 闭合；`new_documents=0`，本次复用既有 DocuMind 文档且未删除既有索引。

既有 M2 在线 smoke 已改为调用同一 `acquire_pdf()`，把每篇 PDF 的 `source_sha256` 传入
ingest 前置校验并写入脱敏 summary；M10-P3 入口为：

```powershell
uv run python scripts/run_m10_p3_smoke.py
```

该命令默认读取锁定的三篇 `tests/fixtures/documind/m2_three_papers.json`，访问公开 arXiv、
本地 DocuMind `2.2.0` 和 Ollama；PDF、绑定和完整报告只写入已忽略的 `artifacts/`。不传
`--keep-documents` 时仅删除本次新建的 DocuMind 文档。

## 4. 人工复审清单

1. 确认三篇锁定 arXiv 版本仍可在当前网络合法公开访问；
2. 在 DocuMind `2.2.0` readiness 为 ready、Ollama embedding 已预热时运行 P3 smoke；
3. 检查 summary 中每个 `pdf_source_sha256` 与对应 DocuMind binding 的 `source_sha256` 相同；
4. 检查成功后任务能以同一 binding 完成单论文 retrieve，且无跨论文 evidence；
5. 故意让一次响应返回 HTML、跨域跳转或错误 hash，确认任务失败关闭且 `.tmp` 不残留；
6. 确认清理失败时报告 `failed_document_keys`，不会误删既有文档。

## 5. 遗留问题与边界

- 本阶段只支持 arXiv；出版社、机构仓储和 DOI landing page 需要逐来源合法性与许可策略，
  不应通过放宽 host allowlist 解决；
- 本次已完成真实 arXiv 与 DocuMind 在线闭环；在线结果只代表三篇锁定版本和当前本机部署，
  不外推到其他来源或更大规模吞吐；
- 不提供断点续传。部分下载会被清理并重新开始，避免拼接未经完整校验的内容；
- 30 MiB 是单文件保护上限，不代表论文内容质量或 DocuMind ingestion 吞吐量；
- DocuMind 的 600 秒驻留与 60 秒 readiness 都是本地单 GPU 的有界运维策略，不保证
  永久驻留，也不能消除 Ollama 驱逐、模型切换或显存不足；DocuMind 变更仍需独立提交/发布；
- 后续 M10-P4 已补齐安装、备份、恢复和发布演练；ScholarGraph 仍默认关闭。

## 6. 阶段结论

代码、契约边界、故障注入、静态检查和三篇真实在线闭环达到 `PASS WITH NOTES`。P3 的剩余
note 是当前仅支持公开 arXiv，且 DocuMind 容量/恢复仍是独立上游边界。后续发布与恢复结论见 `docs/M10_P4_RELEASE_REVIEW.md`。
