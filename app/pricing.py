"""pricing.py: what one API call costs, from the token counts the API returns.
Prices in USD per million tokens, from platform.claude.com/docs/en/about-claude/pricing (checked 2026-10-26).
  input        regular input tokens
  write_5m     cache writes with the default 5-minute lifetime (1.25x input)
  write_1h     cache writes with the 1-hour lifetime (2x input)
  cache_read   cache hits (10% of input on Haiku)
  output       output tokens (thinking tokens are billed as output)"""

PRICES = {
    "claude-haiku-5-5":  {"input": 0.10, "write_5m": 0.125, "write_1h": 0.20, "cache_read": 0.01, "output": 0.50},
    "claude-sonnet-5-5": {"input": 2.00, "write_5m": 2.50,  "write_1h": 4.00, "cache_read": 0.10, "output": 10.00},
}

# Haiku 5.5 only: a request whose prompt is over 100,000 tokens (input + cache reads + cache writes) pays these prices
# for the WHOLE request, not just the tokens above the line.
LONG_PROMPT_TOKENS = 100_000
LONG_PRICES = {
    "claude-haiku-5-5": {"input": 0.50, "write_5m": 0.625, "write_1h": 1.00, "cache_read": 0.05, "output": 2.50},
}


def prompt_tokens(u: dict) -> int:
    return u["input_tokens"] + u["cache_read_input_tokens"] + u["cache_write_5m"] + u["cache_write_1h"]


def cost_usd(model: str, u: dict) -> tuple[float, bool]:
    """u: input_tokens, output_tokens, cache_read_input_tokens, cache_write_5m, cache_write_1h.
    Returns (cost, long_prompt)."""
    long_prompt = model in LONG_PRICES and prompt_tokens(u) > LONG_PROMPT_TOKENS
    p = LONG_PRICES[model] if long_prompt else PRICES[model]
    cost = (u["input_tokens"] * p["input"] + u["cache_write_5m"] * p["write_5m"] + u["cache_write_1h"] * p["write_1h"]
            + u["cache_read_input_tokens"] * p["cache_read"] + u["output_tokens"] * p["output"]) / 1_000_000
    return cost, long_prompt
