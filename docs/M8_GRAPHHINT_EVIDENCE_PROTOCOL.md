# M8-G GraphHint 查漏补缺与 Evidence 增益协议

> 状态：已完成（Gate A `NO_GO / keep_disabled`）<br>
> 前置结论：M6 B3/B4 与 M7-G 保持冻结，不得回改<br>
> 默认决策：ScholarGraph 继续关闭，除非本协议的分层门禁依次通过

## 1. 目标与职责

M8-G 验证一条新的、可归因的路径：ScholarGraph 是否能利用实体、关系和论文映射，
从 B5 初始结果之外发现遗漏候选，并让这些候选在 DocuMind 全文链路中形成新增的有效
Evidence。GraphHint 只负责候选发现，不生成最终答案，也不能直接成为 Evidence。

新评测门禁只改善测量：它通过预先定义的问题分层减少简单题集的满分天花板，不得降低
B5 的 top-k、输入质量或预算，也不得先运行 B5 再选择其失败题。

## 2. 对照条件

- B5：冻结的文档 title/text 检索基线；
- B6：与 B5 使用相同问题、语料、seed top-k、预算和边界规则，只追加结构化图扩展候选；
- B6 必须从 B5 初始候选和查询实体出发，执行最多一跳、可配置但默认关闭的两跳扩展；
- B6 返回 B5 seed 加最多 1 个候选 OpenAlex ID、标题、来源 document、关系路径、
  匹配信号和确定性分数；GraphHint 不挤掉 B5 已找到的结果；
- B5/B6 都不读取生成答案，不调用模型，不访问网络，不修改 GraphRAG 索引；
- 当前固定 198 篇、2020-2025 年英文 RAG 摘要语料和 GraphRAG 3.1.2 不变。

## 3. 题集设计

问题先按真实关系需求定义分层，再从语料身份和结构中抽样，不依据 B5 运行结果筛题：

1. `alias_bridge`：问题术语与论文标题不同，但由实体别名或描述连接；
2. `relation_bridge`：需要方法-任务、方法-指标或方法-数据集关系定位论文；
3. `paper_neighbor`：从已知论文或方法沿共享实体发现相关论文；
4. `multi_concept`：需要覆盖两个不同概念簇，防止单一高频词占满 top-k；
5. `negative_boundary`：超语料、未来年份、全文数值和写入请求，必须返回零候选。

开发集可用于实现和调参。最终 holdout 必须在算法及阈值冻结后一次性运行；其问题、gold、
top-k 和评分代码必须先哈希冻结。holdout 不得包含开发集论文身份，也不得根据 B5/B6 结果
增删样本。每个正向分层至少两题，且报告逐题保留 B5 漏检、B6 补回和 B6 新增误检。

## 4. 分层门禁

### Gate A1：来源与算法完整性

- source ref 回链有效率 100%；
- boundary 为零候选；
- 重复运行结果、关系路径和排序签名一致；
- 正式 Parquet 运行前后哈希一致；
- 每个 B6 新增候选均有非空、可回链的关系路径；
- 不运行网络、模型或付费调用。

### Gate A2：候选发现增益

- holdout 的 B6 candidate recall 严格高于 B5；
- B6 question coverage 不低于 B5；
- B6 候选池 precision 不低于 B5 seed precision 超过 0.10；
- 至少两个正向分层出现真实补回，不能由单题决定结论；
- B5/B6 使用完全相同的 seed top-k 和输入约束，B6 每题最多增加 1 个 GraphHint 候选。

Gate A1 或 A2 失败即 `keep_disabled`，不进入 DocuMind、在线 API 或付费质量评测。

### Gate B：DocuMind Evidence 转化

只有 Gate A 通过并取得人工确认后才启动。ScholarTrace 对 B6 相比 B5 新增且 gold 有效的
候选执行公开来源身份解析、获取全文、DocuMind ingest/retrieve、Validator 和 Verifier。

- 新增候选的全文身份和 source hash 必须闭合；
- 至少一条新增候选形成通过 Validator 的 Evidence；
- holdout 的有效 Evidence 覆盖或 Claim 支持率严格提高；
- GraphHint、摘要和关系路径本身不得计为 Evidence；
- 任何 API 强模型调用需另行给出调用数、参考成本和人民币硬上限。

### Gate C：最终质量与产品代价

只有 Gate B 通过才设计盲审或 Shadow Mode。比较最终质量、延迟、费用和失败率；未观察到
最终质量收益时只保留实验能力，不默认启用 ScholarGraph。

## 5. 决策与停止条件

```text
B5 漏检
  -> B6 通过图路径补回候选
  -> DocuMind 形成新增有效 Evidence
  -> Claim 支持率或最终盲审质量提高
```

- 只提升 candidate recall：说明图查漏补缺有效，不等于系统正增益；
- 未提升有效 Evidence：停止在 Gate B，继续默认关闭；
- 未提升最终质量或代价不可接受：停止在 Gate C，继续默认关闭；
- 三层均通过并经人工复审：才讨论受控在线接入；
- 现有 198 篇存在明确语料缺口时，另行申请定向增加 50-100 篇，不先扩到 1000 篇。

## 6. 交付物

- ScholarGraph 阶段 19 图扩展实现、测试、开发集和冻结 holdout 报告；
- ScholarTrace Gate A 结果镜像、Gate B 回放器及 Evidence 增益报告（仅在 Gate A 通过时）；
- 两仓阶段复审、失败样本和可复现命令；
- 不包含 API Key、论文全文、模型原始回答或私有盲审映射。
