"""A provider that plays back a fixed script, so the agent loop can be tested offline."""

from __future__ import annotations

from typing import Any

from support_agent.providers import ToolCall, ToolResult, ToolSpec, Turn


class ScriptedProvider:
    name = "scripted"

    def __init__(self, script: list[list[tuple[str, dict[str, Any]]]]) -> None:
        self.script = list(script)
        self.seen: list[str] = []  # every text the "model" received
        self.tool_results: list[ToolResult] = []

    def start(self, system: str, user: str, tools: list[ToolSpec]) -> None:
        self.tools = tools
        self.system = system
        self.seen.append(user)

    def step(self) -> Turn:
        calls = self.script.pop(0) if self.script else []
        return Turn(text="", tool_calls=[ToolCall(f"c{i}", n, a) for i, (n, a) in enumerate(calls)])

    def add_tool_results(self, results: list[ToolResult]) -> None:
        self.tool_results.extend(results)
        self.seen.extend(r.content for r in results)

    def add_user_message(self, text: str) -> None:
        self.seen.append(text)
