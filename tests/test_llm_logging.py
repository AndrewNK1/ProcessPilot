"""What the LangChain adapter keeps from a provider reply, and the Gemini settings.

No network: the chat model is a stand-in that returns a prepared message, and
the Gemini client is only constructed, never called.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from processpilot.agent.catalogue import ModelSettings, load_agent_settings
from processpilot.agent.llm import ChatTurn, LangChainLLM, TokenUsage, bare_model_name, cost_usd

GEMINI = "google_genai:gemini-3.6-flash"


class _Answer(BaseModel):
    text: str


class _FakeChat:
    """``with_structured_output(...).invoke(...)`` returns a prepared raw message."""

    def __init__(self, raw: AIMessage) -> None:
        self.raw = raw

    def with_structured_output(self, schema: type[BaseModel], **_: Any) -> _FakeChat:
        return self

    def invoke(self, _messages: list[Any]) -> dict[str, Any]:
        return {"raw": self.raw, "parsed": _Answer(text="ok"), "parsing_error": None}


def _llm_returning(raw: AIMessage) -> LangChainLLM:
    llm = LangChainLLM(GEMINI, ModelSettings(env_var="X", structured_output_method="json_schema"))
    llm._chat = _FakeChat(raw)  # skip init_chat_model: no client, no network
    return llm


def test_gemini_reply_keeps_finish_reason_served_model_and_reasoning_tokens() -> None:
    # Shape of a langchain-google-genai 4.4.2 reply: `finish_reason`, `model_name`,
    # thinking tokens inside output_tokens and broken out as `reasoning`.
    raw = AIMessage(
        content='{"text": "ok"}',
        response_metadata={"finish_reason": "MAX_TOKENS", "model_name": "gemini-3.6-flash"},
        usage_metadata={
            "input_tokens": 9524,
            "output_tokens": 3823,
            "total_tokens": 13347,
            "input_token_details": {"cache_read": 7586},
            "output_token_details": {"reasoning": 2900},
        },
    )
    resp = _llm_returning(raw).invoke([ChatTurn(role="user", content="q")], _Answer)
    assert resp.stop_reason == "MAX_TOKENS"
    assert resp.served_model == "gemini-3.6-flash"
    assert resp.usage.reasoning_tokens == 2900
    assert resp.usage.output_tokens == 3823 and resp.usage.cache_read_tokens == 7586
    assert resp.parsed == {"text": "ok"}


def test_anthropic_stop_reason_still_read() -> None:
    raw = AIMessage(
        content="{}",
        response_metadata={"stop_reason": "end_turn", "model": "claude-opus-5-5"},
        usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    )
    resp = _llm_returning(raw).invoke([ChatTurn(role="user", content="q")], _Answer)
    assert resp.stop_reason == "end_turn" and resp.served_model == "claude-opus-5-5"
    assert resp.usage.reasoning_tokens is None


def test_gemini_settings_send_no_sampling_params_and_log_seed_caveat() -> None:
    model = load_agent_settings().model
    kwargs = model.kwargs_for(GEMINI)
    assert not {"temperature", "top_p", "top_k"} & set(kwargs)
    assert kwargs["seed"] == 42
    notes = " ".join(model.notes_for(GEMINI))
    assert "seed=42" in notes and "UNVERIFIED" in notes
    assert model.notes_for("anthropic:claude-opus-5-5") == []


def test_gemini_client_sends_no_temperature() -> None:
    # langchain-google-genai drops its 0.7 default for Gemini 3+ when temperature
    # is not set explicitly; if a later version stops doing so, this fails.
    from langchain_google_genai import ChatGoogleGenerativeAI

    kwargs = load_agent_settings().model.kwargs_for(GEMINI)
    chat = ChatGoogleGenerativeAI(
        model=bare_model_name(GEMINI), google_api_key="not-a-real-key", **kwargs
    )
    assert chat.temperature is None and chat.top_p is None and chat.top_k is None


def test_gemini_list_price_gives_a_cost() -> None:
    price = load_agent_settings().pricing_usd_per_mtok[bare_model_name(GEMINI)]
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000, cache_read_tokens=0)
    # 0.75 input + 3.75 output per million tokens (paid list price, through 2026-12-31)
    assert cost_usd(usage, price) == pytest.approx(4.50)
