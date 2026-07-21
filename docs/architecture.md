# 技术架构

```text
用户 Query
→ Query Plan / Query State
→ Dify BM25 + Dense + Reranker
→ destination_id + semantic score
→ PostgreSQL / JSONL hydrate
→ 预算、天数、交通硬过滤
→ mood / vibe / activity 加权排序
→ Claim BGE Rerank + Evidence Gate
→ MMR 多样性选择
→ 受限理由校验 + 代码事实卡
```

## 为什么不是开放式 Agent

旅行推荐包含预算、交通、证据和安全边界。开放式 ReAct 会扩大调用次数、延迟和不可预测性。本项目采用固定 Workflow，让每个节点都有输入输出合同、失败状态和可测试指标。

## Dify 与后端的边界

- Dify：意图模型调用、混合召回、节点编排。
- 推荐后端：事实过滤、业务排序、证据取回、MMR、输出忠实度。
- PostgreSQL/JSONL：Entity、Fact、Travel、Claim、Profile。
- 前端：只渲染结构化结果，不持有应用 Key。

## 运行模式

- `demo`：合成 JSONL、规则 Query Plan、真实确定性排序，无外部依赖。
- `full`：Dify + PostgreSQL + 高德 + 可选 Xinference/Tavily。
