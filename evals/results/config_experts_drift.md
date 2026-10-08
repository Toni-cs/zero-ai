# 已删除的 config.yaml `experts` 段（历史存档）

- 来源: `.backup_20261008_2155\config.yaml.bak`
- 删除于 2026-10-08，原因见 commit（专家配置统一到 constants.EXPERT_TEAM）
- 本文件存在是为了让 evals/ablate_keywords.py 的 B 变体可复跑，并保留漂移证据

## 删除当时与 constants.EXPERT_TEAM 的漂移

| expert | config 词数 | constants 词数 | keywords 是否相同 | system_prompt 是否相同 | label 是否相同 | desc 是否相同 |
|---|---|---|---|---|---|---|
| pm | 10 | 17 | **否** | 是 | 是 | 是 |
| coder | 19 | 38 | **否** | 是 | 是 | 是 |
| reasoner | 14 | 23 | **否** | 是 | 是 | 是 |
| knowledge | 11 | 16 | **否** | **否** | **否** | **否** |
| chinese | 12 | 28 | **否** | **否** | 是 | **否** |
| vision | 10 | 19 | **否** | **否** | **否** | **否** |
| academic | 17 | 36 | **否** | **否** | 是 | **否** |
| devops | 30 | 36 | **否** | 是 | 是 | 是 |
| security | 31 | 37 | **否** | 是 | 是 | 是 |
| data | 27 | 38 | **否** | 是 | 是 | 是 |

**system_prompt 漂移 4 / 10 个专家。**

其中 academic 的 config 版只有普通的一句学术专家介绍，
而 constants 版是一整套学术规范（禁止编造文献、引用前必须
citation_check、公式必须 LaTeX 等）。走 config.yaml 的入口
（expert.py 全链路、zeroai_tui/integration）拿不到这些规则 ——
即同一程序在不同入口上学术诚信约束不一致。

### 两者 academic system_prompt 的字数对比

- config.yaml 版: **75 字**
- constants 版: **5345 字**
