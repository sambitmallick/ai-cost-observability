#!/usr/bin/env python3
"""cli_vs_api.py: why this episode pays for the API instead of using the claude CLI like the others.
The same tiny request, once through `claude -p` and once straight to the API, and the tokens each one bills.
The CLI wraps every call in its own context: fine for an agent, but it would drown a cost measurement.
  python harness/cli_vs_api.py      (needs `claude` on PATH and ANTHROPIC_API_KEY or ~/.anthropic/ep31.key)"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from pricing import cost_usd   # noqa: E402

SYSTEM = "You explain CI failures in two sentences."
LOG = "npm ERR! code ERESOLVE unable to resolve dependency tree: react@19 vs react-dom@18"
MODEL = "claude-haiku-5-5"


def show(name, u, cost):
    prompt = u["input_tokens"] + u["cache_read_input_tokens"] + u["cache_write_5m"] + u["cache_write_1h"]
    print(f"{name:12} prompt tokens {prompt:>7,}   output {u['output_tokens']:>4}   cost ${cost:.7f}")


# 1. the CLI, as in every earlier episode (no tools, our own system prompt, an empty folder)
env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
p = subprocess.run([shutil.which("claude"), "-p", "--no-session-persistence", "--tools", "", "--output-format", "json",
                    "--model", "haiku", "--system-prompt", SYSTEM], input=LOG, capture_output=True, text=True,
                   encoding="utf-8", env=env, cwd=tempfile.mkdtemp(), timeout=300)
d = json.loads(p.stdout); x = d["usage"]; cc = x.get("cache_creation", {})
show("claude -p", {"input_tokens": x["input_tokens"], "output_tokens": x["output_tokens"],
                   "cache_read_input_tokens": x["cache_read_input_tokens"],
                   "cache_write_5m": cc.get("ephemeral_5m_input_tokens", 0), "cache_write_1h": cc.get("ephemeral_1h_input_tokens", 0)},
     d["total_cost_usd"])

# 2. the API directly (plain HTTP, no SDK needed): only our system prompt and our message
import urllib.request   # noqa: E402
key = os.environ.get("ANTHROPIC_API_KEY") or (Path.home() / ".anthropic" / "ep31.key").read_text().strip()
req = urllib.request.Request("https://api.anthropic.com/v1/messages", method="POST",
                             headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                             data=json.dumps({"model": MODEL, "max_tokens": 300, "system": SYSTEM,
                                              "messages": [{"role": "user", "content": LOG}]}).encode())
x = json.load(urllib.request.urlopen(req, timeout=120))["usage"]
u = {"input_tokens": x["input_tokens"], "output_tokens": x["output_tokens"], "cache_read_input_tokens": x.get("cache_read_input_tokens") or 0,
     "cache_write_5m": x.get("cache_creation_input_tokens") or 0, "cache_write_1h": 0}
show("API", u, cost_usd(MODEL, u)[0])
