# M2 DocuMind 证据闭环 MVP

> 日期：2026-08-29
> 工程复审：PASS
> 阶段状态：M2 工程验收通过，M3 尚未开始

## 1. 范围

M2 建立从已归一化 Paper 到可定位 Evidence 报告的单流程闭环：DocuMind 单论文检索 Consumer、active index 绑定、PaperCard/Claim 提取、Evidence provenance、报告、审计、预算和 RunManifest。

M2 核心流水线不实现通用论文 acquisition、LangGraph Agent 编排、引用网络、独立语义 Verifier、ScholarGraph 或付费 API 模型。三论文 Fixture 只验证契约与确定性边界；独立在线验收脚本使用锁定版本的公开 arXiv PDF，执行受限下载、DocuMind 入库和真实 Evidence 闭环。

## 2. 冻结契约

| 项目 | 冻结值 |
|---|---|
| Retrieve Schema | `1.0` |
| Retrieval version | `dense-v1` |
| 最低 Provider | DocuMind `2.1.0` / `32c5eb8` |
| 已验证兼容 Provider | DocuMind `2.2.0` / `212f60a` |
| 单请求范围 | 一个 `document_key` + 一个 `expected_index_id` |
| Consumer 尝试 | 1-3 次；M2 默认 2 次 |
| 响应大小 | 默认不超过 2 MB |
| Paper 并发 | M2 smoke 上限 2 |
| Paper 数量 | 每次 3-5 篇 |

`evaluation/reports/m2_documind_compatibility.json` 通过 `git show` 读取两个冻结 Provider Schema，并使用同一组有效/无效请求验证 Provider JSON Schema 与 ScholarTrace Consumer。2.1.0 与 2.2.0 均通过；DocuMind 当前工作树有其他未提交修改，未被修改、回退或纳入协议。

## 3. 证据闭环

1. SQLite 以 `canonical_paper_id` 保存唯一 document/index/source 绑定；active index 变化必须显式 CAS；
2. Client 检查 readiness，并只向 `/api/v1/retrieve` 发送单文档请求；
3. 返回的 document、index、source、服务版本、Chunk hash、唯一 ID 和连续 rank 全部严格校验；
4. 空 Chunk 是成功业务结果，但流水线不调用模型、不生成 Claim；所有论文检索完成后才进入生成阶段；
5. 本地 Qwen 从最多 6 个、每个最多 1,000 字符的受限 Chunk excerpt 中生成 PaperCard/Claim 草稿；
6. 模型只返回 `chunk-N` 与 `quote-N`，Consumer 映射回真实 Chunk ID 和输入原文；
7. Evidence 确定性计算 quote hash、Chunk hash、字符偏移、ID 和 DocuMind provenance；
8. 三到五篇 Paper 汇总为带 paper/page/chunk/evidence level 的 Markdown 报告；
9. 报告、检索审计、模型用量与 RunManifest 经过预算校验后原子写入。

短引用是实机失败分析后的可靠性设计。第一次本地 smoke 显示模型会在 64 位 Chunk ID 中途结束，第二次显示模型会改写长引文；两种结果都被原校验失败关闭。最终接口不再要求概率模型复制长哈希或原文，字节级 provenance 由 Consumer 保证。

## 4. 测试覆盖

M2 自动测试覆盖：

- Provider/Consumer Schema、未知字段、服务版本和 error envelope；
- SQLite 幂等写入、active index CAS、document identity 不可变；
- stale index、503 capacity、timeout、超大响应和空 Chunk；
- document/index/source/version 污染、非法 Chunk hash、重复 ID 和 rank 跳号；
- 未提供 Chunk/quote 短引用、跨论文响应和全局 ID 冲突；
- 三论文完整流水线、页码/Chunk 报告、预算汇总和 Artifact 字节哈希；
- 超预算在任何成功 Artifact 写入前失败。

最终结果：`81 passed in 3.01s`；Ruff PASS；Mypy 对 22 个源码文件 PASS；`uv lock --check` 与 `git diff --check` PASS。48 个提交候选 JSON 可解析，28 个 JSON Schema 通过 Draft 2020-12 检查，OpenAPI 合法，Markdown 围栏和凭据扫描通过；`agent/`、`artifacts/`、`data/` 未被追踪。

## 5. 本地模型 Smoke

可提交摘要：`evaluation/reports/m2_local_fixture_smoke.json`
忽略的完整 Artifact：`artifacts/m2-evidence-fixture/`

| 指标 | 结果 |
|---|---:|
| 真实论文身份 | 3 |
| Fixture 检索尝试 | 3 |
| 生成 Evidence | 3 |
| 本地模型调用 | 3 |
| 结构修复重试 | 0 |
| 输入 Token | 1,009 |
| 输出 Token | 903 |
| 模型调用耗时总和 | 49.340 秒 |
| 并发流水线墙钟 | 29.600 秒 |
| 计费成本 | 0 CNY |

模型为本地 `qwen3:8b`，ID `500a1f067a9f`，Q4_K_M，请求上下文 8,192，单次输出上限 2,048。三篇成功结果均通过短引用白名单、单论文范围、exact quote、字符偏移和内容 hash 校验。GPU 时间仍未从墙钟时间中独立测量。

## 6. 在线全文 Smoke

可提交摘要：`evaluation/reports/m2_live_documind_smoke.json`
忽略的 PDF、绑定和完整 Artifact：`artifacts/m2-live-documents/`、`artifacts/m2-evidence-live/`

| 指标 | 结果 |
|---|---:|
| DocuMind | `2.2.0/212f60a` |
| 在线 readiness | HTTP 200，retrieval ready |
| 版本化公开 arXiv PDF | 3 篇，2,907,739 bytes |
| DocuMind 索引 Chunk | 617 |
| 在线返回 Chunk | 15 |
| 生成 Evidence | 11 |
| 检索尝试 | 3，全部一次成功 |
| 检索耗时总和 | 0.650 秒 |
| 入库耗时 | 114.253 秒 |
| 本地模型调用 | 3 |
| 结构修复重试 | 0 |
| 输入 / 输出 Token | 2,839 / 1,642 |
| 模型耗时总和 | 73.291 秒 |
| 流水线墙钟 | 44.564 秒 |
| 端到端墙钟 | 159.630 秒 |
| 计费成本 | 0 CNY |

在线运行使用本地 `qwen3-embedding` 4096 维向量与 Milvus，随后由本地 `qwen3:8b` 分析。初次运行暴露单 GPU 换模竞争：检索和生成交错时，Embedding 查询可能在生成模型占用期间超时。最终流水线加入全局检索屏障，所有 `/retrieve` 完成后才启动生成，在线 smoke 通过。

DocuMind 默认 2 秒 Embedding readiness 探针小于本机约 8 秒冷加载时间。验收脚本以 `keep_alive=10m` 显式预热已安装模型后再执行有界 readiness 检查；这里的“卸载”仅指移出 RAM/VRAM，不删除磁盘模型。该限制属于本地启动运维注意事项，不改变检索契约。

## 7. 阶段结论

M2 工程闭环满足范围隔离、Evidence 定位、跨论文污染 fail-closed、模型输出约束、预算、可复现 Artifact 和三篇公开全文在线验收要求，阶段结论为 `PASS`。

保留限制：在线样本只有三篇 DEV-01 论文；M2 验证 exact quote、身份、位置和 hash，不承担 M4 的独立语义蕴含判断；GPU 时间尚未从墙钟时间独立测量。本次三篇公开论文索引按 `--keep-documents` 保留在本机 DocuMind，未写入 Git。
