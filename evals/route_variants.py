# -*- coding: utf-8 -*-
"""路由算法候选变体 + 三套指标，用于在 **dev 池**（dev+holdout）上选型。

## 为什么需要三套指标

生产链路 route_expert_glm 的行为是分段的：

    len(text) < 10          -> L1 结果即最终结果（无 L2 兜底）
    L1 == "knowledge"       -> 交给 L2 GLM 语义判断
    L1 == 其他非知识专家     -> 短路返回，L2 永远没有机会

因此把「L1 返回 knowledge」和「L1 短路到错误专家」都算成同样的错误
是不对的：前者在长输入下会被 L2 纠正，后者才是用户实际收到的错答案。

指标：
  l1_acc        L1 单独的准确率（与历史可比）
  defer_rate    L1 交给 L2 的比例（= 成本与延迟）
  unsafe_rate   **用户实际收到错误答案**的比例 —— 越低越好
                 unsafe = (pred != gold) 且不满足「deferred 且长输入」
  safe_err      长输入下被短路到错误专家的数量（真正要消灭的）

## 变体说明

V0  现状：首命中即胜 + 固定优先级
V1  竞争式评分：(命中词数, 命中词总长, 原优先级) 取最大
V2  V1 + knowledge 获得关键词（此前零关键词，结构上不可能主动胜出）
V3  V2 + 削弱 pm 的超泛化词（什么/怎么/如何/帮助/介绍/解释）
V4  V3 + 关键词补覆盖（依据 dev+holdout 错判补的缺口词）

⚠ 所有选型只在 dev 池（routing_eval + routing_holdout，共 495 条）上做。
   routing_test.jsonl 在全部完成前禁止执行。
"""
import io
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zeroai.core.constants import EXPERT_TEAM  # noqa: E402

# ── 改动前的关键词快照 ──────────────────────────────────────────────
# 关键：本模块的变体对比必须以「改动前」为基线。若直接读活的
# constants，则一旦按 V6 改了 constants，V0 读到的就是新词表，
# 「改动前」再也无法复现，整张对比表会失真。
# 快照由 evals/snapshot_keywords_before.py 生成。
_BEFORE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "results", "keywords_before.json")
if os.path.exists(_BEFORE_PATH):
    BEFORE_KEYWORDS = json.load(io.open(_BEFORE_PATH, encoding="utf-8"))
else:
    BEFORE_KEYWORDS = {k: list(v.get("keywords") or [])
                       for k, v in EXPERT_TEAM.items()}

# 原固定优先级（用作评分并列时的裁决顺序）
PRIORITY = ("coder", "security", "devops", "data", "reasoner",
            "academic", "chinese", "pm")
PRIORITY_IDX = {k: i for i, k in enumerate(PRIORITY)}
PRIORITY_IDX.setdefault("vision", -1)
PRIORITY_IDX.setdefault("knowledge", len(PRIORITY) + 1)

# config.yaml 里已有但 constants 中缺失的 knowledge 关键词
KNOWLEDGE_KW_CONFIG = ["知识", "百科", "事实", "历史", "地理", "科学",
                       "常识", "是什么", "谁是", "什么时候", "哪里"]

# pm 中信号极弱的超泛化词：几乎命中任何问句，对区分专家几乎无贡献
PM_GENERIC = ["什么", "怎么", "如何", "帮助", "介绍", "解释"]


def _hit(kw, text, normalize=False):
    """与 zeroai/core/expert_route.route_expert 一致的匹配规则。

    normalize=False 时与生产代码逐字符等价 —— V0 必须保持这个行为，
    否则「现状」这个基线就不成立了。

    normalize=True 时额外做一次去空格匹配。理由是实测而非猜测：
    「SQL 注入的参数化查询怎么写」里 "SQL" 与 "注入" 之间有空格，
    security 的关键词 `sql注入` 因此永远匹配不上，只剩 coder 的 `sql`
    命中，安全问题被判给编程专家。中文正文里给英文词加空格是常见
    排版习惯，这不是个别样本的问题。
    """
    k = kw.lower()
    if k.isascii() and k.isalpha() and len(k) > 4:
        return re.search(r"\b" + re.escape(k) + r"\b", text) is not None
    if k in text:
        return True
    if normalize and any("一" <= ch <= "鿿" for ch in k):
        return k in text.replace(" ", "").replace("　", "")
    return False


def _base_keywords(expert, variant):
    """V0-V6 一律以**改动前快照**为基线；V6live 才读活的 constants。"""
    if variant == "V6live":
        return list(EXPERT_TEAM.get(expert, {}).get("keywords") or [])
    return list(BEFORE_KEYWORDS.get(expert, []))


def _keywords(expert, variant):
    kws = _base_keywords(expert, variant)
    if expert == "knowledge" and variant in ("V2", "V3", "V4", "V6"):
        kws = list(KNOWLEDGE_KW_CONFIG)
    if expert == "pm" and variant in ("V3", "V4", "V6"):
        kws = [k for k in kws if k not in PM_GENERIC]
    if variant in ("V4", "V6"):
        kws = kws + EXTRA_KW.get(expert, [])
    if expert == "chinese" and variant == "V6":
        # 中文写作专家不该独占「论文」「摘要」——那是学术专家的职责词。
        # dev 集里这个问题被固定优先级掩盖了（academic 排在 chinese
        # 前面，先命中就返回）；一旦改成按评分竞争，chinese 的
        # 「写」+「论文」+「摘要」三项就会压过 academic 的两项，
        # 反而造成回归。这是改动评分方式后**新暴露**出来的数据问题。
        kws = [k for k in kws if k not in CHINESE_REMOVE] + CHINESE_ADD
    # 去重：data 原词表里「数据分析」出现 3 次。首命中算法下重复无影响，
    # 但按命中词数计分会凭空多算，必须先去重再计分。
    seen, out = set(), []
    for k in kws:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


# chinese 关键词修正（V6）
CHINESE_REMOVE = ["论文", "摘要"]          # 归 academic
CHINESE_ADD = ["公文", "通知", "自我介绍"]   # 补「写一个通知的公文」这类


# V4：依据 dev + holdout 错判补的覆盖缺口词（**不是**在 test 上找的）
EXTRA_KW = {
    "chinese": ["改", "改写", "起草", "病句", "通顺", "措辞", "串词",
                "口号", "标语", "标题", "扩写", "口语", "错别字", "语病"],
    "academic": ["摘要", "引言", "答辩", "审稿", "效度", "信度", "局限性",
                 "创新点", "预印本"],
    "data": ["画", "折线图", "柱状图", "饼图", "散点图", "热力图", "图表",
             "方差", "中位数", "偏度", "标准化", "去重", "聚合"],
    "pm": ["需求", "任务", "拆", "拆解", "排期", "优先级", "里程碑",
           "复盘", "阻塞", "范围", "验收", "排期", "分工", "卡住"],
    "knowledge": ["是什么", "是什么", "区别", "多久", "多少", "哪些"],
    "reasoner": ["极限", "求和", "概率", "导数", "积分", "方程组", "级数",
                 "辅助线", "不等式", "曲率"],
    "coder": ["异常", "空指针", "死锁", "闭包", "类型", "编译", "线程"],
    "security": ["越权", "注入", "鉴权", "脱敏", "凭证", "扫描"],
    "devops": ["挂载", "探针", "时区", "灰度", "句柄", "内核参数"],
    "vision": ["照片", "海报", "图标", "颜色"],
}


def route_variant(text, variant):
    """按变体规则返回专家 key。"""
    base = variant
    orig = variant
    kn_pos = None
    normalize = False
    if variant.startswith("V5"):
        base = "V4"
        # V5x：knowledge 参与并列裁决时的位置（越小越优先）。
        # V0/V4 中 knowledge 恒排最后，导致「为什么…」这类事实问句
        # 必然输给 reasoner 的「为什么」。
        kn_pos = {"V5a": 3, "V5b": 0, "V5c": 6}[variant]
    elif variant in ("V6", "V6live"):
        # V6    = 旧词表快照 + 各项增量（实验用，可复现原对比表）
        # V6live= 活的 constants（已按 V6 落地）+ 同一套算法
        #         —— 二者数值必须一致，用于验证落地实现没有走样
        base = variant
        kn_pos = 3         # V5a 与 V5b 结果完全相同，说明只要排在
                           # reasoner 之前即可，无需更激进
        normalize = True   # 开启去空格匹配（修 sql注入 类漏配）
    variant = base
    prio = dict(PRIORITY_IDX)
    if kn_pos is not None:
        prio["knowledge"] = kn_pos

    t = text.lower()

    # vision 保持硬优先（截图被判给 coder 是明显错误）
    if variant == "V0":
        for kw in _base_keywords("vision", variant):
            if _hit(kw, t):
                return "vision"
        for e in PRIORITY:
            for kw in _base_keywords(e, variant):
                if _hit(kw, t):
                    return e
        return "knowledge"

    # V1-V5：vision 也参与评分，但带一个固定加成使其在"明显看图"时胜出
    best = None  # (score_tuple, expert)
    want_why = orig.startswith("V5") or orig == "V6"
    for expert in list(PRIORITY) + ["vision", "knowledge"]:
        kws = _keywords(expert, variant)
        if want_why and expert == "knowledge":
            # V5 的核心假设：事实性「为什么」应由 knowledge 承接
            # （长输入下会交给 L2 GLM 做真正的语义判断），而不是被
            # reasoner 短路。是否成立由分集合指标检验。
            kws = kws + ["为什么"]
        matched = [k for k in kws if _hit(k, t, normalize)]
        if not matched:
            continue
        cnt = len(matched)
        total = sum(len(k) for k in matched)
        bonus = 0
        if expert == "vision":
            bonus = 100  # 看图诉求优先，避免被 coder 的"看/打开"抢走
        score = (bonus + cnt, total, -prio.get(expert, 99))
        if best is None or score > best[0]:
            best = (score, expert)
    if best is None:
        return "knowledge"
    return best[1]


def metrics(rows, variant, name=""):
    n = len(rows)
    l1_ok = 0
    unsafe = 0
    defer = 0
    safe_err = 0
    short_err = 0
    errs = []
    for r in rows:
        text = r["text"]
        pred = route_variant(text, variant)
        gold = r["gold"]
        ok = pred == gold
        l1_ok += ok
        is_defer = (pred == "knowledge")
        if is_defer:
            defer += 1
        # 生产语义：长输入下 def 到 knowledge 会被 L2 纠正
        if is_defer and len(text) >= 10:
            pass                      # 安全：交给 L2
        else:
            if not ok:
                unsafe += 1
                if is_defer:
                    short_err += 1     # 短句 def 到 knowledge 但无人纠正
                else:
                    safe_err += 1      # 短路到错误专家
                errs.append((gold, pred, text))
    return {
        "name": name or variant,
        "n": n,
        "l1_acc": round(l1_ok / n, 4),
        "defer_rate": round(defer / n, 4),
        "unsafe_rate": round(unsafe / n, 4),
        "short_defer_err": short_err,
        "wrong_shortcircuit": safe_err,
        "errors": errs,
    }


def load(paths):
    rows = []
    for p in paths:
        with io.open(p, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
    return rows
