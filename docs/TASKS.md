# 核验消融交付索引

当前功能包含冻结输入双条件运行、确定性引用校验、独立语义核验、请求级检查点、共享发送前预算、失败累计和脱敏导出。

| 范围 | 证据 |
|---|---|
| 版本与依赖基线 | SA_01_BASELINE.md |
| 冻结题集与协议 | SA_02_VERIFICATION_ABLATION_PROTOCOL.md |
| 实验入口与合同 | SA_03_VERIFICATION_ABLATION.md |
| 试运行与故障历史 | SA_04_PILOT_RESULT.md、SA_04_REPAIR_STATUS.md |
| 正式结果 | evaluation/reports/sa_05_formal_v_on_v_off.json |
| 内容评价与可靠性修复 | VERIFICATION_ABLATION_RESULTS.md |
| 操作手册 | VERIFICATION_ABLATION_RUNBOOK.md |

实时验证状态以 evaluation/reports/verification_ablation_validation.json 的源码指纹和检查结果为准；过期记录不是当前验证结论。历史文件保留原运行日期及适用范围。

阶段二已完成简单基线、新题冻结、选择性核验实现、三方案对照、跳过 Claim 审计和工程验证复评。选择性候选未通过无支持论断门禁，正式路径保留完整核验；专项成果与复现入口已形成。

## 阶段二工程验证

详细验证清单见 [SP_VALIDATION_CHECKLIST.md](SP_VALIDATION_CHECKLIST.md)；SP-04 预检见 [SP_04_COMPARISON_PREFLIGHT.md](SP_04_COMPARISON_PREFLIGHT.md)；SP-01 记录见 [SP_01_SIMPLE_BASELINE.md](SP_01_SIMPLE_BASELINE.md)；SP-02 记录见 [SP_02_NEW_QUESTIONS.md](SP_02_NEW_QUESTIONS.md)；SP-03 记录见 [SP_03_SELECTIVE_VERIFICATION.md](SP_03_SELECTIVE_VERIFICATION.md)；SP-04 结果见 [SP_04_THREE_SCHEME_COMPARISON.md](SP_04_THREE_SCHEME_COMPARISON.md)；SP-05 成果见 [SP_05_SPECIAL_OUTCOME.md](SP_05_SPECIAL_OUTCOME.md)；工程验证清单见 [SP_VALIDATION_CHECKLIST.md](SP_VALIDATION_CHECKLIST.md)。阶段二 SP-01～SP-05 已完成。

| 范围 | 当前状态 |
|---|---|
| 阶段闸门与版本基线 | 阶段二工程验证完成（含限制）；最终采用由项目所有者确认 |
| SP-01 简单基线 | 完成（含限制） |
| SP-02 新问题与专项假设 | 完成（含限制） |
| SP-03 有界改进 | 完成（含限制） |
| SP-04 三方案对照与未核验内容审计 | 完成（含限制） |
| SP-05 专项成果与复评 | 完成（含限制） |
