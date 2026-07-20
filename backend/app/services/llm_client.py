"""LLM layer, built on LangChain.

Design goals
------------
* One seam (:class:`LLMClient`) that every service depends on. It wraps a
  LangChain ``ChatAnthropic`` chat model so the services compose real LCEL
  chains (``prompt | llm.structured(Schema)`` / ``prompt | llm.text()``).
* Structured JSON uses ``ChatAnthropic.with_structured_output`` — LangChain
  drives Claude via tool-calling under the hood, the version-robust way to get
  schema-valid output.
* If no API key is configured (``settings.use_mock_llm``), ``client.mock`` is
  ``True``: no model is constructed and services fall back to their own
  deterministic heuristics. The whole loop + test-suite run offline and free.
* A rough cost estimate is accumulated from token usage reported on each
  response, surfaced in the agent run.

Swapping providers later: build a different LangChain chat model here and select
on ``settings.llm_provider``. The service layer never imports a provider directly.
"""
from __future__ import annotations

from typing import Any, Optional, Type
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.output_parsers import StrOutputParser
from langchain_core.outputs import LLMResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel

from ..config import Settings, get_settings

# Approximate $/1M-token (input, output) rates for the cost estimate only —
# not billing-accurate. Keyed by a substring of the model id, matched
# most-specific first. Update as prices change.
_PRICES: list[tuple[str, float, float]] = [
    ("gpt-4o-mini", 0.15, 0.60),
    ("gpt-4.1-mini", 0.40, 1.60),
    ("gpt-4.1", 2.00, 8.00),
    ("gpt-4o", 2.50, 10.00),
    ("claude-3-5-haiku", 0.80, 4.00),
    ("haiku", 1.00, 5.00),
    ("claude-3-opus", 15.00, 75.00),
    ("opus", 15.00, 75.00),
    ("sonnet", 3.00, 15.00),
]
# Fallback when the model id matches nothing above (Sonnet-class ballpark).
_DEFAULT_RATE = (3.0, 15.0)


def _rates_for(model: str) -> tuple[float, float]:
    """(input, output) $/token for a model id — matched by substring."""
    low = (model or "").lower()
    for key, rin, rout in _PRICES:
        if key in low:
            return rin / 1_000_000, rout / 1_000_000
    return _DEFAULT_RATE[0] / 1_000_000, _DEFAULT_RATE[1] / 1_000_000


class _CostHandler(BaseCallbackHandler):
    """Accumulates a rough dollar cost from token usage, priced for the
    actually-configured model (not a hardcoded Sonnet rate)."""

    def __init__(self, model: str = "") -> None:
        self.cost = 0.0
        self._rate_in, self._rate_out = _rates_for(model)

    def reset(self) -> None:
        self.cost = 0.0

    def on_llm_end(self, response: LLMResult, *, run_id: UUID | None = None, **kwargs: Any) -> None:
        in_tok, out_tok = _usage_from_result(response)
        self.cost += in_tok * self._rate_in + out_tok * self._rate_out


def _usage_from_result(response: LLMResult) -> tuple[int, int]:
    """Best-effort extraction of (input_tokens, output_tokens) from a result."""
    for gen_list in response.generations:
        for gen in gen_list:
            msg = getattr(gen, "message", None)
            usage = getattr(msg, "usage_metadata", None)
            if usage:
                return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
    llm_output = response.llm_output or {}
    usage = llm_output.get("usage") or llm_output.get("token_usage") or {}
    return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))


class LLMClient:
    """Thin adapter around a LangChain chat model with mock fallback + costing."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.mock = self.settings.use_mock_llm
        self._cost_handler = _CostHandler(self.settings.llm_model)
        self.model = None
        if not self.mock:
            self.model = self._make_model()
            # If the model cannot be constructed, degrade to mock rather than crash.
            if self.model is None:
                self.mock = True

    def _make_model(self):
        """Build the LangChain chat model for the configured provider.

        Both providers expose the same ``BaseChatModel`` interface, so every
        service chain (``prompt | llm.structured(...)`` / ``| llm.text()``) works
        unchanged. Select with ``LLM_PROVIDER`` (+ the matching API key).
        """
        try:
            if self.settings.llm_provider == "openai":
                from langchain_openai import ChatOpenAI

                kwargs = {}
                if self.settings.openai_base_url:
                    # OpenAI-compatible proxy/relay endpoint.
                    kwargs["base_url"] = self.settings.openai_base_url
                return ChatOpenAI(
                    model=self.settings.llm_model,
                    api_key=self.settings.openai_api_key,
                    max_tokens=2048,
                    timeout=60,
                    callbacks=[self._cost_handler],
                    **kwargs,
                )

            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                model=self.settings.llm_model,
                api_key=self.settings.anthropic_api_key,
                max_tokens=2048,
                timeout=60,
                callbacks=[self._cost_handler],
            )
        except Exception:  # pragma: no cover - defensive, offline fallback
            return None

    # ---- LCEL building blocks used by the services ----
    def structured(self, schema: Type[BaseModel]) -> Runnable:
        """A Runnable that returns ``schema``-typed structured output.

        In mock mode there is no model, so callers must guard on ``self.mock``;
        we still return a Runnable that raises if it is ever invoked, to make a
        missed guard loud rather than silent.
        """
        if self.mock or self.model is None:
            return RunnableLambda(_mock_guard)
        return self.model.with_structured_output(schema)

    def text(self) -> Runnable:
        """A Runnable ``model | StrOutputParser`` for free-form text replies."""
        if self.mock or self.model is None:
            return RunnableLambda(_mock_guard)
        return self.model | StrOutputParser()

    # ---- cost tracking ----
    @property
    def cost_estimate(self) -> float:
        return round(self._cost_handler.cost, 6)

    def reset_cost(self) -> None:
        self._cost_handler.reset()


def _mock_guard(_: Any) -> Any:  # pragma: no cover - only hit on a missed guard
    raise RuntimeError("LLM chain invoked while in mock mode; guard on client.mock first.")


_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
