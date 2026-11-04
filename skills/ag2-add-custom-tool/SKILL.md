---
name: ag2-add-custom-tool
description: Add a custom Python tool to an AG2 `Agent` using the `@tool` decorator. Use when the user wants to give an Agent a new capability backed by Python code (API calls, DB queries, computations, file ops) or needs tools to share state. Covers sync and async tools, `@tool(timeout=...)`, parameter typing, Pydantic `Field` schema customisation, returning typed `Input` / `ToolResult` (text / data / images / binary), `final=True` early-exit, dependency injection via `Context` / `Inject` / `Variable` / `Depends`, and conversation variables (`ask(variables=...)`, reading results back from `reply.context.variables`, `ScopedValue` / `Scope` / `ScopeError` for subagents). For built-in tools see `ag2-use-builtin-tools`; delegation `ag2-subagent-delegation`; approval gates `ag2-hitl`; testing `ag2-testing`.
license: Apache-2.0
metadata:
  ag2_version: "1.3.0"
---

# Add a custom Python tool

## When to use

The user wants their `Agent` to take a real-world action: hit an API, query a database, compute something, return an image — or tools must share state across calls. For shipped tools (web search, code exec, shell) see `ag2-use-builtin-tools` / `ag2-shell-tool`.

## 60-second recipe

```python
from ag2 import Agent, tool
from ag2.config import OpenAIResponsesConfig

@tool
def calculate_shipping_cost(destination: str, weight_kg: float) -> str:
    """Calculates shipping cost for a package to a destination."""
    return "$15.00"

agent = Agent(
    "shipping",
    prompt="Use tools when helpful.",
    config=OpenAIResponsesConfig(model="gpt-4.1-mini"),
    tools=[calculate_shipping_cost],
)
reply = await agent.ask("Cost to ship 2kg to Berlin?")
print(reply.body)
```

`@tool` builds the LLM-facing schema from the signature, type hints and docstring. **The docstring is the description the LLM sees.** Plain undecorated functions in `Agent(tools=[...])` are wrapped automatically, but **not** in per-call `agent.ask(..., tools=[...])` — there a plain function raises `AttributeError` in 1.3.0, so decorate it with `@tool` first; `@agent.tool` attaches to an existing agent. Tool names must be unique per turn — a later tool with the same name replaces the earlier one (with a warning).

## Sync, async, timeouts

Sync `def` tools run in a worker thread by default (`@tool(sync_to_thread=False)` only for cheap pure-Python work). `async def` tools run on the event loop.

```python
@tool(timeout=30.0)   # seconds; default None = wait forever
async def fetch_page(url: str) -> str:
    """Fetches the text of a web page."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        return (await client.get(url)).text
```

On expiry the model gets a `TimeoutError` tool result (`ToolErrorEvent`) and can continue. A timed-out *sync* tool's thread keeps running — also set timeouts on the underlying client.

## Schema control with Pydantic `Field`

```python
from typing import Annotated
from pydantic import Field

@tool(name="set_thermostat", description="Sets the thermostat.")
def set_temperature(
    temp: Annotated[int, Field(description="Target °C.", ge=10, le=30)],
    mode: Annotated[str, Field(pattern="^(heat|cool|auto)$")],
) -> str:
    return f"Set to {temp}°C in {mode} mode."
```

## Returning typed `Input` / `ToolResult`

A `str` return becomes `TextInput`. For richer payloads return an `Input` subtype or compose with `ToolResult`:

```python
from ag2 import DataInput, ImageInput, ToolResult, tool

@tool
def get_user_profile(user_id: str) -> DataInput:
    """Look up a user."""
    return DataInput({"id": user_id, "name": "Alice"})

@tool
def analyze_product(product_id: str) -> ToolResult:
    """Returns image + structured metadata in one tool call."""
    return ToolResult(ImageInput(url=f"https://cdn.example.com/{product_id}.jpg"), {"id": product_id, "stock": 42})
```

Raw bytes: `BinaryInput(data, media_type="application/pdf")`.

**End the turn early:** `return ToolResult("Ticket 42 was escalated.", final=True)` — the text becomes `reply.body` with no extra LLM round-trip. A final result must hold exactly one part (`TextInput` or `DataInput`).

## Dependency injection & shared state

Hidden from the LLM schema; full guide in `references/dependency_injection.md`.

| Annotation | Resolves from | Use for |
|---|---|---|
| `ctx: Context` | the current context | `.variables`, `.dependencies`, `.input()` |
| `Annotated[T, Inject("name")]` | `dependencies={...}` | clients, DB pools, locks, secrets (never serialized) |
| `Annotated[T, Variable("name", default=...)]` | `variables={...}` | serializable state shared between tools |
| `Annotated[T, Depends(fn)]` | calls `fn` per tool call (may `yield` for teardown) | sessions, auth checks |

```python
from ag2 import Context, Variable

@tool
def authenticate(context: Context) -> str:
    """Log in. Call before fetch_secure."""
    context.variables["auth_token"] = "abc-123"
    return "Authenticated"

@tool
def fetch_secure(auth_token: Annotated[str | None, Variable(default=None)]) -> str:
    """Fetch secure data."""
    return f"Data with token {auth_token}" if auth_token else "Not authenticated"

inputs = {"user": "u1"}
reply = await agent.ask("Get my data.", variables=inputs)
reply.context.variables["auth_token"]   # "abc-123"  <- read results HERE
inputs                                  # {"user": "u1"} — unchanged
```

**`ask(variables=...)` deep-copies the mapping** (merged over `Agent(variables=...)` defaults) — tool mutations never reach your original dict or the agent defaults. Read state back from `reply.context.variables`; `reply.ask(...)` continues with the same variables. For subagents (`Agent.as_tool()`), variables are copied into each child unless declared `ScopedValue(value, scope=Scope.SHARED)` at the root — see the reference.

## Going deeper

- `references/dependency_injection.md` — `Inject` / `Variable` / `Depends` details, `Scope` semantics, test overrides.
- Upstream: `website/docs/user-guide/tools/tools.mdx`, `context/variables.mdx`, `context/inject.mdx`, `depends.mdx`, `tools/toolkits.mdx`, `tools/tool_middleware.mdx`.

## Common pitfalls

- **Vague docstring / missing type hints** — the LLM won't know when or how to call the tool.
- **Expecting your `variables` dict to be mutated** — it's copied; use `reply.context.variables`.
- **Putting clients/locks in `variables`** — they're deep-copied (and synced over A2A/AG-UI). Use `dependencies` + `Inject`.
- **Blocking the loop** with `sync_to_thread=False` on slow code — keep the default.
- **Swallowing `asyncio.CancelledError`** in async tools — breaks `timeout=`.
- **`final=True` with multiple parts** — raises.
