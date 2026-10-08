# 路由基线评测

- 评测集: `evals\routing_eval.jsonl` (217 条)
- L1 关键词未命中率（= 会触发 GLM 回落的比例）: **8.8%**

## 已跑后端

| 后端 | 准确率 | P50 延迟(ms) | P95 延迟(ms) |
|---|---|---|---|
| l1 | 75.1% | 0.015 | 0.022 |
| l1_alt | 48.9% | 0.022 | 0.026 |

## 分 tag 准确率

| 后端 |  | conflict | short | vision-priority |
|---|---|---|---|---|
| l1 | 74% | 75% | 70% | 87% |
| l1_alt | 49% | 58% | 50% | 40% |

## 混淆对 Top 10

**l1**
- `chinese` → `coder`  ×5
- `knowledge` → `pm`  ×5
- `devops` → `coder`  ×4
- `security` → `coder`  ×4
- `knowledge` → `reasoner`  ×4
- `academic` → `reasoner`  ×3
- `data` → `coder`  ×3
- `pm` → `data`  ×3
- `academic` → `data`  ×2
- `reasoner` → `data`  ×2

**l1_alt**
- `knowledge` → `pm`  ×22
- `academic` → `pm`  ×18
- `chinese` → `pm`  ×13
- `coder` → `pm`  ×11
- `reasoner` → `pm`  ×11
- `vision` → `pm`  ×9
- `data` → `pm`  ×8
- `devops` → `pm`  ×5
- `security` → `pm`  ×5
- `academic` → `reasoner`  ×3
