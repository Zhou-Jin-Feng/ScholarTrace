# M9-P2 前瞻门禁基础设施复审

> 日期：2026-09-03
> 结论：**PASS WITH NOTES**
> 验证范围：工具与预注册已冻结；真实样本 `0/30`；ScholarGraph `keep_disabled`

## 1. 范围

本次复审只覆盖 P2 的预注册、连续采集账本、先 Gold 后条件的批次盲法、跨仓 B5/B7 条件
快照、脱敏状态报告和 Gate A 计算。没有创建或伪造正式研究问题，没有使用 P0/M8 样本，
没有启动 DocuMind、模型、网络、付费调用、索引写入或 ScholarGraph 在线集成。

## 2. 冻结边界

- 预注册 SHA-256：`2267c1fda78d98c67aad051901a76cf3fbd6a7448c5ae551dc9fe8b187f84899`；
- 排除 39 条 P0 和 8 条 M8 holdout 问题哈希，规范化重复同样拒绝；
- 初始 30 条必须全部冻结 Gold 后才能揭示 B5/B7；
- 机会少于 6 时才允许扩展，31-49 条期间仍禁止揭示，到 50 条整体冻结后再运行；
- B5 `37e00cf`、B7 `04f5327`、198 篇语料和六表 bundle 均为固定身份；
- 条件揭示后禁止修订该批次已有 Gold，终局后禁止继续采样。

## 3. 验证

- ScholarTrace 全量 `195 tests`：PASS；
- Ruff 全仓、严格 Mypy 76 个 source file、`uv lock --check`：PASS；
- 前端 `npm ci`、TypeScript 与 Vite build：PASS，0 vulnerabilities；
- `docker compose config --quiet`、Schema 导出和 `git diff --check`：PASS；
- ScholarGraph 锁定容器全量 `224 tests`：PASS；
- ScholarGraph 改动文件 Ruff、compileall、pip check：PASS；
- 正式 198 篇六表 fixture smoke：B5 3 个候选，B7 保留全部 seed 并追加 1 个 GraphHint；
- fixture 的 source ref、GraphPath、4 条 boundary、重复运行确定性和 Parquet 只读：PASS；
- ScholarTrace 成功消费 ScholarGraph 条件快照，且公开状态不含问题、source event 或 notes；
- ScholarGraph 交付校验：本机 Python 3.11 `PASS`，13 claims、19 artifacts、3 cases、
  8 clean-room checks；容器运行仅因 worktree `.git` 指向宿主外部路径而无法访问 Git。

## 4. Note 与结论

工程与协议实现为 `PASS WITH NOTES`。唯一 note 不是代码缺陷，而是尚无 B7 冻结后自然发生
的真实 ScholarTrace 研究子问题，因此公开报告只能是 `COLLECTING 0/30`。fixture smoke
只证明工具可运行，不能证明候选增益，也不能开启 P3。

后续把真实研究任务自然拆分并连续写入账本即可；在首批 30 条 Gold 全部冻结前，不得运行
正式 B5/B7 快照。P2 Gate A 尚未完成，ScholarGraph 继续默认关闭。
