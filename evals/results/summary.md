# 路由基线评测

- 评测集: `evals\routing_eval.jsonl` (217 条)
- L1 关键词未命中率（= 会触发 GLM 回落的比例）: **18.4%**

## 已跑后端

| 后端 | 准确率 | P50 延迟(ms) | P95 延迟(ms) |
|---|---|---|---|
| l1 | 84.3% | 0.390 | 0.723 |

## 分 tag 准确率

| 后端 |  | conflict | short | vision-priority |
|---|---|---|---|---|
| l1 | 84% | 75% | 80% | 100% |

## 混淆对 Top 10

**l1**
- `pm` → `knowledge`  ×10
- `academic` → `reasoner`  ×3
- `reasoner` → `knowledge`  ×3
- `coder` → `knowledge`  ×2
- `coder` → `data`  ×2
- `academic` → `knowledge`  ×2
- `pm` → `data`  ×2
- `academic` → `data`  ×1
- `chinese` → `academic`  ×1
- `data` → `vision`  ×1
