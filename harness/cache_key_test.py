#!/usr/bin/env python3
"""cache_key_test.py: would a smarter response-cache key work on REAL CI logs, not on my simulator?
The corpus has 18 real failed runs; 4 of them are the same lint failure on different pull requests.
  key 1  the whole log without timestamps (what v2 used: my simulator only changes timestamps)
  key 2  only the error lines, without GUIDs, SHAs and PR numbers
A good key merges the same failure and NEVER merges different failures: a wrong cached answer is worse than a paid one."""
import collections, glob, hashlib, re
from pathlib import Path

C = Path(__file__).resolve().parents[1] / "loadtest" / "corpus"
TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z")
ERR = re.compile(r"##\[error\]|error|fail|ERR!|exit code|Traceback|exception", re.I)
VOL = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\b[0-9a-f]{7,40}\b|pull/\d+")
LINT = ["pulse-board-31414102193", "pulse-board-31446901943", "pulse-board-31447455044", "pulse-board-31447799064"]


def key_no_timestamps(t):
    return hashlib.md5(TS.sub("", t).encode()).hexdigest()[:8]


def error_lines(t):
    return [VOL.sub("", TS.sub("", l.split("\t")[-1])).strip() for l in t.splitlines() if ERR.search(TS.sub("", l.split("\t")[-1]))]


def key_error_lines(t):
    return hashlib.md5("\n".join(error_lines(t)).encode()).hexdigest()[:8]


logs = {Path(f).name[:-len(".failed.log")]: Path(f).read_text(encoding="utf-8") for f in sorted(glob.glob(str(C / "*.failed.log")))}
for name, key in (("whole log, no timestamps", key_no_timestamps), ("error lines only", key_error_lines)):
    groups = collections.defaultdict(list)
    for r, t in logs.items():
        groups[key(t)].append(r)
    same = len({key(logs[r]) for r in LINT}) == 1
    print(f"== key: {name}")
    print(f"   the 4 re-runs of the same lint failure share one key: {'YES' if same else 'NO'}")
    print(f"   18 real runs -> {len(groups)} keys")
print("\n== what the error-lines key sees in those lint runs:")
for l in error_lines(logs[LINT[0]]):
    print("   ", l)
print("   the actual problem (F401, an unused import) is not on an 'error' line: ANY lint run with 1 problem gets this key")
