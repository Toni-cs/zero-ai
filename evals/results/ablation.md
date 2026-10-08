# 关键词表消融实验

- 评测集: `evals\routing_eval.jsonl`（217 条）
- 算法固定：vision 优先 + 固定顺序首命中（与 `expert_route.route_expert` 一致）
- 唯一变量：关键词表

| 变体 | 准确率 | Δ vs A | knowledge召回 | pm召回 | coder召回 |
|---|---|---|---|---|---|
| A_const | 75.1% | +0.0pp | 61% | 65% | 100% |
| B_yaml | 69.6% | -5.5pp | 61% | 55% | 85% |
| C_union | 73.7% | -1.4pp | 61% | 50% | 96% |
| D_const_trim | 78.3% | +3.2pp | 61% | 75% | 89% |
| E_trim_plus_knowledge | 78.3% | +3.2pp | 61% | 75% | 89% |

## 各变体描述

- **A_const**: constants.EXPERT_TEAM（4c99f06 快照·当时 TUI 用）（212 词）
- **B_yaml**: zeroai/config.yaml experts 段（历史存档·当时包导出用）（181 词）
- **C_union**: 两者并集（233 词）
- **D_const_trim**: constants 去掉 coder 18 个泛词（194 词）
- **E_trim_plus_knowledge**: D + 补 knowledge 11 词（205 词）

## 最优变体的混淆对 Top 10

**D_const_trim**（78.3%）
- `chinese` → `coder`  ×5
- `knowledge` → `pm`  ×5
- `knowledge` → `reasoner`  ×4
- `coder` → `knowledge`  ×3
- `academic` → `reasoner`  ×3
- `pm` → `data`  ×3
- `academic` → `data`  ×2
- `security` → `coder`  ×2
- `reasoner` → `data`  ×2
- `reasoner` → `knowledge`  ×2
