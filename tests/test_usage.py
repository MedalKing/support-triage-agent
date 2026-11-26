from support_agent.usage import CallUsage, summarize


def test_cost_counts_cache_writes_and_reads_at_their_own_prices():
    u = CallUsage("claude-opus-5-5", input_tokens=1_000_000, output_tokens=1_000_000,
                  cache_write_tokens=1_000_000, cache_read_tokens=1_000_000)
    assert u.cost_usd() == 4.00 + 20.00 + 5.00 + 0.20


def test_summary_adds_both_models_and_leaves_unknown_prices_open():
    calls = [CallUsage("claude-haiku-5-5", 1000, 100, 0, 3000, 1.5).to_dict(),
             CallUsage("claude-opus-5-5", 2000, 200).to_dict()]
    s = summarize(calls)
    assert s["calls"] == 2 and s["input_tokens"] == 6000 and s["cached_input_tokens"] == 3000
    assert s["cost_usd"] == round((1000 * 0.10 + 100 * 0.50 + 3000 * 0.01 + 2000 * 4 + 200 * 20) / 1e6, 6)
    assert summarize([CallUsage("ollama:qwen3:8b", 10, 10).to_dict()])["cost_usd"] is None
