# CI Copilot ledger

One JSON line per request to our AI app (CI Copilot: explain / summarize / chat / fix a failing CI build).
ledger-v1.jsonl: the app as first shipped. ledger-v3.jsonl: the current version. Same 60 users, same 292 requests.

Fields: ts, version, user, tier (free/pro), feature, model, session (chat only),
usage {input_tokens, output_tokens, cache_read_input_tokens, cache_write_5m, cache_write_1h, thinking_tokens},
cost_usd (computed by pricing.py from the usage), long_prompt, stop_reason, seconds, id.
usage is null when the answer came from the app's response cache (cost 0).
