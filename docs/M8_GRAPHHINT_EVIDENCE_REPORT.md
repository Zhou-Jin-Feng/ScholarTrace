# M8-G GraphHint 查漏补缺与 Evidence 门禁结果

> 日期：2026-08-31  
> 阶段结论：**PASS WITH NOTES**  
> Gate A：**NO_GO / keep_disabled**  
> Gate B/C：按协议未启动

## 范围

M8-G 没有修改 M6 B3/B4 或 M7-G 证据。ScholarGraph 在独立阶段 19 中实现 B5 top-3
seed 加最多 1 个可审计一跳 GraphHint 的离线候选池；候选不替换 B5 结果，不是 Evidence，
也未进入 ScholarTrace 在线 API。

## 结果

开发集上 B6 相对 B5 的 recall 从 0.85 提升到 0.95，coverage 从 0.70 提升到 0.90，
候选 precision 从 0.566667 降至 0.475；补回发生在两个分层。该集合包含既有公开开发材料，
只用于调试，不能作为上线证据。

算法冻结后，独立 holdout 包含 8 题、四个分层、10 个 gold ID，与开发 gold 无重叠。
正式 Gate A 的 B5/B6 recall 均为 0.90，coverage 均为 0.875，没有任何分层出现补回；B6
precision 为 0.321429，低于 B5 的 0.428571。唯一 B5 漏检 `W4408716028` 未被 B6 找到。

来源有效率、GraphPath 回链、boundary、重复确定性和 Parquet 只读性全部通过，说明失败
来自收益不足，不是端口、服务版本、索引损坏或执行不稳定。全程无网络、模型和付费调用。

## 决策

- Gate A 严格结论为 `NO_GO / keep_disabled`；
- 不实现 ScholarTrace GraphHint Consumer，不启动 DocuMind ingest/retrieve 或 Verifier；
- 不执行 Shadow Mode、盲审或付费最终质量评测；
- ScholarGraph 保持默认关闭，在线 `source_refs=[]` 不变；
- 保留开发正结果与 holdout 零增益，不能用开发集覆盖正式结论。

ScholarGraph 实现和实验资产本身经 213 项全量测试通过，完整上游证据见
`docs/phase-19-m8-graph-expansion-report.md`。若未来重开，必须出现新的真实候选漏检需求、
不同图数据或有依据的定向语料变更，不能修改本轮冻结 holdout 继续调参。
