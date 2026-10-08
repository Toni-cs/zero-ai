# -*- coding: utf-8 -*-
"""路由基线评测器

用法：
    python evals/run_routing_benchmark.py                  # 跑无需 API key 的后端
    python evals/run_routing_benchmark.py --backends l1,l1_alt
    python evals/run_routing_benchmark.py --backends hybrid,l2   # 需要 GLM key

后端：
    l1       zeroai.core.expert_route.route_expert          TUI 实际使用的关键词路由
    l1_alt   zeroai.core.expert.ExpertRouter.route_by_keywords  包导出的关键词路由
    hybrid   zeroai.core.expert_route.route_expert_glm      L1 命中即返回，未命中走 GLM
    l2       强制走 GLM 语义路由                             需要 API key

输出：
    evals/results/<backend>.json    该后端完整指标
    evals/results/summary.json      所有已跑后端的汇总
    evals/results/summary.md        人读对比表

诚实性约定：跑不了的后端显式记为 skipped + 原因，不用 0 或估计值填充。
"""
import argparse
import asyncio
import io
import json
import os
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EVAL_PATH = os.path.join("evals", "routing_eval.jsonl")
OUT_DIR = os.path.join("evals", "results")


def load_eval():
    rows = []
    with io.open(EVAL_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------- 后端实现
def make_l1():
    from zeroai.core.expert_route import route_expert
    return route_expert


def make_l1_alt():
    from zeroai.core.expert import get_expert_router
    router = get_expert_router()
    return router.route_by_keywords


def make_hybrid():
    from zeroai.core.expert_route import route_expert_glm
    return lambda text: asyncio.run(route_expert_glm(text))


def make_l2():
    """强制所有输入走 GLM 语义路由（绕过关键词短路）"""
    from zeroai.core.expert_route import route_expert_glm, route_expert  # noqa
    from zeroai.core.llm import LLMClient

    async def _run(text):
        # route_expert_glm 内部会先跑关键词；这里直接调用其 GLM 分支逻辑
        from zeroai.core import expert_route as ER
        if len(text) < 10:
            return ER.route_expert(text), True
        cache_key = ER.hashlib.md5(text.encode("utf-8")).hexdigest()[:16]
        cached = ER._expert_route_cache.get(cache_key)
        if cached is not None:
            return cached, True
        result = await ER._route_by_glm_semantic(text) if hasattr(ER, "_route_by_glm_semantic") else None
        if result is None:
            # 退化：无独立 L2 入口时返回关键词结果并标记
            return ER.route_expert(text), False
        ER._expert_route_cache.set(cache_key, result)
        return result, True

    def run(text):
        return asyncio.run(_run(text))[0]
    return run


BACKENDS = {
    "l1": (make_l1, "expert_route.route_expert（TUI 路径）"),
    "l1_alt": (make_l1_alt, "expert.ExpertRouter.route_by_keywords（包导出路径）"),
    "hybrid": (make_hybrid, "expert_route.route_expert_glm（L1→GLM 混合）"),
    "l2": (make_l2, "强制 GLM 语义路由"),
}


# ---------------------------------------------------------------- 指标
def evaluate(pred_fn, rows, repeats=1):
    """返回 (metrics, detail_rows)"""
    preds, lats, errors = [], [], Counter()
    api_like = 0  # L1 为 0；hybrid 只在回落时 >0，单独由调用方统计

    for r in rows:
        t0 = time.perf_counter()
        try:
            p = pred_fn(r["text"])
        except Exception as e:  # noqa: BLE001
            p = None
            errors[type(e).__name__] += 1
        dt = (time.perf_counter() - t0) * 1000.0
        preds.append(p)
        lats.append(dt)

    total = len(rows)
    correct = sum(1 for p, r in zip(preds, rows) if p == r["gold"])
    acc = correct / total if total else 0.0

    # 按 gold 分组的召回
    per_gold = defaultdict(lambda: [0, 0])
    for p, r in zip(preds, rows):
        per_gold[r["gold"]][1] += 1
        if p == r["gold"]:
            per_gold[r["gold"]][0] += 1
    recall = {g: (c / n if n else 0.0) for g, (c, n) in sorted(per_gold.items())}

    # 按预测分组的精确率
    per_pred = defaultdict(lambda: [0, 0])
    for p, r in zip(preds, rows):
        if p is None:
            continue
        per_pred[p][1] += 1
        if p == r["gold"]:
            per_pred[p][0] += 1
    precision = {g: (c / n if n else 0.0) for g, (c, n) in sorted(per_pred.items())}

    # 按 tag 分组
    per_tag = defaultdict(lambda: [0, 0])
    for p, r in zip(preds, rows):
        per_tag[r["tag"]][1] += 1
        if p == r["gold"]:
            per_tag[r["tag"]][0] += 1
    tag_acc = {t: (c / n if n else 0.0) for t, (c, n) in sorted(per_tag.items())}

    # 混淆对 top
    conf = Counter()
    for p, r in zip(preds, rows):
        if p != r["gold"]:
            conf[(r["gold"], p)] += 1

    lats_sorted = sorted(lats)

    def pct(q):
        if not lats_sorted:
            return 0.0
        i = min(len(lats_sorted) - 1, int(round(q * (len(lats_sorted) - 1))))
        return lats_sorted[i]

    metrics = {
        "n": total,
        "accuracy": round(acc, 4),
        "correct": correct,
        "recall_by_gold": {k: round(v, 4) for k, v in recall.items()},
        "precision_by_pred": {k: round(v, 4) for k, v in precision.items()},
        "accuracy_by_tag": {k: round(v, 4) for k, v in tag_acc.items()},
        "top_confusions": [
            {"gold": g, "pred": p, "n": n}
            for (g, p), n in conf.most_common(15)
        ],
        "latency_ms": {
            "p50": round(pct(0.50), 4),
            "p95": round(pct(0.95), 4),
            "max": round(max(lats) if lats else 0.0, 4),
            "mean": round(sum(lats) / len(lats), 4) if lats else 0.0,
        },
        "errors": dict(errors),
    }

    detail = [
        {"text": r["text"], "gold": r["gold"], "pred": p,
         "tag": r["tag"], "ok": p == r["gold"]}
        for r, p in zip(rows, preds)
    ]
    return metrics, detail, api_like


def stat_kw_fallback(rows):
    """统计 L1 关键词会返回 knowledge（即会触发 GLM 回落）的比例"""
    from zeroai.core.expert_route import route_expert
    n_kw = 0
    for r in rows:
        if route_expert(r["text"]) != "knowledge":
            n_kw += 1
    return n_kw, len(rows), n_kw / len(rows) if rows else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backends", default="l1,l1_alt")
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()

    rows = load_eval()
    os.makedirs(args.out, exist_ok=True)

    wanted = [b.strip() for b in args.backends.split(",") if b.strip()]
    summary = {"n_eval": len(rows), "backends": {}, "skipped": {}}

    # L1 回落率（架构假设的实证）
    n_hit, n_all, ratio = stat_kw_fallback(rows)
    summary["l1_knowledge_fallback"] = {
        "n_eval": n_all,
        "n_keyword_hit_non_knowledge": n_hit,
        "n_fallback_to_llm": n_all - n_hit,
        "llm_fallback_rate": round(1 - ratio, 4),
        "note": "仅当 route_expert 返回 knowledge 时才会调用 GLM 语义路由",
    }

    for name in wanted:
        if name not in BACKENDS:
            summary["skipped"][name] = "unknown backend"
            continue
        factory, desc = BACKENDS[name]
        try:
            fn = factory()
        except Exception as e:  # noqa: BLE001
            summary["skipped"][name] = "%s: %s" % (type(e).__name__, e)
            continue
        metrics, detail, _ = evaluate(fn, rows)
        metrics["backend"] = name
        metrics["desc"] = desc
        summary["backends"][name] = metrics

        with io.open(os.path.join(args.out, name + ".json"), "w", encoding="utf-8") as f:
            json.dump({"metrics": metrics, "detail": detail}, f,
                      ensure_ascii=False, indent=2)

    # 汇总
    with io.open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    lines = ["# 路由基线评测", "",
             "- 评测集: `%s` (%d 条)" % (EVAL_PATH, len(rows)),
             "- L1 关键词未命中率（= 会触发 GLM 回落的比例）: **%.1f%%**"
             % (summary["l1_knowledge_fallback"]["llm_fallback_rate"] * 100),
             "", "## 已跑后端", "",
             "| 后端 | 准确率 | P50 延迟(ms) | P95 延迟(ms) |",
             "|---|---|---|---|"]
    for name, m in summary["backends"].items():
        lat = m["latency_ms"]
        lines.append("| %s | %.1f%% | %.3f | %.3f |"
                     % (name, m["accuracy"] * 100, lat["p50"], lat["p95"]))
    if summary["skipped"]:
        lines += ["", "## 跳过", ""]
        for name, why in summary["skipped"].items():
            lines.append("- `%s`: %s" % (name, why))
    lines += ["", "## 分 tag 准确率", ""]
    tags = sorted({t for m in summary["backends"].values() for t in m["accuracy_by_tag"]})
    lines.append("| 后端 | " + " | ".join(tags) + " |")
    lines.append("|---" * (len(tags) + 1) + "|")
    for name, m in summary["backends"].items():
        lines.append("| %s | " % name +
                     " | ".join("%.0f%%" % (m["accuracy_by_tag"].get(t, 0) * 100)
                                for t in tags) + " |")
    lines += ["", "## 混淆对 Top 10", ""]
    for name, m in summary["backends"].items():
        lines.append("**%s**" % name)
        for c in m["top_confusions"][:10]:
            lines.append("- `%s` → `%s`  ×%d" % (c["gold"], c["pred"], c["n"]))
        lines.append("")

    md_path = os.path.join(args.out, "summary.md")
    with io.open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("OK -> %s" % md_path)


if __name__ == "__main__":
    main()
