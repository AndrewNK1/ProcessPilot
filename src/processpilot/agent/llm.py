"""The one place a chat model is called.

WHY a protocol in front of LangChain: nodes depend on ``StructuredLLM`` only,
so unit tests inject a scripted fake and never reach a real model. The real
implementation wraps ``init_chat_model(PP_MODEL)`` (provider-swappable) with
``with_structured_output(..., include_raw=True)``, so the raw output, token
usage and any parse error are kept for the run log instead of being swallowed.

A parse failure is not an exception here: it comes back as
``LLMResponse(parsed=None, parse_error=...)`` and the caller treats it as a
validation issue to retry, like any other.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, Field

from processpilot.agent.catalogue import ModelSettings, Price

T = TypeVar("T", bound=BaseModel)


class ChatTurn(BaseModel):
    """One message of a conversation, provider-neutral."""

    role: str  # "system" | "user" | "assistant"
    content: str


class TokenUsage(BaseModel):
    """Token counts as reported by the provider (``None`` = not reported)."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None


class LLMResponse(BaseModel):
    """What one call produced, parsed or not."""

    parsed: dict[str, Any] | None = None
    parse_error: str | None = None
    raw_text: str
    raw_content: Any = None
    stop_reason: str | None = None
    served_model: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)


class StructuredLLM(Protocol):
    """A chat model that answers with an instance of ``schema``."""

    model_id: str

    def invoke(self, turns: list[ChatTurn], schema: type[BaseModel]) -> LLMResponse:
        """One model call; never raises on unparseable output."""
        ...


def cost_usd(usage: TokenUsage, price: Price | None) -> float | None:
    """Cost of one call, or ``None`` if the price or counts are unknown.

    LangChain's ``input_tokens`` already includes cache reads and writes, so
    those are subtracted before applying the base input price.
    """
    if price is None or usage.input_tokens is None or usage.output_tokens is None:
        return None
    cache_read = usage.cache_read_tokens or 0
    cache_write = usage.cache_creation_tokens or 0
    fresh = usage.input_tokens - cache_read - cache_write
    return (
        fresh * price.input
        + cache_read * price.cache_read
        + cache_write * price.cache_write
        + usage.output_tokens * price.output
    ) / 1_000_000


def bare_model_name(model_id: str) -> str:
    """``"anthropic:claude-opus-5-5"`` -> ``"claude-opus-5-5"`` (pricing key)."""
    return model_id.split(":", 1)[1] if ":" in model_id else model_id


def model_fingerprint(model_id: str) -> dict[str, Any] | None:
    """Exact local weights behind ``model_id``, for the run log.

    WHY: an Ollama tag such as ``qwen3:4b`` is mutable (it was re-pointed to
    a different 2507 build upstream), so the tag alone does not identify the
    model a thesis result came from; the content digest does. Hosted
    providers expose no digest, so they return ``None`` and the logged model
    id is all there is. A local server that cannot be reached is recorded as
    an error rather than raised, because the model call itself will fail
    loudly a moment later.
    """
    provider, _, name = model_id.partition(":")
    if provider != "ollama":
        return None
    try:
        import ollama

        client = ollama.Client()
        listed = {m.model: m for m in client.list().models}
        entry = listed.get(name) or listed.get(f"{name}:latest")
        if entry is None:
            return {"name": name, "error": "model not pulled on the local Ollama server"}
        shown = client.show(name)
        details = entry.details
        return {
            "name": entry.model,
            "digest": entry.digest,
            "size_bytes": entry.size,
            "family": details.family if details else None,
            "parameter_size": details.parameter_size if details else None,
            "quantization": details.quantization_level if details else None,
            "modified_at": str(shown.modified_at),
            "server_version": _ollama_server_version(),
        }
    except Exception as exc:  # unreachable server, client/API mismatch
        return {"name": name, "error": repr(exc)}


def _ollama_server_version() -> str | None:
    """``GET /api/version``; the Python client has no public call for it."""
    import os
    import urllib.request

    host = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
    if "://" not in host:
        host = f"http://{host}"
    try:
        with urllib.request.urlopen(f"{host}/api/version", timeout=5) as resp:
            return json.loads(resp.read()).get("version")
    except Exception:
        return None


def _text_of(content: Any) -> str:
    """The visible text of a LangChain message content (str or block list)."""
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif isinstance(block, str):
            parts.append(block)
    return "".join(parts)


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return json.loads(json.dumps(value, default=str))


class LangChainLLM:
    """``init_chat_model`` + native structured output. Created lazily."""

    def __init__(self, model_id: str, settings: ModelSettings) -> None:
        """Store config; the provider client is created on first call."""
        self.model_id = model_id
        self._settings = settings
        self._chat: Any = None

    def _model(self) -> Any:
        if self._chat is None:
            from langchain.chat_models import init_chat_model

            self._chat = init_chat_model(self.model_id, **self._settings.kwargs_for(self.model_id))
        return self._chat

    def invoke(self, turns: list[ChatTurn], schema: type[BaseModel]) -> LLMResponse:
        """Call the model; parse errors are returned, transport errors raise."""
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        cls = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}
        messages = [cls[t.role](content=t.content) for t in turns]
        runnable = self._model().with_structured_output(
            schema, method=self._settings.structured_output_method, include_raw=True
        )
        result = runnable.invoke(messages)
        raw = result["raw"]
        meta = getattr(raw, "response_metadata", {}) or {}
        um = getattr(raw, "usage_metadata", None) or {}
        details = um.get("input_token_details") or {}
        parsed = result.get("parsed")
        error = result.get("parsing_error")
        stop = meta.get("stop_reason")
        if parsed is None and error is None:
            error = f"model returned no parseable output (stop_reason={stop})"
        return LLMResponse(
            parsed=parsed.model_dump() if isinstance(parsed, BaseModel) else parsed,
            parse_error=None if error is None else str(error),
            raw_text=_text_of(raw.content),
            raw_content=_jsonable(raw.content),
            stop_reason=stop,
            served_model=meta.get("model") or meta.get("model_name"),
            usage=TokenUsage(
                input_tokens=um.get("input_tokens"),
                output_tokens=um.get("output_tokens"),
                cache_read_tokens=details.get("cache_read"),
                cache_creation_tokens=details.get("cache_creation"),
            ),
        )
