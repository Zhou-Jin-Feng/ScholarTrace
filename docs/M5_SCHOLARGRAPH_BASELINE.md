# M5 ScholarGraph 能力受限集成基线

> 状态日期：2026-08-30
> ScholarTrace：`0.5.0`
> ScholarGraph：`1.2.0/3aa5e2a`
> 阶段结论：`PASS WITH NOTES`

## 1. 阶段目标与边界

M5 在 ScholarTrace 内实现 ScholarGraph 的能力受限 Consumer、确定性路由和 B3/B4 配对评测框架。ScholarGraph 仍是可选工具，不是子智能体，也不拥有研究任务、论文身份、全文 Evidence 或报告决策。

冻结语料为 198 篇 2020-2025 年英文 Retrieval-Augmented Generation 摘要，证据等级为 `abstract`。Provider 输出只能作为辅助上下文或检索线索，不能写成已核对全文的 Evidence。

## 2. 冻结上游与契约

| 项目 | 冻结值 |
|---|---|
| ScholarGraph commit | `3aa5e2a0f57efa173cf633808bc2d370f519907c` |
| 服务版本 | `1.2.0` |
| GraphRAG | `3.1.2` |
| Corpus ID | `openalex-rag-abstracts-2020-2025-v1` |
| Corpus version | `formal-2026-08-26` |
| 文档数 | 198 |
| 年份 | 2020-2025 |
| 证据等级 | `abstract` |

`contracts/openapi/scholargraph-v1.openapi.json` 覆盖 live、ready、capabilities、metrics 和 query 五个操作。兼容报告确认 Provider/Consumer 规范化后完全一致，契约 SHA-256 为 `a7bdd2b6c179e2c9b66578dcfdbbbeffd8b35001bb3b99e4d29f175597a4130d`。

## 3. Consumer 与路由

- Pydantic 严格模型拒绝未知字段、版本漂移、非法错误信封、过大响应和非空 `source_refs`；
- 健康、能力和指标 GET 最多尝试两次，间隔有界；
- POST query 不自动重试，避免重复执行昂贵的本地 GraphRAG 生成；
- Provider 方法 timeout 外加 15 秒 Consumer 响应宽限；
- Basic 是默认方法，Local 只用于实体邻域；Global/DRIFT 默认禁用；
- 主题、年份、全文需求、索引写入、方法用途、API 调用预算和剩余墙钟时间均在网络调用前检查；
- 跳过、拒绝、503、超时、失败和协议错误全部返回 `fallback_to_b3=true`。

## 4. B3/B4 评测设计

问题集包含 6 个 ScholarGraph eligible 问题和 6 个 boundary 问题。配对评测器要求 B3/B4 使用相同的问题集、模型 Profile、论文池 hash、Prompt hash、预算和报告限制；覆盖不全、条件不一致、B3 意外调用 ScholarGraph 或边界 B4 发起调用都会 fail closed。

Fixture smoke 仅验证路由与评测器：

| 指标 | 结果 |
|---|---:|
| 问题数 | 12 |
| eligible / boundary | 6 / 6 |
| Provider 调用 | 0 |
| 模型调用 | 0 |
| 可比性检查 | 通过 |
| 边界路由 | 通过 |
| 默认决策 | `keep_disabled_insufficient_quality_evidence` |

Fixture 行不是模型答案，不能用于声明质量收益。

## 5. 真实 Basic 联调

在本机 `http://127.0.0.1:8002` 对冻结 ScholarGraph `1.2.0` 执行一次受限 Basic 查询，并对六个边界请求进行网络前路由检查：

| 指标 | 结果 |
|---|---:|
| Readiness 四项检查 | 全部通过 |
| Basic 请求 | 1 次，首次成功 |
| Provider 状态 | `succeeded` |
| Provider 耗时 | 35.043 秒 |
| 查询 HTTP 耗时 | 35.049 秒 |
| 端到端耗时 | 36.857 秒 |
| Answer 大小 | 1,896 bytes |
| 可见 Token 下界 | 19 |
| Provider 报告成本 | 0 CNY |
| Boundary 网络调用 | 0 |
| Global / DRIFT 调用 | 0 |

公开报告只保存 answer 大小和 hash，不保存问题、答案、Prompt 或原始诊断。单次联调只证明接口和运行兼容，不是延迟容量基准。

## 6. 验证命令

```powershell
uv run python scripts/export_schemas.py
uv run pytest --basetemp artifacts/pytest
uv run ruff check .
uv run mypy src
uv lock --check
uv run python scripts/run_m5_fixture_smoke.py
powershell -ExecutionPolicy Bypass -File scripts/verify_m5_scholargraph.ps1
```

真实联调需要冻结服务已启动：

```powershell
uv run python scripts/run_m5_scholargraph_live_smoke.py
```

## 7. 阶段结论

M5 的契约、Consumer、Capability Router、B3 安全回退、问题集、评测器、Fixture 和真实 Basic 兼容联调均已完成。越界请求不会访问 Provider，昂贵查询不会自动重试，摘要输出不会进入全文 Evidence。

保留项：生产 `api-strong` Coordinator、关键 Verifier 和 Synthesis 仍禁用，因此无法诚实执行完整研究流水线下的真实同条件 B3/B4 质量对照；历史 ScholarGraph 成绩和 Fixture 结果不能替代该证据。ScholarGraph 继续默认关闭，M6 需先完成人工接受的真实配对评测，才可讨论默认启用。
