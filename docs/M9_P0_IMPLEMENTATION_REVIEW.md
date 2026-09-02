# M9-P0 采集基础设施复审

> 日期：2026-09-02
> 结论：**PASS WITH NOTES**
> 实施状态：工具完成，真实采集 `0/30`

## 1. 复审范围

本次复审覆盖 ScholarTrace 私有观察账本、capture/review/status CLI、脱敏公开报告、JSON
Schema、ScholarGraph 冻结 B5 快照生产器、两仓边界、隐私泄漏、冻结索引身份、门禁逻辑、
测试和项目状态文档。未审查或执行 P1 treatment、前瞻 Gate A、DocuMind 回放和付费评测。

## 2. 自动检查

- ScholarTrace：`192 passed`；Ruff PASS；strict Mypy 75 个 source file PASS；
  `uv lock --check` PASS；Vite 6.4.3 生产构建 PASS；
- M9 专项：14 项通过，覆盖哈希链、复核修订、重复问题、fixture 排除、隐私、门禁、
  Schema 和黑盒 CLI；
- ScholarGraph：锁定 `graphrag-project:3.1.2` 容器内 `218 tests` 全部通过；
- 实际正式索引 smoke：198 documents、冻结六表 bundle
  `c837792525d387ff3257a3e7d0504a7ec73fc28e2f09399e96a339ef2a25617b`，B5 成功返回
  3 个候选，快照不含问题原文，运行前后只读校验通过；
- ScholarGraph 交付校验 PASS；两仓 `git diff --check` 无空白错误。

## 3. 复审发现与修复

### 中：同规模但非冻结索引可伪装成正式 B5

初版只检查 198 documents 和运行前后 hash 相同，未要求六表等于 M8 冻结值。现已在
ScholarGraph 固定六表 hash，并在 ScholarTrace Schema 固定 bundle hash；错误索引 fail
closed，新增专项回归。

### 中：损坏账本可能被误写为需求不足

初版在 50 条已审核记录后直接返回 `NO_GO_INSUFFICIENT_NEED`，未先要求哈希链和快照可复现。
现已要求两项完整性同时通过，否则保持 `COLLECT_MORE` 并公开阻断原因。

### 低：公开根因分布遗漏非图侧原因

初版 `root_cause_counts` 只累计 `G1-G4`，无法观察语料或全文缺口是否占主导。现已公开
全部已确认漏检的 `G1-G4/N1-N4` 聚合计数，同时门禁仍只计算语料内图侧机会。

### 低：任意 reason 字符串存在隐私误写风险

快照 reason 已收窄为 `matched/no_catalog_match`，并验证 status/reason 组合，禁止把问题
文本或内部错误写入公开快照。

## 4. 遗留说明

- 当前 `0/30` 只证明设施可运行，不证明产品中存在足量漏检，也不证明 ScholarGraph 有增益；
- SQLite 账本未加密，依赖本机可信账户、磁盘和 `agent/` 私有边界；CLI 仅支持单操作员串行；
- 真实 gold 和根因需要项目所有者逐条确认；达到 `GO_IMPLEMENT` 后仍必须再次审批；
- ScholarGraph 继续默认关闭，`GraphHint` 仍不是 Evidence。

以上备注均不阻断 P0 连续采集，因此本次基础设施复审结论为 `PASS WITH NOTES`，阶段停在
真实数据采集门禁，不进入 M9-P1。
