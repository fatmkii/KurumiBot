"""Conservative estimates using the official peak-time CNY tariff, 2026-10-04."""
RATES = {
    "deepseek-flash": (0.04, 2.0, 8.0),
    "deepseek-v4-pro": (0.30, 9.0, 27.0),
}
PRICE_NOTE = "Codex OAuth Proxy 调用费用未知；历史 DeepSeek 调用按 2026-10-04 官方高峰费率估算，未计低峰折扣，仅供参考。"
PRICE_SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"


def estimate(model, usage):
    if model not in RATES or not all(type(usage.get(k)) is int and usage[k] >= 0
                                     for k in ("prompt_tokens", "completion_tokens")):
        return None
    prompt = usage["prompt_tokens"]
    cached = min(prompt, max(0, usage.get("prompt_cache_hit_tokens", 0)))
    hit, miss, output = RATES[model]
    return round((cached * hit + (prompt - cached) * miss + usage["completion_tokens"] * output) / 1_000_000, 6)
