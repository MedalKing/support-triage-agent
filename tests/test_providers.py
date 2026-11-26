import json
from types import SimpleNamespace

import httpx

from support_agent.providers import ClaudeProvider, OllamaProvider, ToolCall, ToolResult, ToolSpec

TOOLS = [ToolSpec("get_order", "Get an order", {"type": "object", "properties": {"order_id": {"type": "string"}}})]


class FakeMessages:
    def __init__(self, response):
        self.response, self.calls = response, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_claude_provider_maps_tool_use_and_keeps_full_content():
    blocks = [SimpleNamespace(type="text", text="Looking it up."),
              SimpleNamespace(type="tool_use", id="tu_1", name="get_order", input={"order_id": "SO-5001"})]
    usage = SimpleNamespace(input_tokens=40, output_tokens=12, cache_creation_input_tokens=900,
                            cache_read_input_tokens=0)
    messages = FakeMessages(SimpleNamespace(stop_reason="tool_use", content=blocks, usage=usage,
                                            model="claude-opus-5-5"))
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    p = ClaudeProvider(model="claude-opus-5-5", client=client)
    p.start("sys", "ticket", TOOLS)
    turn = p.step()
    assert turn.text == "Looking it up."
    assert turn.tool_calls[0].name == "get_order" and turn.tool_calls[0].arguments == {"order_id": "SO-5001"}
    sent = messages.calls[0]
    assert sent["model"] == "claude-opus-5-5" and sent["fallbacks"] == "default"
    assert sent["cache_control"] == {"type": "ephemeral"}
    assert p.usage[0].cache_write_tokens == 900 and p.usage[0].output_tokens == 12
    assert p.messages[-1]["content"] is blocks
    p.add_tool_results([ToolResult(turn.tool_calls[0], "{}")])
    assert p.messages[-1]["content"][0]["tool_use_id"] == "tu_1"


def test_ollama_provider_speaks_native_chat_api():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "get_order", "arguments": {"order_id": "SO-5001"}}}]}})

    p = OllamaProvider(model="qwen3:8b", http=httpx.Client(transport=httpx.MockTransport(handler)))
    p.start("sys", "ticket", TOOLS)
    turn = p.step()
    assert seen[0]["tools"][0]["function"]["name"] == "get_order"
    assert seen[0]["messages"][0] == {"role": "system", "content": "sys"}
    assert turn.tool_calls[0].arguments == {"order_id": "SO-5001"}
    p.add_tool_results([ToolResult(turn.tool_calls[0], "{}")])
    assert p.messages[-1] == {"role": "tool", "tool_name": "get_order", "content": "{}"}


def test_prompt_caching_can_be_turned_off():
    usage = SimpleNamespace(input_tokens=1, output_tokens=1, cache_creation_input_tokens=None,
                            cache_read_input_tokens=None)
    messages = FakeMessages(SimpleNamespace(stop_reason="end_turn", content=[], usage=usage, model="claude-haiku-5-5"))
    p = ClaudeProvider(model="claude-haiku-5-5", client=SimpleNamespace(beta=SimpleNamespace(messages=messages)),
                       cache=False)
    p.start("sys", "ticket", TOOLS)
    p.step()
    assert "cache_control" not in messages.calls[0] and p.usage[0].cache_read_tokens == 0
