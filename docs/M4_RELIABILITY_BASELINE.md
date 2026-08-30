# M4 引用网络与证据核验基线

> 日期：2026-08-30
> ScholarTrace：`0.4.0`
> 工程复审：`PASS WITH NOTES`
> 阶段状态：M4 基线冻结，M5 尚未开始

## 1. 范围

M4 增加显式引用扩展、引用论文生命周期、确定性 Citation Graph、Evidence Validator、独立四态 Verifier、报告 Claim 门禁和最多一轮定向补查。Citation Graph 只使用学术 API 的显式书目边，不使用 ScholarGraph 生成式语义边。

本阶段不配置付费 `api-strong`，不把 Fixture Verifier 当作真实语义模型；OpenAlex 只执行有界在线兼容性 smoke，不把单控制 seed 当作引用召回率基准，也不进入 M5 ScholarGraph 集成。

## 2. 冻结实现

| 项目 | M4 冻结值 |
|---|---|
| ScholarTrace | `0.4.0` |
| Citation Provider | OpenAlex 必选实现；Semantic Scholar 可选、未启用 |
| Online control seed | FLARE / `W4389519118`，不替换研究评测种子 |
| 引用方向 | `citing -> cited` |
| 引用扩展上限 | 单次最多 100 个目标论文元数据 |
| 图实现 | `networkx==3.6.1` 基础包 |
| PageRank | damping 0.85、最多 100 次、tolerance `1e-12` 的确定性幂迭代 |
| 生命周期 | normalized、relevance、access、acquire、ingest、analyze 顺序门 |
| 核验顺序 | deterministic Validator -> profile-gated Verifier -> report gate |
| Verifier 状态 | supported、partially_supported、unsupported、conflicted |
| FollowUp | 全局最多 1 个，`round_index=1`，`max_additional_queries=1` |

## 3. 引用图与降级语义

OpenAlex Provider 先批量读取 seed works 和 `referenced_works`，再对受限引用目标读取论文元数据。每条边保存 source work、公开 request ID、响应 SHA-256 和采集时间。API Key 不进入公开参数、审计或缓存键。

以下情况不会被解释为完整空图：

- seed 没有 OpenAlex ID；
- 响应缺少 `referenced_works` 字段；
- 引用目标超出上限或没有返回元数据；
- 目标元数据请求失败。

这些情况进入 `degraded/failed` 和 missing/unresolved 列表；系统不使用语义边补造引用。

## 4. Validator 与报告门禁

Validator 在任何语义调用前检查：

- Claim 引用的 Evidence 是否存在；
- Evidence Paper、`version_of` 目标和引用论文生命周期；
- fulltext Evidence 与 active DocuMind Binding 的 document/index/source；
- quote hash、Chunk ID/hash、页码、字符范围和逐字 quote；
- Claim 中的数值是否出现在支持证据；
- 明确要求的 `citing -> cited` 边是否存在。

确定性失败直接生成 `unsupported` Verification，不调用语义后端。报告层排除所有 unsupported Claim；partially_supported 和 conflicted Claim 只有带可见标记时才能进入报告。该规则强于“unsupported 关键 Claim 不得无标记进入报告”的最低验收要求。

## 5. 零成本 Fixture 结果

命令：

```powershell
uv run python scripts/run_m4_fixture_smoke.py
```

脱敏结果：`evaluation/reports/m4_reliability_fixture_smoke.json`

| 指标 | 结果 |
|---|---:|
| 外部学术 Provider 调用 | 0 |
| 模型 Provider 调用 | 0 |
| Fixture Verifier 调用 | 1 |
| Citation nodes / edges | 2 / 1 |
| PageRank 总和 | 1.0 |
| 冲突状态与可见标记 | `conflicted` / `[CONFLICTED]` |
| 缺失引用边 | `citation_edge_missing` |
| 被拦截关键 Claim | 1 |
| FollowUp | 1 个，第 1 轮，最多 1 个查询 |
| Smoke | `passed=true` |

Fixture 故意包含冲突和缺失引用边，因此流水线业务 outcome 为 `degraded`、Validator outcome 为 `failed`；这证明降级与拦截路径按设计工作，不是 smoke 失败。

## 6. OpenAlex 有界在线结果

命令：

```powershell
uv run python scripts/run_m4_openalex_live_smoke.py
```

脱敏结果：`evaluation/reports/m4_openalex_live_smoke.json`

最初使用两篇冻结 M2 preprint seed 时，OpenAlex 明确返回空 `referenced_works`；字段存在且类型正确，因此不是 Provider 解析失败。随后使用与自适应检索主题相关、书目边已确认存在的 FLARE 正式论文作为独立兼容性控制 seed。该 seed 只验证 Citation Provider，不替换 DEV/EVAL 研究论文。

| 指标 | 结果 |
|---|---:|
| credential mode | anonymous |
| seed | 1 |
| 逻辑请求 / 网络 attempts | 2 / 2 |
| 请求状态 | 2 succeeded |
| 显式 citation edges | 62 |
| 目标元数据请求上限 | 10 |
| 返回目标元数据 | 8 |
| 上限内解析率 | 0.8 |
| 因上限主动延后 | 52 |
| 请求但未返回 | 2 |
| missing reference list | 0 |
| edge provenance | complete |
| cache-only replay | passed |
| Provider 报告成本 | USD 0.0002 |
| 账单成本 | CNY 0.0 |
| Smoke | `passed=true` |

Provider outcome 为 `degraded`，因为系统主动只获取 10/62 个引用目标元数据，并对未扩展节点保留 unresolved 状态；两次网络请求本身均成功。这是预算内部分图的正确语义，不是在线验收失败。

## 7. 自动检查

2026-08-30 阶段复审：

```text
pytest: 112 passed
ruff: PASS
mypy --strict: PASS
JSON Schema source parity: PASS
OpenAPI validation: PASS
repository hygiene / credential scan: PASS
git diff --check: PASS
```

聚焦测试覆盖 OpenAlex 两段式扩展、轻量 CitationSeed、缺边降级、API Key 脱敏、在线 summary、缓存重放、生命周期不可跳步、图 hash/指标确定性、绑定/Chunk/数值/引用错误、四种 Verification 状态、禁用 Profile fail closed、报告门禁和 FollowUp 预算。

## 8. 人工复审

- [x] Citation Graph 与 ScholarGraph Semantic Graph 没有混称；
- [x] 引用边方向、来源、请求与响应 hash 可追溯；
- [x] 新论文不能跳过 DocuMind ingest 直接分析；
- [x] 确定性失败不会调用 Verifier；
- [x] conflicted Claim 显示冲突标记；
- [x] unsupported 关键 Claim 不进入报告；
- [x] FollowUp 不会超过一轮或一个追加查询；
- [x] Fixture 未声明真实语义质量或 Provider 覆盖率。
- [x] 在线报告不含标题、摘要、原始响应、缓存路径或 API Key；
- [x] 主动限制引用目标元数据时保持 degraded，不把部分图误报为完整图。

## 9. 限制与结论

- OpenAlex Citation Provider 已通过匿名在线兼容性 smoke，但单个控制 seed 和 10 个目标元数据上限不能估计完整引用召回率；
- `api-strong` 仍禁用，生产关键 Verifier 明确失败关闭；真实四态准确率、Token、延迟和费用未评测；
- 生命周期门已冻结顺序和 Artifact/Binding 不变量，通用合法 acquisition 与完整 Research Task HTTP 装配仍由后续应用层完成；
- Fixture 证明工程控制流，不证明论文结论的语义正确性。

M4 工程结论为 `PASS WITH NOTES`。M5 ScholarGraph 能力受限集成与对照实验尚未开始。
