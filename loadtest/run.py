#!/usr/bin/env python3
"""run.py: 60 simulated users of CI Copilot, replaying 18 real failed GitHub Actions runs (loadtest/corpus/).
Seeded: v1 and v2 get exactly the same users, requests and logs, in the same order.
  light   (40, free) 1-3 x explain: paste the failed step, ask why
  regular (14)       2 x explain, 1 x summarize (the whole log), a 3-6 turn chat that starts by pasting the failure
  heavy   (6, pro)   summarize "everything": all of a repo's failed runs pasted at once, 2 x fix (Sonnet), a 12-15 turn chat
A pasted failure is the SAME run 30% of the time (a teammate opens the same red build: identical text), and a
re-run of it 70% of the time (same failure, new timestamps): what an exact-match cache would see in real life.
Every request goes to the app over `kubectl port-forward svc/ci-copilot 18080:8080`.
A budget guard stops the run if the app's own meter (its /metrics) passes --budget dollars.
  python loadtest/run.py v1 [--budget 4] [--workers 4]   -> loadtest/results/<version>.jsonl"""
import argparse, json, random, re, threading, time, urllib.request
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent
APP = "http://localhost:18080"
FOLLOW_UPS = ["Why does that happen only in CI and not on my machine?", "What's the exact command to fix it?",
              "Could this be a flaky test instead?", "Which file do I need to change?",
              "Will that break anything else?", "How do I stop this from happening again?",
              "Can you explain that step by step?", "Is there a quicker workaround for today?",
              "What does that error code mean?", "Should I pin the version or upgrade?",
              "How do I reproduce it locally?", "Does the order of the steps matter here?",
              "What would you check first?", "Is my lockfile the problem?", "Ok, and after that?"]


def corpus():
    runs = {}
    for f in sorted((HERE / "corpus").glob("*.failed.log")):
        rid = f.name[:-len(".failed.log")]
        runs[rid] = {"repo": re.sub(r"-\d+$", "", rid), "failed": f.read_text(encoding="utf-8"),
                     "full": (HERE / "corpus" / f"{rid}.full.log").read_text(encoding="utf-8")}
    return runs


TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z")


def rerun(log, rng):
    """The same failure on a later run: every timestamp moves by the same random offset."""
    shift = timedelta(minutes=rng.randint(5, 600), seconds=rng.random() * 60)
    def move(m):
        t = datetime.strptime(m.group(0)[:26], "%Y-%m-%dT%H:%M:%S.%f") + shift
        return t.strftime("%Y-%m-%dT%H:%M:%S.%f") + m.group(0)[26:]
    return TS.sub(move, log)


def plan(seed=31):
    rng, runs = random.Random(seed), corpus()
    ids = list(runs)
    paste = lambda: (lambda r: r if rng.random() < 0.3 else rerun(r, rng))(runs[rng.choice(ids)]["failed"])
    users = []
    for n in range(60):
        kind = "light" if n < 40 else ("regular" if n < 54 else "heavy")
        tier = "pro" if kind == "heavy" or (kind == "regular" and n % 2) else "free"
        u, steps = f"u{n:02d}", []
        if kind == "light":
            for _ in range(rng.randint(1, 3)):
                steps.append(("explain", {"log": paste()}))
        else:
            for _ in range(2 if kind == "regular" else 1):
                steps.append(("explain", {"log": paste()}))
            if kind == "regular":
                steps.append(("summarize", {"log": runs[rng.choice(ids)]["full"]}))
            else:   # "here's everything": every failed run of one repo, pasted in one go
                repo = rng.choice(sorted({r["repo"] for r in runs.values()}))
                steps.append(("summarize", {"log": "\n\n".join(r["full"] for r in runs.values() if r["repo"] == repo)}))
                for _ in range(2):
                    steps.append(("fix", {"log": paste()}))
            turns = rng.randint(3, 6) if kind == "regular" else rng.randint(12, 15)
            first = paste()
            for t in range(turns):
                msg = f"My build failed:\n{first}" if t == 0 else rng.choice(FOLLOW_UPS)
                steps.append(("chat", {"session": "s1", "message": msg}))
        users.append({"user": u, "kind": kind, "tier": tier, "steps": steps})
    order = list(range(60)); rng.shuffle(order)
    return [users[i] for i in order]


def post(path, body):
    req = urllib.request.Request(APP + path, data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.load(r), time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:200]}, time.perf_counter() - t0
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"[:200]}, time.perf_counter() - t0


def metered_cost():
    text = urllib.request.urlopen(APP + "/metrics", timeout=30).read().decode()
    return sum(float(l.rsplit(" ", 1)[1]) for l in text.splitlines() if l.startswith("ai_cost_usd_total{"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version"); ap.add_argument("--budget", type=float, default=4.0); ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    out = HERE / "results" / f"{a.version}.jsonl"; out.parent.mkdir(exist_ok=True)
    assert not out.exists(), f"{out} exists: one run per version"
    users = plan()
    print(f"{len(users)} users, {sum(len(u['steps']) for u in users)} requests, budget ${a.budget:.2f}", flush=True)
    lock, stop, done = threading.Lock(), threading.Event(), [0]
    start_cost = metered_cost()

    def run_user(u):
        for i, (feature, body) in enumerate(u["steps"]):
            if stop.is_set():
                return
            status, resp, secs = post("/" + feature, {"user": u["user"], "tier": u["tier"], **body})
            rec = {"user": u["user"], "kind": u["kind"], "tier": u["tier"], "step": i, "feature": feature,
                   "status": status, "seconds": round(secs, 2), "input_chars": len(body.get("log") or body.get("message")),
                   "error": resp.get("error")}
            with lock:
                done[0] += 1
                with out.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec) + "\n")
                if done[0] % 20 == 0:
                    spent = metered_cost() - start_cost
                    print(f"  {done[0]} requests, ${spent:.4f} metered", flush=True)
                    if spent > a.budget:
                        print(f"BUDGET GUARD: ${spent:.4f} > ${a.budget:.2f}, stopping", flush=True); stop.set()

    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(run_user, users))
    recs = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    bad = [r for r in recs if r["status"] != 200]
    print(f"done: {len(recs)} requests in {time.time() - t0:.0f}s, {len(bad)} failed, ${metered_cost() - start_cost:.4f} metered by the app")
    for r in bad[:5]:
        print("  failed:", r["user"], r["feature"], r["status"], r["error"])


if __name__ == "__main__":
    main()
