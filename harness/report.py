#!/usr/bin/env python3
"""report.py <version>: what the ledger says about cost, the questions a dashboard can't answer well.
The ledger is the app's own JSON line per request (kubectl logs), saved to ledger/<version>.jsonl by save_ledger.sh.
  - cost per user: the distribution, not just the average, and who the expensive users are
  - cost by feature, per call
  - chat: what each turn costs as the conversation grows
  - requests over Haiku's 100k-token line (priced 5x), cut-off answers (max_tokens), thinking tokens
  python harness/report.py v1"""
import json, statistics, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from pricing import PRICES     # noqa: E402

v = sys.argv[1]
L = [json.loads(l) for l in (ROOT / "ledger" / f"{v}.jsonl").read_text(encoding="utf-8").splitlines() if l.startswith("{")]
L = [r for r in L if r["version"] == v and r["user"].startswith("u")]          # the load test's users only
kinds = {}
for l in (ROOT / "loadtest" / "results" / f"{v}.jsonl").read_text(encoding="utf-8").splitlines():
    r = json.loads(l); kinds[r["user"]] = r["kind"]
calls = [r for r in L if r["usage"]]
total = sum(r["cost_usd"] for r in L)
usd = lambda x: f"${x:.4f}"


def pct(xs, q):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(q * len(xs)))]


print(f"== {v}: {len(L)} requests, {len(calls)} model calls, {len(L) - len(calls)} answered from the response cache, total {usd(total)}")

print("\n-- by feature")
by = defaultdict(list)
for r in L:
    by[r["feature"]].append(r)
for f, rs in sorted(by.items(), key=lambda kv: -sum(r["cost_usd"] for r in kv[1])):
    c = sum(r["cost_usd"] for r in rs); n = sum(1 for r in rs if r["usage"])
    print(f"{f:10} {len(rs):4} requests {usd(c):>9}  {c / total:5.1%} of cost   {usd(c / max(n, 1))} per model call")

print("\n-- per user")
per = defaultdict(float)
for r in L:
    per[r["user"]] += r["cost_usd"]
costs = sorted(per.values(), reverse=True)
top = costs[:max(1, len(costs) // 10)]
print(f"{len(costs)} users: mean {usd(statistics.mean(costs))}, median {usd(statistics.median(costs))}, "
      f"p90 {usd(pct(costs, 0.9))}, max {usd(costs[0])}")
print(f"the top 10% of users ({len(top)}) = {sum(top) / total:.0%} of the cost")
for kind in ("light", "regular", "heavy"):
    ks = [c for u, c in per.items() if kinds.get(u) == kind]
    if ks:
        print(f"  {kind:8} {len(ks):3} users  {usd(sum(ks)):>9} ({sum(ks) / total:4.0%})  {usd(statistics.mean(ks))} per user")
for u, c in sorted(per.items(), key=lambda kv: -kv[1])[:5]:
    feats = defaultdict(float)
    for r in L:
        if r["user"] == u:
            feats[r["feature"]] += r["cost_usd"]
    print(f"  {u} ({kinds.get(u)}) {usd(c)}: " + ", ".join(f"{f} {usd(x)}" for f, x in sorted(feats.items(), key=lambda kv: -kv[1])))

print("\n-- chat: each turn vs the SAME conversation's first turn (the whole history is resent every turn)")
sess = defaultdict(list)
for r in sorted((r for r in calls if r["feature"] == "chat"), key=lambda r: r["ts"]):
    u = r["usage"]
    sess[(r["user"], r["session"])].append((r["cost_usd"], u["input_tokens"] + u["cache_read_input_tokens"] + u["cache_write_5m"] + u["cache_write_1h"]))
grow = defaultdict(list)
for turns in sess.values():
    for i, (c, p) in enumerate(turns, 1):
        grow[i].append((c / turns[0][0], p - turns[0][1]))
for t in sorted(grow):
    if t in (1, 2, 3, 5, 8, 10, 12) or t == max(grow):
        xs = grow[t]
        print(f"turn {t:2}: {len(xs):3} conversations, cost {statistics.mean(c for c, _ in xs):4.2f}x turn 1, "
              f"prompt +{statistics.mean(d for _, d in xs):,.0f} tokens")
longest = max(sess.values(), key=len)
print(f"longest conversation: {len(longest)} turns, {usd(sum(c for c, _ in longest))} in total, "
      f"first turn {usd(longest[0][0])}, last turn {usd(longest[-1][0])}")

print("\n-- requests over 100k prompt tokens (Haiku 5.5 bills the WHOLE request at 5x)")
for r in (r for r in calls if r["long_prompt"]):
    u = r["usage"]; p = PRICES[r["model"]]
    normal = (u["input_tokens"] * p["input"] + u["cache_write_5m"] * p["write_5m"] + u["cache_write_1h"] * p["write_1h"]
              + u["cache_read_input_tokens"] * p["cache_read"] + u["output_tokens"] * p["output"]) / 1e6
    print(f"  {r['user']} {r['feature']}: {u['input_tokens'] + u['cache_write_5m'] + u['cache_read_input_tokens']:,} prompt tokens, "
          f"{usd(r['cost_usd'])} (under the line it would be {usd(normal)})")

print("\n-- answers cut off by max_tokens, and thinking")
for f, rs in sorted(by.items()):
    rs = [r for r in rs if r["usage"]]
    if rs:
        cut = sum(r["stop_reason"] == "max_tokens" for r in rs)
        out = sum(r["usage"]["output_tokens"] for r in rs); th = sum(r["usage"].get("thinking_tokens", 0) for r in rs)
        print(f"{f:10} {cut:3}/{len(rs)} cut off ({cut / len(rs):4.0%})   thinking = {th / max(out, 1):4.0%} of output tokens")

print("\n-- caches")
pt = sum(r["usage"]["input_tokens"] + r["usage"]["cache_read_input_tokens"] + r["usage"]["cache_write_5m"] + r["usage"]["cache_write_1h"] for r in calls)
cr = sum(r["usage"]["cache_read_input_tokens"] for r in calls)
print(f"prompt cache: {cr / pt:.1%} of all prompt tokens were cache reads")
hits = [r for r in L if not r["usage"]]
print(f"response cache: {len(hits)} of {len(L)} requests ({len(hits) / len(L):.1%})")
