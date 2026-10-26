"""make_dashboard.py: the cost dashboard, as code -> ai-cost.json. Every panel is one PromQL query you can read here."""
import json
from pathlib import Path

P = 'version=~"$version"'      # every query follows the version picker (v1 = before the fixes, v2 = after)
PANELS = [
    # (title, query, legend, type, unit, width, height)
    ("Total cost", f'sum(ai_cost_usd_total{{{P}}})', "", "stat", "currencyUSD", 4, 5),
    ("Requests (model calls)", f'sum(ai_requests_total{{cached="no",{P}}})', "", "stat", "short", 4, 5),
    ("Cost per model call", f'sum(ai_cost_usd_total{{{P}}}) / sum(ai_requests_total{{cached="no",{P}}})', "", "stat", "currencyUSD", 4, 5),
    ("Answers cut off (max_tokens)", f'sum(ai_stop_reason_total{{stop_reason="max_tokens",{P}}}) / sum(ai_stop_reason_total{{{P}}})', "", "stat", "percentunit", 4, 5),
    ("Requests over 100k tokens (5x price)", f'sum(ai_long_prompt_requests_total{{{P}}}) or vector(0)', "", "stat", "short", 4, 5),
    ("Response cache hit rate", f'sum(ai_requests_total{{cached="yes",{P}}}) / sum(ai_requests_total{{{P}}})', "", "stat", "percentunit", 4, 5),
    ("Cost by feature", f'sum by (feature) (ai_cost_usd_total{{{P}}})', "{{feature}}", "bargauge", "currencyUSD", 8, 9),
    ("Cost per call, by feature", f'sum by (feature) (ai_cost_usd_total{{{P}}}) / sum by (feature) (ai_requests_total{{cached="no",{P}}})', "{{feature}}", "bargauge", "currencyUSD", 8, 9),
    ("Cost by user tier", f'sum by (tier) (ai_cost_usd_total{{{P}}})', "{{tier}}", "bargauge", "currencyUSD", 8, 9),
    ("Spend rate ($/hour), by feature", f'sum by (feature) (rate(ai_cost_usd_total{{{P}}}[2m])) * 3600', "{{feature}}", "timeseries", "currencyUSD", 12, 9),
    ("Tokens billed, by type", f'sum by (type) (ai_tokens_total{{type!="thinking",{P}}})', "{{type}}", "bargauge", "short", 12, 9),
    ("Prompt cache: share of prompt tokens read from cache", f'sum by (feature) (ai_tokens_total{{type="cache_read",{P}}}) / sum by (feature) (ai_tokens_total{{type=~"input|cache_read|cache_write|cache_write_1h",{P}}})', "{{feature}}", "bargauge", "percentunit", 12, 9),
    ("Latency p50 / p99, by feature", f'histogram_quantile(0.99, sum by (feature, le) (rate(ai_request_seconds_bucket{{{P}}}[5m])))', "p99 {{feature}}", "timeseries", "s", 12, 9),
]


def panel(i, title, expr, legend, kind, unit, w, h, x, y):
    p = {"id": i + 1, "title": title, "type": kind, "datasource": {"type": "prometheus", "uid": "prom"},
         "gridPos": {"x": x, "y": y, "w": w, "h": h},
         "targets": [{"refId": "A", "expr": expr, "legendFormat": legend, "instant": kind in ("stat", "bargauge")}],
         "fieldConfig": {"defaults": {"unit": unit, "decimals": 4 if unit == "currencyUSD" else None}, "overrides": []},
         "options": {}}
    bad = "cut off" in title or "100k" in title                     # any of these is a problem: red
    steps = [{"color": "green", "value": None}, {"color": "red", "value": 0.0001}] if bad else [{"color": "blue", "value": None}]
    if title in ("Total cost", "Cost per model call"):
        steps = [{"color": "orange", "value": None}]
    p["fieldConfig"]["defaults"]["thresholds"] = {"mode": "absolute", "steps": steps}
    p["fieldConfig"]["defaults"]["color"] = {"mode": "thresholds"} if kind == "stat" else {"mode": "fixed", "fixedColor": "orange"}
    if kind == "bargauge":
        p["options"] = {"orientation": "horizontal", "displayMode": "basic", "showUnfilled": True, "valueMode": "color",
                        "namePlacement": "left", "text": {"valueSize": 22, "titleSize": 18},
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}
        p["fieldConfig"]["defaults"]["min"] = 0
    if kind == "timeseries":
        p["options"] = {"legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"}}
        p["fieldConfig"]["defaults"]["color"] = {"mode": "palette-classic"}
    if title.startswith("Latency"):
        p["targets"].append({"refId": "B", "expr": expr.replace("0.99", "0.5"), "legendFormat": "p50 {{feature}}"})
    return p


panels, x, y, rowh = [], 0, 0, 0
for i, (t, e, l, k, u, w, h) in enumerate(PANELS):
    if x + w > 24:
        x, y, rowh = 0, y + rowh, 0
    panels.append(panel(i, t, e, l, k, u, w, h, x, y))
    x, rowh = x + w, max(rowh, h)

dash = {"uid": "ai-cost", "title": "CI Copilot: what does my AI app cost?", "timezone": "browser", "refresh": "10s",
        "time": {"from": "now-30m", "to": "now"}, "schemaVersion": 41, "panels": panels,
        "templating": {"list": [{"name": "version", "label": "app version", "type": "custom", "query": "v1,v2,v3",
                                 "current": {"text": "v1", "value": "v1"}, "includeAll": True, "allValue": ".*",
                                 "options": []}]}}
Path(__file__).with_name("ai-cost.json").write_text(json.dumps(dash, indent=1), encoding="utf-8")
print(f"ai-cost.json: {len(panels)} panels")
