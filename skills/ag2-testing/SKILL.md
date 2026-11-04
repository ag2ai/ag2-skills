---
name: ag2-testing
description: Test AG2 agents and tools without hitting a real LLM provider. Pass `TestConfig(...)` from `ag2.testing` as the agent's config (or per-`ask`) to script LLM turns — text, `ToolCallEvent(name=..., arguments=<JSON string>)`, parallel tool calls, provider exceptions — and wrap it in `TrackingConfig` to assert exactly what the model received (`config.calls[i].messages / prompt / tools / variables`). Covers pytest-asyncio setup, capturing `ToolCallEvent` / `ToolResultEvent` / `ToolErrorEvent` on a `MemoryStream`, `raise_tool_errors=False`, `ToolNotFoundError`, `Depends` / `Inject` overrides, and the multi-turn cursor rules. Use when the user is writing pytest tests for an Agent, Tool, middleware or response schema. For writing tools see `ag2-add-custom-tool`; for LLM-graded evals see `ag2-evaluation`.
license: Apache-2.0
metadata:
  ag2_version: "1.3.0"
---

# Testing agents and tools

## When to use

Writing tests for code that builds AG2 `Agent`s, `@tool` functions, middleware or response schemas — anywhere you don't want real LLM calls.

## Setup — pytest-asyncio

```bash
pip install ag2 pytest pytest-asyncio
```

```toml
# pyproject.toml — no @pytest.mark.asyncio needed on each test
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```

(Without `asyncio_mode = "auto"`, decorate each test with `@pytest.mark.asyncio`. Without `asyncio_default_fixture_loop_scope`, pytest-asyncio prints a harmless `PytestDeprecationWarning`.) `TestConfig` needs no provider extra and no API key.

## 60-second recipe — mock a reply

```python
from ag2 import Agent
from ag2.testing import TestConfig

async def test_mocked_response():
    agent = Agent("test_agent")
    reply = await agent.ask("Hi!", config=TestConfig("This is a mocked response."))
    assert reply.body == "This is a mocked response."
```

Each positional arg of `TestConfig(*turns)` is the next LLM call's result:

| Turn | Effect |
|---|---|
| `"text"` | model replies with text |
| `ToolCallEvent(name="t", arguments='{"x": 1}')` | model calls a tool — `arguments` is a **JSON string** (default `"{}"`) |
| `[ToolCallEvent(...), ToolCallEvent(...)]` | parallel tool calls in one turn |
| `TimeoutError("dropped")` (any exception) | the provider call raises it |
| `ModelResponse(...)` | fully spelled-out response |

## Tool call with arguments + asserting events

```python
import json
from ag2 import Agent, MemoryStream, tool
from ag2.events import ToolCallEvent, ToolResultEvent
from ag2.testing import TestConfig

@tool
def get_weather(city: str, units: str = "c") -> str:
    """Weather for a city."""
    return f"Sunny in {city} ({units})"

async def test_tool_call():
    stream = MemoryStream()
    calls: list[ToolCallEvent] = []
    results: list[ToolResultEvent] = []
    stream.where(ToolCallEvent).subscribe(lambda e: calls.append(e))
    stream.where(ToolResultEvent).subscribe(lambda e: results.append(e))

    agent = Agent("a", tools=[get_weather])
    config = TestConfig(
        ToolCallEvent(name="get_weather", arguments=json.dumps({"city": "Paris"})),
        "It is sunny in Paris.",            # LLM call after the tool result
    )
    reply = await agent.ask("Weather?", config=config, stream=stream)

    assert reply.body == "It is sunny in Paris."
    assert calls[0].serialized_arguments == {"city": "Paris"}   # parsed dict
    assert results[0].result.parts[0].content == "Sunny in Paris (c)"
```

`ToolErrorEvent` (a `ToolResultEvent` subclass with `.error`) is published when a tool raises.

## Inspect what the model received — `TrackingConfig`

```python
from ag2 import Agent, TextInput
from ag2.events import ModelMessage, ModelRequest, ModelResponse
from ag2.testing import TestConfig, TrackingConfig

async def test_model_inputs():
    config = TrackingConfig(TestConfig("Hello!", "Goodbye!"))
    agent = Agent("assistant", prompt="Be nice.", config=config, variables={"v": 1})
    reply = await agent.ask("Hi")
    await reply.ask("Bye")

    assert config.calls[0].prompt == ("Be nice.",)
    assert config.calls[0].variables == {"v": 1}
    assert [t.function.name for t in config.calls[0].tools] == []
    assert config.calls[1].messages == (
        ModelRequest([TextInput("Hi")]),
        ModelResponse(ModelMessage("Hello!")),
        ModelRequest([TextInput("Bye")]),
    )
    config.mock.assert_called_with(ModelRequest([TextInput("Bye")]))  # last message per call
```

One `ModelCall` per attempted LLM call (including ones that raise).

## Error paths

```python
import pytest
from ag2.exceptions import ToolNotFoundError

def failing_tool() -> str:
    raise ValueError("Something went wrong")

async def test_tool_raises():          # default: TestConfig re-raises tool errors
    agent = Agent("t", tools=[failing_tool],
                  config=TestConfig(ToolCallEvent(name="failing_tool"), "result"))
    with pytest.raises(ValueError, match="Something went wrong"):
        await agent.ask("Hi!")

async def test_tool_error_reaches_model():   # behave like a real provider
    agent = Agent("t", tools=[failing_tool], config=TestConfig(
        ToolCallEvent(name="failing_tool"), "Sorry, that failed.", raise_tool_errors=False))
    assert (await agent.ask("Hi!")).body == "Sorry, that failed."

async def test_tool_not_found():
    agent = Agent("t", config=TestConfig(ToolCallEvent(name="unregistered_tool")))
    with pytest.raises(ToolNotFoundError, match="Tool `unregistered_tool` not found"):
        await agent.ask("Hi!")
```

The re-raise is the **test double's** default (`raise_tool_errors=True`); real providers hand the error to the model as a tool result. Use `raise_tool_errors=False` when asserting real agent behaviour.

## Overriding dependencies

```python
agent.dependency_provider.override(get_production_db, lambda: "mock_db")    # Depends(...)
await agent.ask("Read", dependencies={"pool": fake_pool}, variables={"user": "u1"})  # Inject / Variable
```

## Multi-turn cursor rules

- A **fresh `agent.ask()`** builds a new client — the script restarts at the first turn.
- **`reply.ask()`** continues the same client — the script keeps advancing (`TestConfig("one", "two")`: `agent.ask` → "one", `reply.ask` → "two").
- Running out of turns raises `RuntimeError: coroutine raised StopIteration` — count every LLM call, including the one after each tool result.
- For per-turn control pass `config=TestConfig(...)` to each `ask`, or make a tool stateful when something else drives the loop (network / workflow).

## Common pitfalls

- **`arguments` as a dict** — it's a JSON string; use `json.dumps(...)`. Read parsed args via `.serialized_arguments`.
- **Asserting `reply.body` with a `response_schema`** — use `await reply.content()` (`TestConfig('{"n": 3}')` validates fine).
- **Mocking the LLM but not the tool** — the tool still runs; override its `Depends` / pass fake `dependencies`.
- **Sharing agents between tests** — construct fresh agents per test.
- **Relying on provider keys in CI** — always pass `TestConfig`.

Upstream: `website/docs/user-guide/testing.mdx`, `advanced/stream.mdx`.
