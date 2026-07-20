"""Cost estimate uses the configured model's rates, not a hardcoded one."""
from __future__ import annotations

from app.services.llm_client import _CostHandler, _rates_for


def test_rates_match_model():
    assert _rates_for("gpt-4o-mini") == (0.15 / 1e6, 0.60 / 1e6)
    assert _rates_for("claude-sonnet-4-6") == (3.0 / 1e6, 15.0 / 1e6)
    assert _rates_for("claude-3-5-haiku-latest") == (0.80 / 1e6, 4.0 / 1e6)
    # Unknown model falls back to the Sonnet-class ballpark.
    assert _rates_for("mystery-model") == (3.0 / 1e6, 15.0 / 1e6)


def test_cost_handler_prices_for_its_model():
    """1000 in + 1000 out on gpt-4o-mini is ~25x cheaper than on Sonnet."""
    class _Msg:
        usage_metadata = {"input_tokens": 1000, "output_tokens": 1000}

    class _Gen:
        message = _Msg()

    class _Result:
        generations = [[_Gen()]]
        llm_output = None

    mini = _CostHandler("gpt-4o-mini")
    mini.on_llm_end(_Result())
    sonnet = _CostHandler("claude-sonnet-4-6")
    sonnet.on_llm_end(_Result())

    assert round(mini.cost, 6) == round(1000 * 0.15 / 1e6 + 1000 * 0.60 / 1e6, 6)
    assert sonnet.cost > mini.cost * 15  # much pricier, as expected
