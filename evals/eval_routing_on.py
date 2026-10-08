# -*- coding: utf-8 -*-
"""在指定 jsonl 上测路由准确率，输出准确率、分 tag、混淆对。

用法:
    python evals/eval_routing_on.py evals/routing_holdout.jsonl
    python evals/eval_routing_on.py evals/routing_eval.jsonl --errors out.md

留出集 evals/routing_holdout.jsonl 的**哈希会被记录**，
用于证明调优期间该文件未被改动（见 evals/results/holdout_lock.json）。
"""
import argparse
import hashlib
import io
import json
import os
from collections import Counter, defaultdict

from zeroai.core import expert_route


def eval_file(path, errors_out=None):
    rows = [json.loads(l) for l in io.open(path, encoding="utf-8") if l.strip()]
    errors = []
    ok = 0
    for i, r in enumerate(rows):
        pred = expert_route.route_expert(r["text"])
        if pred == r["gold"]:
            ok += 1
        else:
            errors.append({"idx": i, "text": r["text"], "gold": r["gold"],
                           "pred": pred, "tag": r.get("tag", "")})
    acc = ok / len(rows)

    by_tag = defaultdict(lambda: [0, 0])
    for r in rows:
        by_tag[r.get("tag") or "(常规)"][1] += 1
    for e in errors:
        by_tag[e["tag"] or "(常规)"][0] += 1
    tag_acc = {}
    for t, (nerr, n) in by_tag.items():
        tag_acc[t] = round((n - nerr) / n, 4)

    pairs = Counter((e["gold"], e["pred"]) for e in errors)

    if errors_out:
        L = ["# 错判明细 — %s" % path, "",
             "- n=%d  准确率=**%.4f**  错判=%d" % (len(rows), acc, len(errors)),
             "", "## 混淆对", "", "| gold | pred | n |", "|---|---|---|"]
        for (g, p), n in pairs.most_common():
            L.append("| `%s` | `%s` | %d |" % (g, p, n))
        L += ["", "## 逐条", ""]
        cur = None
        for e in sorted(errors, key=lambda x: (x["gold"], x["pred"])):
            k = (e["gold"], e["pred"])
            if k != cur:
                cur = k
                L += ["", "### `%s`→`%s` ×%d" % (k[0], k[1], pairs[k]), ""]
            L.append("- [%s] %s" % (e["tag"] or "-", e["text"]))
        io.open(errors_out, "w", encoding="utf-8").write("\n".join(L) + "\n")

    return {"path": path, "n": len(rows), "accuracy": round(acc, 4),
            "n_error": len(errors), "tag_accuracy": tag_acc,
            "pairs": {"%s->%s" % k: v for k, v in pairs.most_common()},
            "errors": errors}


def sha256(path):
    return hashlib.sha256(io.open(path, "rb").read()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--errors", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    res = eval_file(a.path, a.errors)
    print("path   =", res["path"])
    print("n      =", res["n"])
    print("sha256 =", sha256(a.path))
    print("acc    = %.4f  (err %d)" % (res["accuracy"], res["n_error"]))
    print("tags   =", json.dumps(res["tag_accuracy"], ensure_ascii=False))
    for k, v in list(res["pairs"].items())[:12]:
        print("   %-26s %d" % (k, v))
    if a.out:
        io.open(a.out, "w", encoding="utf-8").write(
            json.dumps(res, ensure_ascii=False, indent=2))
        print("->", a.out)


if __name__ == "__main__":
    main()
