# -*- coding: utf-8 -*-
"""Laya 决策模型 vs 关键词路由：在同一批金标样本上对比

回答三个问题：
  1. Laya 零样本的原始准确率是多少？能不能超过关键词路由的 79.7%？
  2. 它的置信度可信吗（错的时候是不是也高置信）？
  3. 用置信度门控把两者组合，能不能同时高于两者？

注意：本脚本只做测量，不改 zero-ai 任何源码。
     Laya 权重需先下载（HF_ENDPOINT=https://hf-mirror.com）。

用法：
    set HF_ENDPOINT=https://hf-mirror.com
    python evals/eval_laya.py [--variant desc|kw] [--model multilingual]

输出：
    evals/results/laya.json
    evals/results/laya.md
"""
import argparse
import io
import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zeroai.core.constants import EXPERT_TEAM  # noqa: E402
from zeroai.core import expert_route  # noqa: E402

EVAL_PATH = os.path.join("evals", "routing_eval.jsonl")
OUT_DIR = os.path.join("evals", "results")

EXPERT_ORDER = list(EXPERT_TEAM.keys())


def load_eval():
    rows = []
    with io.open(EVAL_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build_criteria(variant):
    """构造 choice 的 criteria。

    variant=desc：用仓库自己的 desc 字段（去掉括号里的模型元信息）
    variant=kw  ：用仓库自己的 keywords（前 8 个）
    两种都取自 EXPERT_TEAM，不手工编写，避免人为偏向某个选项。
    """
    crit = {}
    for k, cfg in EXPERT_TEAM.items():
        if variant == "kw":
            kws = cfg.get("keywords", [])[:8]
            text = "、".join(kws) if kws else (cfg.get("desc") or k)
        else:
            text = (cfg.get("desc") or k).split("（")[0]
        crit[k] = text
    return crit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="desc", choices=["desc", "kw"])
    ap.add_argument("--model", default="multilingual")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    rows = load_eval()
    criteria = build_criteria(args.variant)
    questions = {
        "expert": {
            "type": "choice",
            "instructions": "Which expert should handle this request? "
                            "Reply with exactly one key from the criteria.",
            "criteria": criteria,
        }
    }

    from laya import Router

    router = Router()
    preds, confs, lats = [], [], []

    t_all = time.time()
    use_batch = hasattr(router, "predict_batch")
    if use_batch:
        reqs = [{"state": {"body": r["text"]}, "questions": questions,
                 "model": args.model} for r in rows]
        t0 = time.time()
        try:
            outs = router.predict_batch(reqs)
        except Exception as e:  # noqa: BLE001
            print("batch failed (%s), fallback to loop" % e)
            use_batch = False
            outs = None
        if use_batch:
            batch_time = time.time() - t0
            for o in outs:
                a = o["answers"]["expert"]
                preds.append(a.get("choice"))
                confs.append(float(a.get("confidence") or 0.0))
                lats.append(batch_time / len(outs) * 1000.0)

    if not use_batch:
        for r in rows:
            t0 = time.time()
            o = router.predict({"body": r["text"]}, questions, model=args.model)
            lats.append((time.time() - t0) * 1000.0)
            a = o["answers"]["expert"]
            preds.append(a.get("choice"))
            confs.append(float(a.get("confidence") or 0.0))

    total = time.time() - t_all

    # ---- 原始准确率 ----
    correct = sum(1 for p, r in zip(preds, rows) if p == r["gold"])
    acc = correct / len(rows)

    per_gold = defaultdict(lambda: [0, 0])
    per_pred = defaultdict(lambda: [0, 0])
    per_tag = defaultdict(lambda: [0, 0])
    conf = Counter()
    for p, r in zip(preds, rows):
        per_gold[r["gold"]][1] += 1
        per_pred[p][1] += 1
        per_tag[r["tag"]][1] += 1
        if p == r["gold"]:
            per_gold[r["gold"]][0] += 1
            per_pred[p][0] += 1
            per_tag[r["tag"]][0] += 1
        else:
            conf[(r["gold"], p)] += 1

    # ---- 置信度可信度 ----
    right_c = [c for c, p, r in zip(confs, preds, rows) if p == r["gold"]]
    wrong_c = [c for c, p, r in zip(confs, preds, rows) if p != r["gold"]]

    # ---- 关键词基线（同一批样本，用于组合门控）----
    kw_preds = [expert_route.route_expert(r["text"]) for r in rows]
    kw_acc = sum(1 for p, r in zip(kw_preds, rows) if p == r["gold"]) / len(rows)

    agree = sum(1 for a, b in zip(preds, kw_preds) if a == b)

    # ---- 门控扫描：Laya 置信度 >= tau 才用 Laya，否则回落关键词 ----
    sweep = []
    for tau in [0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1.01]:
        use_laya = [c >= tau for c in confs]
        n_laya = sum(use_laya)
        final = [p if u else kw for p, u, kw in zip(preds, use_laya, kw_preds)]
        ok = sum(1 for f, r in zip(final, rows) if f == r["gold"])
        sweep.append({"tau": tau, "n_laya": n_laya,
                      "n_fallback": len(rows) - n_laya,
                      "accuracy": round(ok / len(rows), 4)})

    def pct(vals, q):
        if not vals:
            return 0.0
        s = sorted(vals)
        i = min(len(s) - 1, int(round(q * (len(s) - 1))))
        return s[i]

    metrics = {
        "variant": args.variant,
        "model": args.model,
        "n": len(rows),
        "laya_accuracy": round(acc, 4),
        "keyword_accuracy": round(kw_acc, 4),
        "delta_vs_keyword": round(acc - kw_acc, 4),
        "agreement_with_keyword": round(agree / len(rows), 4),
        "recall_by_gold": {k: round(v[0] / v[1], 4)
                           for k, v in sorted(per_gold.items())},
        "precision_by_pred": {k: round(v[0] / v[1], 4)
                              for k, v in sorted(per_pred.items())},
        "accuracy_by_tag": {k: round(v[0] / v[1], 4)
                            for k, v in sorted(per_tag.items())},
        "top_confusions": [{"gold": g, "pred": p, "n": n}
                           for (g, p), n in conf.most_common(15)],
        "confidence": {
            "mean_right": round(statistics.mean(right_c), 4) if right_c else None,
            "mean_wrong": round(statistics.mean(wrong_c), 4) if wrong_c else None,
            "n_wrong_high_conf": sum(1 for c in wrong_c if c >= 0.9),
            "n_wrong": len(wrong_c),
            "p50": round(pct(confs, 0.5), 4),
        },
        "latency_ms": {
            "batched": use_batch,
            "p50": round(pct(lats, 0.5), 4),
            "p95": round(pct(lats, 0.95), 4),
            "mean": round(statistics.mean(lats), 4) if lats else 0.0,
            "total_s": round(total, 2),
        },
        "gate_sweep": sweep,
        "best_gate": max(sweep, key=lambda s: s["accuracy"]),
    }

    detail = [{"text": r["text"], "gold": r["gold"], "laya": p,
               "keyword": kw, "conf": round(c, 4),
               "laya_ok": p == r["gold"], "kw_ok": kw == r["gold"]}
              for r, p, kw, c in zip(rows, preds, kw_preds, confs)]

    with io.open(os.path.join(OUT_DIR, "laya.json"), "w", encoding="utf-8") as f:
        json.dump({"metrics": metrics, "detail": detail}, f,
                  ensure_ascii=False, indent=2)

    # ---- 报告 ----
    L = ["# Laya 决策模型评测", "",
         "- 评测集: `%s`（%d 条）" % (EVAL_PATH, len(rows)),
         "- 变体: `%s` / checkpoint: `%s`" % (args.variant, args.model),
         "- criteria 来源: `EXPERT_TEAM`（未人工编写，避免选项偏向）", "",
         "## 结果", "",
         "| 路由方案 | 准确率 |",
         "|---|---|",
         "| 关键词 `route_expert`（现状） | **%.1f%%** |" % (kw_acc * 100),
         "| **Laya 零样本** | **%.1f%%** |" % (acc * 100),
         "| 差值 | **%+.1fpp** |" % ((acc - kw_acc) * 100),
         "| 两者一致率 | %.1f%% |" % (agree / len(rows) * 100), "",
         "## 置信度可信度", "",
         "- 判对时平均置信度: **%s**" % metrics["confidence"]["mean_right"],
         "- 判错时平均置信度: **%s**" % metrics["confidence"]["mean_wrong"],
         "- 判错且置信度 ≥0.9 的条数: **%d / %d**" % (
             metrics["confidence"]["n_wrong_high_conf"],
             metrics["confidence"]["n_wrong"]),
         "",
         "## 置信度门控扫描（Laya 置信度 ≥ τ 用 Laya，否则回落关键词）", "",
         "| τ | 用 Laya 条数 | 回落条数 | 准确率 |",
         "|---|---|---|---|"]
    for s in sweep:
        L.append("| %s | %d | %d | %.1f%% |" % (
            s["tau"], s["n_laya"], s["n_fallback"], s["accuracy"] * 100))
    bg = metrics["best_gate"]
    L += ["", "**最优门控**: τ=%s → **%.1f%%**（vs 关键词 %.1f%%，%+.1fpp）" % (
        bg["tau"], bg["accuracy"] * 100, kw_acc * 100,
        (bg["accuracy"] - kw_acc) * 100), "",
          "## 分 tag 准确率", "",
          "| tag | Laya | 关键词 |", "|---|---|---|"]
    kw_tag = defaultdict(lambda: [0, 0])
    for kw, r in zip(kw_preds, rows):
        kw_tag[r["tag"]][1] += 1
        if kw == r["gold"]:
            kw_tag[r["tag"]][0] += 1
    for t in sorted(per_tag):
        L.append("| %s | %.0f%% | %.0f%% |" % (
            t or "(常规)",
            per_tag[t][0] / per_tag[t][1] * 100,
            kw_tag[t][0] / kw_tag[t][1] * 100))

    L += ["", "## 混淆对 Top 10（Laya）", ""]
    for c in metrics["top_confusions"][:10]:
        L.append("- `%s` → `%s`  ×%d" % (c["gold"], c["pred"], c["n"]))

    L += ["", "## Laya 判错而关键词判对（可回收的样本）", ""]
    rec = [d for d in detail if not d["laya_ok"] and d["kw_ok"]]
    for d in rec[:25]:
        L.append("- [金标 `%s` / Laya `%s`(%.2f)] %s" % (
            d["gold"], d["laya"], d["conf"], d["text"]))
    L.append("")
    L += ["## Laya 判对而关键词判错（Laya 独有价值）", ""]
    add = [d for d in detail if d["laya_ok"] and not d["kw_ok"]]
    for d in add[:25]:
        L.append("- [金标 `%s` / 关键词 `%s`] %s" % (
            d["gold"], d["keyword"], d["text"]))
    L.append("")
    L.append("合计: 可回收 %d 条，Laya 独有增益 %d 条" % (len(rec), len(add)))

    with io.open(os.path.join(OUT_DIR, "laya.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    print("OK -> %s" % os.path.join(OUT_DIR, "laya.md"))


if __name__ == "__main__":
    main()
