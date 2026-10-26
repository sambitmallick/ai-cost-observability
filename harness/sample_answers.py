#!/usr/bin/env python3
"""sample_answers.py <version>: the same 4 requests against the running app, answers saved in full, so v1 and v2 can
be read side by side (the load test only keeps costs). User "sample", so report.py leaves them out.
  python harness/sample_answers.py v1   -> samples/v1.json"""
import json, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
C = ROOT / "loadtest" / "corpus"
APP = "http://localhost:18080"
CASES = [
    ("explain", {"log": (C / "pulse-board-31294862390.failed.log").read_text(encoding="utf-8")}),
    ("summarize", {"log": (C / "ci-speedup-demo-33393244873.full.log").read_text(encoding="utf-8")}),
    ("fix", {"log": (C / "ai-dependency-upgrade-35347808843.failed.log").read_text(encoding="utf-8")}),
    ("chat", {"session": "sample", "message": "My build failed:\n" + (C / "pulse-board-31414102193.failed.log").read_text(encoding="utf-8")}),
    ("chat", {"session": "sample", "message": "What's the exact command to fix it?"}),
]
out = []
for feature, body in CASES:
    req = urllib.request.Request(f"{APP}/{feature}", data=json.dumps({"user": "sample", "tier": "sample", **body}).encode(),
                                 headers={"content-type": "application/json"})
    ans = json.load(urllib.request.urlopen(req, timeout=300))["answer"]
    out.append({"feature": feature, "input": (body.get("log") or body["message"])[-200:], "answer": ans})
    print(f"--- {feature} ({len(ans)} chars) ...{ans[-120:]!r}")
(ROOT / "samples").mkdir(exist_ok=True)
(ROOT / "samples" / f"{sys.argv[1]}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
