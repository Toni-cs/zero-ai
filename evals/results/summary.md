# 路由基线评测

- 评测集: `evals\routing_eval.jsonl` (217 条)
- L1 关键词未命中率（= 会触发 GLM 回落的比例）: **9.2%**

## 已跑后端

| 后端 | 准确率 | P50 延迟(ms) | P95 延迟(ms) |
|---|---|---|---|
| l1 | 79.7% | 0.014 | 0.021 |

## 分 tag 准确率

| 后端 |  | conflict | short | vision-priority |
|---|---|---|---|---|
| l1 | 80% | 75% | 70% | 87% |

## 混淆对 Top 10

**l1**
- `chinese` → `coder`  ×5
- `knowledge` → `pm`  ×5
- `knowledge` → `reasoner`  ×4
- `academic` → `reasoner`  ×3
- `pm` → `data`  ×3
- `academic` → `data`  ×2
- `security` → `coder`  ×2
- `reasoner` → `data`  ×2
- `reasoner` → `knowledge`  ×2
- `academic` → `knowledge`  ×1
