"""CI Copilot: a small AI product with four features, each calling the real Anthropic API.
Every call is metered three ways:
  - Prometheus metrics: tokens and cost per feature / model / user tier (no per-user label: see the README)
  - an OpenTelemetry trace span per call, with the GenAI attributes (model, tokens) + user, feature and cost
  - a JSON ledger line per request on stdout: the per-user source of truth, and what we reconcile with the bill
POST /explain /summarize /fix  {"user": "u17", "tier": "free", "log": "..."}
POST /chat                     {"user": "u17", "tier": "free", "session": "s1", "message": "..."}"""
import hashlib, json, os, re, time
from collections import OrderedDict

from anthropic import AsyncAnthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel

from pricing import cost_usd, prompt_tokens
from prompts import FEATURES, GUIDE

MODELS = {"explain": os.environ.get("EXPLAIN_MODEL", "claude-haiku-5-5"),
          "summarize": os.environ.get("SUMMARIZE_MODEL", "claude-haiku-5-5"),
          "chat": os.environ.get("CHAT_MODEL", "claude-haiku-5-5"),
          "fix": os.environ.get("FIX_MODEL", "claude-sonnet-5-5")}
MAX_TOKENS = {"explain": 400, "summarize": 600, "chat": 500, "fix": 1500, **json.loads(os.environ.get("MAX_TOKENS", "{}"))}
RESPONSE_CACHE = os.environ.get("RESPONSE_CACHE", "on") == "on"     # exact-match answers, in memory
VERSION = os.environ.get("APP_VERSION", "v1")
# The v2 fixes. Every one is off by default, so v1 runs exactly as it did; v2 turns them on in its Deployment.
EFFORT = json.loads(os.environ.get("EFFORT", "{}"))                  # per feature: low / medium / high (API default if absent)
PROMPT_LIMIT = int(os.environ.get("PROMPT_LIMIT", "0"))              # trim pasted logs to stay under this many prompt tokens
CACHE_KEY = os.environ.get("CACHE_KEY", "exact")                    # exact | no-timestamps (a re-run of the same failure)
CHAT_HISTORY_CACHE = os.environ.get("CHAT_HISTORY_CACHE", "off") == "on"   # prompt-cache the conversation so far
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z")
ERROR_LINE = re.compile(r"##\[error\]|error|fail|ERR!|exit code|Traceback|exception", re.I)

client = AsyncAnthropic()            # reads ANTHROPIC_API_KEY (from the Kubernetes Secret)
app = FastAPI(title="CI Copilot")

provider = TracerProvider(resource=Resource.create({"service.name": "ci-copilot", "service.version": VERSION}))
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))   # OTEL_EXPORTER_OTLP_ENDPOINT
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("ci-copilot")

L = ["feature", "model", "tier", "version"]          # version: v1 as first built, v2 and v3 with the fixes
REQUESTS = Counter("ai_requests_total", "Requests", L + ["cached"])
TOKENS = Counter("ai_tokens_total", "Tokens billed", L + ["type"])
COST = Counter("ai_cost_usd_total", "Cost in USD, computed from the API's token counts", L)
LONG = Counter("ai_long_prompt_requests_total", "Haiku requests over 100k prompt tokens (priced 5x)", L)
STOPS = Counter("ai_stop_reason_total", "Why the model stopped: end_turn, or max_tokens = the answer was cut off", L + ["stop_reason"])
LATENCY = Histogram("ai_request_seconds", "Model call latency", ["feature", "model", "version"],
                    buckets=(0.5, 1, 2, 3, 5, 8, 13, 21, 34, 55))

cache: "OrderedDict[str, str]" = OrderedDict()
sessions: dict[str, list] = {}


class Ask(BaseModel):
    user: str
    tier: str = "free"
    log: str = ""
    session: str = ""
    message: str = ""


def usage_of(resp) -> dict:
    u = resp.usage
    cc = getattr(u, "cache_creation", None)
    return {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
            "cache_read_input_tokens": u.cache_read_input_tokens or 0,
            "cache_write_5m": (cc.ephemeral_5m_input_tokens if cc else u.cache_creation_input_tokens) or 0,
            "cache_write_1h": (cc.ephemeral_1h_input_tokens if cc else 0) or 0,
            "thinking_tokens": getattr(getattr(u, "output_tokens_details", None), "thinking_tokens", 0) or 0}  # part of output


def system_for(feature):
    return [{"type": "text", "text": GUIDE, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": FEATURES[feature]}]


def shrink(log: str, keep_tail: int) -> str:
    """Keep what a reviewer needs from a huge paste: every error line with 15 lines before and 5 after, plus the tail."""
    lines = log.splitlines()
    keep = set(range(max(0, len(lines) - keep_tail), len(lines)))
    for i, l in enumerate(lines):
        if ERROR_LINE.search(l):
            keep.update(range(max(0, i - 15), min(len(lines), i + 6)))
    out, last = [], -1
    for i in sorted(keep):
        if i != last + 1:
            out.append(f"[... {i - last - 1} lines omitted ...]")
        out.append(lines[i]); last = i
    return "\n".join(out)


async def fit(feature: str, log: str) -> tuple[str, int]:
    """PROMPT_LIMIT: count the tokens first (the counting endpoint is free) and trim the paste until it fits.
    Returns (log, tokens before trimming or 0)."""
    if not PROMPT_LIMIT:
        return log, 0
    count = lambda text: client.messages.count_tokens(model=MODELS[feature], system=system_for(feature),
                                                      messages=[{"role": "user", "content": text}])
    n = (await count(log)).input_tokens
    if n <= PROMPT_LIMIT:
        return log, 0
    tail, small = 400, log
    while (m := (await count(small := shrink(log, tail))).input_tokens) > PROMPT_LIMIT and tail > 50:
        tail //= 2
    if m > PROMPT_LIMIT:                       # still too big: keep the end, sized by the measured ratio
        small = small[-int(len(small) * PROMPT_LIMIT / m * 0.95):]
    return f"[CI Copilot trimmed this paste from {n:,} tokens to the error lines and the end]\n" + small, n


async def call_model(feature: str, a: Ask, messages: list, trimmed_from: int = 0) -> str:
    model = MODELS[feature]
    labels = dict(feature=feature, model=model, tier=a.tier, version=VERSION)
    key_src = json.dumps([feature, model, messages])
    if CACHE_KEY == "no-timestamps":           # the same failure on a re-run differs only in its timestamps
        key_src = TIMESTAMP.sub("", key_src)
    key = hashlib.sha256(key_src.encode()).hexdigest()
    if RESPONSE_CACHE and feature != "chat" and key in cache:
        cache.move_to_end(key)
        REQUESTS.labels(**labels, cached="yes").inc()
        ledger(a, feature, model, None, 0.0, False, 0.0, "response-cache", None)
        return cache[key]
    system = system_for(feature)
    if feature == "chat" and CHAT_HISTORY_CACHE:
        # a cache breakpoint on the newest message: the next turn reads the whole conversation so far from the cache
        last = messages[-1]
        messages = messages[:-1] + [{"role": last["role"], "content": [
            {"type": "text", "text": last["content"], "cache_control": {"type": "ephemeral"}}]}]
    extra = {"output_config": {"effort": EFFORT[feature]}} if feature in EFFORT else {}
    with tracer.start_as_current_span(f"chat {model}") as span:
        span.set_attributes({"gen_ai.system": "anthropic", "gen_ai.operation.name": "chat",
                             "gen_ai.request.model": model, "gen_ai.request.max_tokens": MAX_TOKENS[feature],
                             "app.feature": feature, "app.user.id": a.user, "app.user.tier": a.tier,
                             "app.effort": EFFORT.get(feature, "default"), "app.trimmed_from_tokens": trimmed_from})
        t0 = time.perf_counter()
        try:
            resp = await client.messages.create(model=model, max_tokens=MAX_TOKENS[feature], system=system,
                                                messages=messages, **extra)
        except Exception as e:
            span.record_exception(e)
            raise HTTPException(502, f"model call failed: {type(e).__name__}")
        secs = time.perf_counter() - t0
        u = usage_of(resp)
        cost, long_prompt = cost_usd(model, u)
        span.set_attributes({"gen_ai.response.id": resp.id, "gen_ai.response.model": resp.model,
                             "gen_ai.usage.input_tokens": u["input_tokens"], "gen_ai.usage.output_tokens": u["output_tokens"],
                             "gen_ai.usage.cache_read.input_tokens": u["cache_read_input_tokens"],
                             "gen_ai.usage.cache_creation.input_tokens": u["cache_write_5m"] + u["cache_write_1h"],
                             "app.prompt_tokens": prompt_tokens(u), "app.cost_usd": cost, "app.long_prompt": long_prompt,
                             "gen_ai.response.finish_reasons": [resp.stop_reason or ""]})
    LATENCY.labels(feature, model, VERSION).observe(secs)
    REQUESTS.labels(**labels, cached="no").inc()
    for t, k in (("input", "input_tokens"), ("output", "output_tokens"), ("cache_read", "cache_read_input_tokens"),
                 ("cache_write", "cache_write_5m"), ("cache_write_1h", "cache_write_1h")):
        TOKENS.labels(**labels, type=t).inc(u[k])
    TOKENS.labels(**labels, type="thinking").inc(u["thinking_tokens"])     # already counted in output
    COST.labels(**labels).inc(cost)
    STOPS.labels(**labels, stop_reason=resp.stop_reason or "none").inc()
    if long_prompt:
        LONG.labels(**labels).inc()
    ledger(a, feature, model, u, cost, long_prompt, secs, resp.id, resp.stop_reason, trimmed_from)
    text = "".join(b.text for b in resp.content if b.type == "text")
    if RESPONSE_CACHE and feature != "chat":
        cache[key] = text
        if len(cache) > 500:
            cache.popitem(last=False)
    return text


def ledger(a: Ask, feature, model, u, cost, long_prompt, secs, rid, stop, trimmed_from=0):
    print(json.dumps({"ts": time.time(), "version": VERSION, "user": a.user, "tier": a.tier, "feature": feature,
                      "model": model, "session": a.session or None, "usage": u, "cost_usd": round(cost, 8),
                      "long_prompt": long_prompt, "stop_reason": stop, "effort": EFFORT.get(feature, "default"),
                      "trimmed_from": trimmed_from, "seconds": round(secs, 2), "id": rid}), flush=True)


async def about_log(feature: str, a: Ask):
    if not a.log.strip():
        raise HTTPException(400, "paste a CI log")
    try:
        log, trimmed_from = await fit(feature, a.log)
    except Exception as e:
        raise HTTPException(502, f"token count failed: {type(e).__name__}")
    return {"answer": await call_model(feature, a, [{"role": "user", "content": log}], trimmed_from)}


@app.post("/explain")
async def explain(a: Ask):
    return await about_log("explain", a)


@app.post("/summarize")
async def summarize(a: Ask):
    return await about_log("summarize", a)


@app.post("/fix")
async def fix(a: Ask):
    return await about_log("fix", a)


@app.post("/chat")
async def chat(a: Ask):
    # the whole conversation goes to the model on every turn: that's how a chat API works
    history = sessions.setdefault(f"{a.user}/{a.session}", [])
    history.append({"role": "user", "content": a.message})
    try:
        answer = await call_model("chat", a, history)
    except HTTPException:
        history.pop()                 # don't keep a question that never got an answer
        raise
    history.append({"role": "assistant", "content": answer})
    return {"answer": answer, "turn": len(history) // 2}


@app.get("/metrics")
def metrics():
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": VERSION}
