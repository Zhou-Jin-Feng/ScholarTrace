# M9-P1 排序修复冻结复审

> 日期：2026-09-03
> 结论：**PASS WITH NOTES**
> 验证范围：P1 已冻结；P2 尚未实施；ScholarGraph `keep_disabled`

## 1. 范围

P0 的 11 条 confirmed miss 中，10 条主根因为 `G3_RANKING`、1 条为 `G1_ALIAS`。项目
P1 只在 ScholarGraph 实施 G3 最小修复：保持图候选生成不变，将最终唯一
GraphHint 改为按候选论文查询相关性和 GraphPath 特异性排序。没有混入别名、关系、图数据、
两跳、语料扩展、模型或索引重建。

上游冻结身份：

- ScholarGraph commit：`04f5327`；
- B5 commit：`37e00cfdafbd21612de9e9c60807ed0aeac28783`；
- P1 算法清单 SHA-256：
  `e673f2d7f405f1fec2741c4e36490fc47f7b4d35cb838853519ab1fb2df5ae58`；
- 198 篇六表 bundle：`c837792525d387ff3257a3e7d0504a7ec73fc28e2f09399e96a339ef2a25617b`。

## 2. 实现边界

- 新增独立 B7，不覆盖冻结 B6；
- B5 top-3 原样保留，每题最多追加 1 个一跳 GraphHint；
- B7 输出稳定原因码、查询相关性分量、路径特异性分量和完整 GraphPath；
- 正式索引只读，网络、模型和付费调用均为 0；
- ScholarTrace 未修改运行代码、配置或 P0 冻结报告。

## 3. 验证

- 锁定 `graphrag-project:3.1.2` 容器全量 219 tests；
- ScholarGraph 改动文件 Ruff、compileall、pip check：PASS；
- P0 10 条已知 G3 记录重复两次：B6 补回 1/10，B7 补回 4/10，签名一致；
- B5 seed 保留率与新增 GraphPath 有效率均为 100%；
- M8 开发集 B5/B6 recall 0.85/0.95、B6 precision 0.475 和冻结签名保持不变；
- 正式六表运行前后 SHA-256 一致；公开报告不含问题原文、task ID、source event 或 notes；
- ScholarGraph 交付清单 PASS，检查 635 个已跟踪文件。

## 4. Note 与结论

P0 已知集参与过设计，B7 的 0.40 补回率也低于 P2 预注册的至少 0.50，因此只能证明单一
treatment 已具备前瞻验证条件，不能证明可泛化增益。M9-P1 工程结论为
`PASS WITH NOTES`；ScholarGraph 继续默认关闭。

下一步必须先取得 P2 授权，再收集算法冻结后出现的全新连续 eligible 子问题。不得复用
P0 或 M8 holdout，也不得在看到 B5/B7 结果后挑题。
