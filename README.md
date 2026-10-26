# ai-cost-observability: what does my AI app cost per user?

Companion repo for **DevOps Autopilot, episode 31**.

I built a small AI product, **CI Copilot**: paste a failing CI log and it explains, summarizes, chats about or proposes a
fix for it. It calls the real Anthropic API, so every number here is a real bill. Then I made every request
observable:

- **Prometheus + Grafana**: tokens and dollars per feature, model and user tier, spend rate, cache hit rates, latency
- **OpenTelemetry traces in Jaeger**: one span per model call, with the GenAI attributes (model, tokens) plus user and cost
- **A ledger**: one JSON line per request. It's the source of truth for cost per user, and what I checked against the bill.

Why the API and not the `claude` CLI, as in the other episodes? The CLI wraps every call in its own context: the same
tiny request billed 9,607 prompt tokens through `claude -p` and 60 through the API (`harness/cli_vs_api.py`). For a
cost measurement, that wrapper would be most of the bill.

Then 60 simulated users (40 light, 14 regular, 6 heavy) replayed **18 real failed GitHub Actions runs** from this
channel's repos: 292 requests per run, seeded, identical every time.

**The whole experiment cost $1.80**, and the Anthropic Console agreed with my meter to the cent.

## Results

| | v1 (as first built) | v3 (after the fixes) | |
|---|---|---|---|
| Total for 60 users, 292 requests | $0.8615 | **$0.5413** | -37% |
| Answers cut off (max_tokens) | **74.6%** | 0% | |
| Hidden thinking, share of output tokens | 77.7% | 26.2% | |
| Requests over 100k tokens (billed 5x) | 2 | 0 | |
| Median user | $0.0031 | $0.0030 | |
| Most expensive user | $0.2131 (69x the median) | $0.0704 | -67% |
| Top 10% of users, share of the bill | 76% | 68% | |
| summarize / chat / explain / fix | | -80% / -56% / 0% / -10% | |

## What the dashboard showed

1. **The average user doesn't exist.** The median user cost $0.003 and the top user $0.21. Six heavy users were 76% of
   the bill. Price per user from the distribution, not the mean.
2. **3 in 4 answers were cut off, and I was paying for it.** Claude Haiku 5.5 thinks by default, and thinking counts
   toward `max_tokens`. My limits (400 to 600) left no room for the answer. Thinking was 78% of all output tokens. The
   docs say it plainly: *"Thinking is on by default and counts toward max_tokens, so leave room for it."*
3. **One paste can cost 5x.** Haiku 5.5 bills a request whose prompt is over 100,000 tokens at 5x, for the **whole**
   request. Two heavy users pasted "everything", 100k and 300k tokens: $0.20, a quarter of the v1 bill.
4. **The Sonnet feature is the bill.** `fix` (Claude Sonnet 5.5) was 4% of requests and 41% of the cost in v1, 58% in v3.

## The fixes (k8s/app-v3.yaml)

- `output_config.effort: low` for explain and chat (the docs' advice for chat and simple requests), `medium` for fix,
  and `max_tokens` with room for thinking. Cut-off answers: 74.6% to **0%**.
- Count the tokens first (the counting endpoint is free) and trim a paste over 90k tokens to its error lines and its
  end. summarize: **-80%**, no request over the 100k line.
- A prompt-cache breakpoint on the newest chat message, so the next turn reads the whole conversation from the cache
  at 10% of the price. chat: **-56%**. Later turns cost 0.2 to 0.5x the first, even as the conversation grows.

## Where it went wrong

- **v2 claimed -60%. It was my simulator.** v2 also changed the response-cache key to ignore timestamps, and the cache
  hit rate jumped from 7% to 36%. But my load test makes a "re-run" of a failure by changing only its timestamps. On
  the 4 real re-runs of the same failure in the corpus, that key matched **0**. (`harness/cache_key_test.py`)
- **A smarter key was worse.** Keying on the error lines matched all 4 real re-runs, but the only error lines in a lint
  failure are "Found 1 error." and "Process completed with exit code 1." **Any** one-problem lint run would get the
  cached answer for a different file and rule. A wrong cached answer is worse than a paid one. v3 is v2 without that
  change, and it's the honest result.
- **Claude Code's cost forensics were right, and wrong.** Given only the ledgers and the price table, it found why
  one user cost 69x the median (one 300k-token request billed at 5x), and checked every request's cost against the
  price table. It also said the v3 chat history wasn't cached. It is: 130 of 148 chat calls read it from the cache.
  Check what the AI tells you. (`sessions/`)
- **Counters reset when the pod restarts.** Every deploy of the app starts its Prometheus counters from zero, so a
  "total" panel only works for a time range before the restart. The ledger doesn't have that problem.
- **Lower effort changed cost less than I expected.** explain: same cost, but complete answers. fix: only -10%, though
  its median latency went from 10.7 s to 6.2 s.
- I measured whether answers were **complete**, not whether they were **correct**. `samples/` has the same 5 requests
  answered by v1 and v3, side by side. Read them yourself.

## Why there's no per-user label in Prometheus

Cost per user is the question, but a `user` label on every metric creates one time series per user, per feature,
per model, per tier. With real users that grows without bound. Per-user cost comes from the ledger and the traces
instead. Prometheus keeps the labels that stay small: feature, model, tier, version.

## Run it yourself

You need: Podman (or Docker), kind, kubectl, Python 3.11+, and an Anthropic API key. Use a separate workspace with a
spend limit; the load test has its own budget guard (`--budget`, default $4).

```bash
kind create cluster --name aicost
podman build -t localhost/ci-copilot:1.0 app/
# load localhost/ci-copilot:1.0 and the prometheus, grafana and jaeger images into kind (see demo commands)
kubectl apply -f k8s/namespace.yaml
kubectl -n ai-cost create configmap grafana-provisioning --from-file=grafana/datasources.yaml --from-file=grafana/dashboards.yaml
kubectl -n ai-cost create configmap grafana-dashboards --from-file=grafana/ai-cost.json
kubectl apply -f k8s/prometheus.yaml -f k8s/jaeger.yaml -f k8s/grafana.yaml
kubectl -n ai-cost create secret generic anthropic --from-file=api-key=$HOME/.anthropic/your.key   # never commit it
kubectl apply -f k8s/app.yaml

kubectl -n ai-cost port-forward svc/ci-copilot 18080:8080 &
python loadtest/run.py v1 --budget 4
bash scripts/save_ledger.sh v1
python harness/report.py v1

kubectl apply -f k8s/app-v3.yaml      # (build localhost/ci-copilot:1.1 from the same app/ first)
python loadtest/run.py v3 && bash scripts/save_ledger.sh v3
python harness/compare.py v1 v3
```

Grafana: `kubectl -n ai-cost port-forward svc/grafana 3000` and open http://localhost:3000 (the dashboard has a
version picker). Jaeger: port 16686.

## Layout

```
app/          CI Copilot (FastAPI): main.py, pricing.py (the price table), prompts.py
k8s/          the app (v1, v2, v3), Prometheus, Grafana, Jaeger
grafana/      the dashboard as code (make_dashboard.py -> ai-cost.json)
loadtest/     60 seeded users and the 18 real failed CI runs they replay
ledger/       every request of v1, v2 and v3 (tokens, cost, stop reason, user)
harness/      report, compare, cache_key_test, cli_vs_api, sample answers, session export
samples/      the same 5 requests answered by v1 and v3
sessions/     the two live Claude Code sessions (exported: messages and tool calls only)
cost-desk/    what Claude Code was given for those sessions
```

Prices are from the official pricing page, checked on 2026-10-26 (`app/pricing.py`). They change; check them
before you trust a number.
