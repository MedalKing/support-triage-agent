"""Model providers behind one small interface, so the same agent loop runs on Claude or a
local model via Ollama.

Each provider keeps the conversation in its own wire format; the agent loop only sees
`Turn`, `ToolCall` and `ToolResult`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol

import anthropic
import httpx


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    strict: bool = False


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    call: ToolCall
    content: str
    is_error: bool = False


@dataclass
class Turn:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class ProviderError(RuntimeError):
    pass


class Provider(Protocol):
    name: str

    def start(self, system: str, user: str, tools: list[ToolSpec]) -> None: ...
    def step(self) -> Turn: ...
    def add_tool_results(self, results: list[ToolResult]) -> None: ...
    def add_user_message(self, text: str) -> None: ...


@lru_cache(maxsize=1)
def _shared_anthropic_client() -> anthropic.Anthropic:
    """One client per process. With workload identity federation the client swaps the
    single-use CI identity token for an access token once and reuses it, so a new client
    per ticket would try to swap the same token again and be refused."""
    return anthropic.Anthropic()


class ClaudeProvider:
    """Claude through the Messages API, with server-side refusal fallback enabled."""

    def __init__(self, model: str | None = None, effort: str | None = None,
                 client: anthropic.Anthropic | None = None) -> None:
        self.model = model or os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
        self.effort = effort or os.environ.get("CLAUDE_EFFORT", "medium")
        self.name = f"claude:{self.model}"
        self.client = client or _shared_anthropic_client()

    def start(self, system: str, user: str, tools: list[ToolSpec]) -> None:
        self.system = system
        self.tools = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema,
             **({"strict": True} if t.strict else {})}
            for t in tools
        ]
        self.messages: list[dict[str, Any]] = [{"role": "user", "content": user}]

    def step(self) -> Turn:
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=self.system,
                tools=self.tools,
                messages=self.messages,
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.APIStatusError as e:
            raise ProviderError(f"Claude API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise ProviderError(f"Could not reach the Claude API: {e}") from e

        if response.stop_reason == "refusal":
            raise ProviderError("The model declined this request.")
        if response.stop_reason == "max_tokens":
            raise ProviderError("The response hit max_tokens before finishing.")
        # Keep the full content (thinking and tool_use blocks) so the next request is valid.
        self.messages.append({"role": "assistant", "content": response.content})
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in response.content if b.type == "tool_use"]
        return Turn(text=text, tool_calls=calls)

    def add_tool_results(self, results: list[ToolResult]) -> None:
        # All results for one turn go back in a single user message.
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r.call.id, "content": r.content, "is_error": r.is_error}
            for r in results
        ]})

    def add_user_message(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})


@lru_cache(maxsize=1)
def _shared_http_client() -> httpx.Client:
    """One connection pool per process instead of one client per ticket."""
    return httpx.Client(timeout=300)


class OllamaProvider:
    """A local model through Ollama's native chat API (http://localhost:11434/api/chat)."""

    def __init__(self, model: str | None = None, host: str | None = None,
                 http: httpx.Client | None = None) -> None:
        self.model = model or os.environ.get("OLLAMA_MODEL", "qwen3:8b")
        self.host = (host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        self.name = f"ollama:{self.model}"
        self.http = http or _shared_http_client()
        self._next_id = 0

    def start(self, system: str, user: str, tools: list[ToolSpec]) -> None:
        self.tools = [
            {"type": "function", "function": {"name": t.name, "description": t.description,
                                              "parameters": t.input_schema}}
            for t in tools
        ]
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def step(self) -> Turn:
        try:
            r = self.http.post(f"{self.host}/api/chat", json={
                "model": self.model, "messages": self.messages, "tools": self.tools, "stream": False,
            })
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ProviderError(f"Ollama request failed: {e}") from e
        try:
            message = r.json()["message"]
            calls = [self._parse_call(tc) for tc in message.get("tool_calls") or []]
            text = message.get("content") or ""
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise ProviderError(f"Unexpected response from Ollama: {r.text[:200]}") from e
        self.messages.append(message)  # only a well-formed message goes into the history
        return Turn(text=text, tool_calls=calls)

    def _parse_call(self, tc: dict[str, Any]) -> ToolCall:
        fn = tc["function"]
        args = fn.get("arguments") or {}
        if isinstance(args, str):  # some models return a JSON string
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"_unparsed": args}
        if not isinstance(args, dict):
            args = {"_unparsed": args}
        self._next_id += 1
        return ToolCall(id=f"call_{self._next_id}", name=str(fn["name"]), arguments=args)

    def add_tool_results(self, results: list[ToolResult]) -> None:
        for r in results:
            self.messages.append({"role": "tool", "tool_name": r.call.name, "content": r.content})

    def add_user_message(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})


def make_provider(name: str | None = None) -> Provider:
    name = (name or os.environ.get("AGENT_PROVIDER", "claude")).lower()
    if name == "claude":
        return ClaudeProvider()
    if name == "ollama":
        return OllamaProvider()
    raise ValueError(f"unknown provider {name!r}; use 'auto', 'claude' or 'ollama'")
