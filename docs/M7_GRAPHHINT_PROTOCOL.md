# M7-G GraphHint 前移与 B5/B6 实验协议

> 状态：Gate A 已完成，严格结论 `NO_GO / keep_disabled`<br>
> 日期：2026-08-31<br>
> 默认决策：`keep_disabled`

## 1. 目标与非目标

M6 的 B3/B4 已证明把 ScholarGraph 摘要答案放在最终 Synthesis 前没有可见质量增益，且增加延迟。M7-G 不回改该实验，而是验证另一条因果路径：GraphRAG 是否能在研究早期发现候选论文或 Evidence 缺口，从而改善后续 Search/FollowUp 覆盖。

本阶段不把摘要升级为 Evidence，不用生成文本猜测引用，不扩正式语料，不开放 Global/DRIFT，不改变 `qwen3:8b`、GraphRAG `3.1.2` 或 198 篇固定语料基线。

## 2. 独立对照

- B5：现有 Search/FollowUp，不接收 GraphHint；
- B6：条件、预算和查询限制与 B5 相同，只增加经过确定性验证的 GraphHint；
- B3/B4 的问题、报告、盲审映射和评分保持冻结，不纳入 B5/B6；
- 所有 GraphHint 只生成候选 OpenAlex ID 和查询线索，进入 Evidence 前仍必须经过公开学术源解析、论文身份归一化、DocuMind 全文检索、Validator 和 Verifier。

## 3. 两道实施门禁

### Gate A：离线来源与发现验证

ScholarGraph 在独立工作树读取已经哈希冻结的 `documents`、`text_units`、`entities` 和 `relationships` Parquet。文档文件名必须精确匹配 `openalex_W<digits>.txt`，OpenAlex ID 必须在正式原始清单中唯一存在；document -> text unit -> entity/relationship 全部回链有效。

候选生成只能使用结构化表、查询文本和确定性评分。不得解析模型答案中的表面引用，也不得生成不存在的 ID。映射异常、结构漂移、空信号或越界请求都输出空 hints。

Gate A 通过条件：

- 返回的 `source_refs` 100% 能回链到冻结语料；
- 重复运行结果和排序完全一致；
- boundary 问题为 0 hints；
- B6 在新困难题集的 candidate recall/coverage 相对 B5 有正增益，且保留全部失败样本；
- 离线实现不修改正式索引文件。

Gate A 实测结果：198/198 document 唯一映射、source validity 100%、4 道 boundary
零 hints、重复签名一致且六张 Parquet 哈希不变；但算法冻结后 holdout 的 B5/B6
candidate recall 均为 1.00，增益为 0。因此 Gate A 未通过，详细证据见
`docs/M7_GRAPHHINT_REPORT.md`。

### Gate B：受控在线接入

只有 Gate A 通过才允许 ScholarGraph 在线响应携带 `source_refs`。ScholarTrace 默认仍拒绝未配置来源目录的非空引用；M7 Consumer 必须逐项校验 document ID、OpenAlex ID、标题、语料 manifest 和重复项，任一不一致即整次 GraphHint 失败并回退 B5。

在线接入后先执行 Fixture 和本地零付费 Basic pilot。任何强模型质量评测必须重新给出问题规模、调用次数、参考成本和硬上限，取得人工批准后才运行。

本轮因 Gate A 未通过，Gate B 未启动；ScholarGraph 继续返回 `source_refs=[]`，
ScholarTrace 未实现 GraphHint Consumer，也没有模型或付费调用。

## 4. GraphHint 数据边界

GraphHint 至少记录：稳定 hint ID、问题 hash、语料 manifest、已验证 source refs、候选 OpenAlex ID、用于 Search 还是 FollowUp、生成状态和失败原因。它不包含论文全文、模型原始答案或可直接引用的 Evidence。

Search 只能把已验证标题/ID作为附加查询线索；FollowUp 只能把已验证候选映射到 `target_paper_ids`。GraphHint 不能绕过候选数量、查询轮数、API 调用数、墙钟时间或费用上限。

## 5. 最终决策

- Gate A 或 Gate B 任一失败：`keep_disabled`，保留 B5 主路径；
- 只改善候选发现但未改善最终 Evidence 覆盖：保持实验功能，不进入默认路径；
- 只有来源有效、覆盖有可复现增益、延迟/失败代价可接受，且人工复审通过后，才讨论受控默认启用；
- 无论结论如何，不修改 M6 的负结果。

## 6. 冻结结论

M7-G 离线交付经 ScholarGraph Phase 18 复审为 `PASS WITH NOTES`，但这只说明实验资产
完整可信。严格收益结论为 `NO_GO / keep_disabled`：开发集 B6 相对 B5 的 recall 从
0.70 提升到 1.00，但该 treatment 同时包含图评分、PAPER 身份、标题去重和多样化选择；
算法冻结后 holdout 的 B5/B6 均为 1.00，未建立可泛化正增益。

因此不进入 Gate B、不开放在线 `source_refs`、不修改 ScholarTrace 调用链、不执行
付费质量评测。未来只有语料扩展或出现真实候选漏检需求时，才使用新的独立题集重开
验证；不得回改 M6 B3/B4 或本轮 holdout 来追求正结果。
