#!/usr/bin/env python3
"""compare.py [a b]: two versions on the same 60 users and 292 requests, from their ledgers (default: v1 v3).
  python harness/compare.py v1 v3"""
import json, statistics, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(v):
    L = [json.loads(l) for l in (ROOT / "ledger" / f"{v}.jsonl").read_text(encoding="utf-8").splitlines() if l.startswith("{")]
    return [r for r in L if r["version"] == v and r["user"].startswith("u")]


def stats(L):
    calls = [r for r in L if r["usage"]]
    per = defaultdict(float)
    for r in L:
        per[r["user"]] += r["cost_usd"]
    costs = sorted(per.values(), reverse=True)
    s = {"total": sum(r["cost_usd"] for r in L), "requests": len(L), "model calls": len(calls),
         "user median": statistics.median(costs), "user mean": statistics.mean(costs), "user max": costs[0],
         "top 10% share": sum(costs[:len(costs) // 10]) / sum(costs),
         "cut off": sum(r["stop_reason"] == "max_tokens" for r in calls) / len(calls),
         "thinking share": sum(r["usage"].get("thinking_tokens", 0) for r in calls) / sum(r["usage"]["output_tokens"] for r in calls),
         "over 100k": sum(r["long_prompt"] for r in calls),
         "response cache": sum(1 for r in L if not r["usage"]) / len(L),
         "prompt cache": sum(r["usage"]["cache_read_input_tokens"] for r in calls) / sum(
             r["usage"]["input_tokens"] + r["usage"]["cache_read_input_tokens"] + r["usage"]["cache_write_5m"] + r["usage"]["cache_write_1h"] for r in calls)}
    for f in ("explain", "summarize", "chat", "fix"):
        fr = [r for r in L if r["feature"] == f]
        fc = [r for r in fr if r["usage"]]
        s[f"{f} cost"] = sum(r["cost_usd"] for r in fr)
        s[f"{f} cut off"] = sum(r["stop_reason"] == "max_tokens" for r in fc) / max(len(fc), 1)
        s[f"{f} p50 latency"] = statistics.median(r["seconds"] for r in fc) if fc else 0
    return s


va, vb = (sys.argv[1:3] if len(sys.argv) > 2 else ["v1", "v3"])
a, b = stats(load(va)), stats(load(vb))
money = lambda x: f"${x:.4f}"
pct = lambda x: f"{x:.1%}"
fmt = {"total": money, "user median": money, "user mean": money, "user max": money, "top 10% share": pct,
       "cut off": pct, "thinking share": pct, "response cache": pct, "prompt cache": pct}
print(f"{'':22} {va:>10} {vb:>10}   change")
for k in a:
    f = fmt.get(k) or (money if k.endswith("cost") else pct if k.endswith("cut off") else
                       (lambda x: f"{x:.1f}s") if k.endswith("latency") else (lambda x: f"{x:,}"))
    ch = ""
    if isinstance(a[k], float) and a[k] and (k.endswith("cost") or k in ("total", "user median", "user mean", "user max")):
        ch = f"{b[k] / a[k] - 1:+.0%}"
    print(f"{k:22} {f(a[k]):>10} {f(b[k]):>10}   {ch}")
