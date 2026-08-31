# M7-G GraphHint 离线门禁结果

> 日期：2026-08-31<br>
> ScholarTrace 结论：**PASS WITH NOTES**<br>
> Gate A 结论：**NO_GO / keep_disabled**<br>
> Gate B：未启动<br>
> ScholarGraph 证据：`feat/m7-source-refs@c18dbd2`

## 范围与依赖

M7-G 只验证 ScholarGraph 的结构化图是否能在 Search/FollowUp 前提供可回链的候选论文
线索，不修改 M6 已冻结的 B3/B4 答案、评分或结论。离线实现已提交到 ScholarGraph
`feat/m7-source-refs@c18dbd2`，读取固定 198 篇、2020-2025 年英文 RAG 摘要的只读
GraphRAG 3.1.2 Parquet 与 OpenAlex 原始清单。

B5 只使用 document title/text；B6 额外使用 entity、relationship、`PAPER` 身份、
规范化标题去重和未覆盖查询词多样化选择。两者均返回 top-3 候选。`source_refs` 只是
Search/FollowUp 线索，不是全文 Evidence。

## 结果

| 数据集 | B5 recall | B6 recall | B6-B5 | 结论 |
|---|---:|---:|---:|---|
| 初始开发题 | 0.80 | 0.80 | 0.00 | 图分数挤出既有候选 |
| 最终开发题 | 0.70 | 1.00 | +0.30 | 开发集通过 |
| 算法冻结后 holdout | 1.00 | 1.00 | 0.00 | 严格 Gate A 失败 |

完整性检查同时确认：

- 198/198 document 唯一映射到正式 OpenAlex work；
- 返回来源的 document ID、文件名、OpenAlex ID 和标题有效率为 100%；
- 4 道 boundary 在 B5/B6 下均返回 0 hints；
- 重复运行排序签名一致；
- 六张正式 Parquet 在运行前后 SHA-256 不变；
- 全程无网络、模型、索引写入或付费调用。

ScholarGraph 独立复审完成 15 项专项测试、207 项锁定环境全量测试、JSON/Markdown、
敏感信息、镜像构建和 614 文件交付校验，结论为 `PASS WITH NOTES`。

## 解释边界

开发集 B6 的 `+0.30` 是联合 treatment 效果，不能隔离归因为纯图结构增益。最终算法
冻结后，holdout 的 B5 已达到 100%，B6 没有新增召回空间。该结果不证明 GraphRAG
永远无价值，只证明当前固定语料、top-3 和问题集未建立可泛化的候选发现正增益。

离线资产交付通过也不等于产品功能应上线。M6 的 B3/B4 已显示最终 Synthesis 前的
摘要辅助无 eligible 质量增益且增加延迟；M7-G 又未通过独立候选发现门禁，两条路径均
不支持当前默认启用 ScholarGraph。

## 决策

- ScholarGraph 在线行为保持 `source_refs=[]`；
- ScholarTrace 不实现 GraphHint Consumer，不改变 Search/FollowUp 主路径；
- 不进入 Gate B，不修改服务版本，不执行付费质量评测；
- 保留 M6 B3/B4 和 M7-G 失败证据，不为追求正结果修改冻结题集；
- 未来仅在语料扩展或出现真实候选漏检需求时，以新的独立题集重新评估。

M7-G 基线已冻结。动态语料同步、MCP、Neo4j、
多用户、全文 GraphRAG 和多模态解析均不在本次实现范围内。
