# 数据设计与评测

## 数据关系

- Entity：地点身份、类型、别名和父子关系。
- Evidence：来源凭证与采集元数据。
- Claim：某个来源对某个实体的原子判断。
- Fact / Travel：预算、天数、交通和运营状态。
- Profile：Claim 聚合后的 mood、vibe、activity 和证据质量。
- Dify Document：Profile 面向检索的自然语言投影。

这种分层避免了把 UGC 文本直接切块后同时承担语义召回、事实过滤和理由生成。

## 评测分层

1. Query Plan：scope、task、硬槽位、否定、多轮修改。
2. Retrieval：Recall@K、Hit Rate、MRR、active inventory coverage。
3. Ranking：硬约束误杀、感觉匹配、证据准入和多样性。
4. Output：事实忠实度、引用完整性、降级状态。
5. Performance：后端与完整 Dify 链路分别统计 P95。

公开数据只验证代码合同。历史指标来自单独的私有评测快照，避免将真实 UGC 与来源信息发布到 GitHub。
