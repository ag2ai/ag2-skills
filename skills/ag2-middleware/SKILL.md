---
name: ag2-middleware
description: "Intercept the AG2 agent loop with `BaseMiddleware` hooks — `on_turn`, `on_llm_call`, `on_tool_execution`, `on_human_input` — registered via `Agent(middleware=[...])` or `ask(middleware=[...])`. Custom subclasses are passed as the CLASS or `Middleware(Cls, **kwargs)`, never an instance. Covers getting per-turn state out (shared object via kwargs, `reply.context.variables`), ordering, `ConditionalMiddleware`, `statusbar_middleware` (fresh status each model call), `describe()` / `DescribableMiddleware`, `ScopedMiddlewareFactory`, and assembly policies via `Middleware(AssemblerMiddleware, policies=[...])` + `HaltCheckMiddleware`. Built-ins: `LoggingMiddleware`, `RetryMiddleware`, `HistoryLimiter`, `TokenLimiter`, `MetricsMiddleware`, `TelemetryMiddleware` (see `ag2-telemetry`), `approval_required` (see `ag2-hitl`). Use for retry, logging, timing, history trimming, request mutation, tool auditing, guardrails, rate limiting."
license: Apache-2.0
---

# Middleware

## When to use

Cross-cutting behaviour that should apply to every turn without changing the agent, model client, or tools: logging/timing, retries, history trimming, request mutation, tool auditing/guardrails, rate limiting, HITL auditing.

## Four hooks

| Hook | Wraps | Returns |
|---|---|---|
| `on_turn(call_next, event, context)` | the whole `ask()` turn | `ModelResponse` |
| `on_llm_call(call_next, events, context)` | each model call (history in `events`) | `ModelResponse` |
| `on_tool_execution(call_next, event, context)` | each tool call (`ToolCallEvent`) | `ToolResultType` |
| `on_human_input(call_next, event, context)` | each `context.input()` / approval | `HumanMessage` |

A **new instance is created per turn** (`__init__(self, event, context, **your_kwargs)`), so `self` state dies with the turn.

## Registration — the rule that trips people up

`middleware=[...]` takes **factories**: callables `(event, context) -> BaseMiddleware`.

| What you have | Pass it as |
|---|---|
| Built-in (`LoggingMiddleware`, `RetryMiddleware`, `TokenLimiter`, ...) | an instance: `RetryMiddleware(max_retries=2)` (built-ins are factories) |
| Your `BaseMiddleware` subclass, no extra args | the **class**: `middleware=[MyMiddleware]` |
| Your subclass with extra `__init__` args | `Middleware(MyMiddleware, key=value)` |
| `MyMiddleware()` / `MyMiddleware(event, ctx)` | **wrong** → `TypeError: 'MyMiddleware' object is not callable` |

Call-level middleware (`agent.ask(..., middleware=[...])`, `reply.ask(...)`) is appended **after** the agent's list.

## Recipe — custom middleware + getting state out

```python
import time
from collections.abc import Sequence

from ag2 import Agent, Context, tool
from ag2.config import OpenAIResponsesConfig
from ag2.events import BaseEvent, ModelResponse
from ag2.middleware import BaseMiddleware, LLMCall, Middleware, RetryMiddleware

class CallTimer(BaseMiddleware):
    def __init__(self, event: BaseEvent, context: Context, stats: list[float]) -> None:
        super().__init__(event, context)
        self.stats = stats                      # shared object outlives the per-turn instance

    async def on_llm_call(self, call_next: LLMCall, events: Sequence[BaseEvent], context: Context) -> ModelResponse:
        start = time.perf_counter()
        try:
            return await call_next(events, context)
        finally:
            self.stats.append(time.perf_counter() - start)

class CountCalls(BaseMiddleware):               # no extra args -> pass the class
    async def on_llm_call(self, call_next, events, context):
        context.variables["llm_calls"] = context.variables.get("llm_calls", 0) + 1
        return await call_next(events, context)

@tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b

durations: list[float] = []
agent = Agent(
    "calc",
    config=OpenAIResponsesConfig(model="gpt-4.1-mini"),
    tools=[add],
    middleware=[Middleware(CallTimer, stats=durations), CountCalls, RetryMiddleware(max_retries=2)],
)
reply = await agent.ask("What is 17+25? Use the tool.")
print(len(durations), reply.context.variables["llm_calls"])   # 2 2  (tool round-trip)
```

Two ways out of a per-turn instance: pass a mutable object via `Middleware(Cls, obj=...)`, or write to `context.variables` and read **`reply.context.variables`** (the dict you passed to `ask(variables=...)` is copied, not mutated). For a reusable component with its own state, write a factory class (`__call__(self, event, context) -> BaseMiddleware`) like the built-ins do.

## Ordering

`[A, B, C]` enters A → B → C and unwinds C → B → A (A is outermost). Put retry *outside* anything whose work should be retried; put history trimming closest to the model; put `statusbar_middleware` **before** `RetryMiddleware` to reuse one snapshot across attempts.

## Built-ins and helpers (all from `ag2.middleware`)

| Name | Use |
|---|---|
| `LoggingMiddleware(logger=None)` | log turns, LLM calls, tools |
| `RetryMiddleware(max_retries=3, retry_on=(Exception,))` | retry LLM calls (not after a chunk/tool call was already published) |
| `HistoryLimiter(max_events=N)` / `TokenLimiter(max_tokens=N, chars_per_token=4)` | trim history before each call (targets, not hard caps) |
| `MetricsMiddleware(registry=CollectorRegistry())` | Prometheus metrics, `ag2[metrics]`; one per registry |
| `TelemetryMiddleware(...)` | OpenTelemetry spans — `ag2-telemetry` |
| `ConditionalMiddleware(factory, condition=ToolCallEvent.name == "x")` | gate any middleware by event condition |
| `statusbar_middleware` | append fresh `Input` parts to every model request |
| `HaltCheckMiddleware` | stop model calls after a `HaltEvent` (with `AlertPolicy`) |
| `approval_required()` | **tool** middleware for HITL — `ag2-hitl` |

### Status bar (1.3.0)

```python
from typing import Annotated
from ag2 import DataInput, Variable
from ag2.middleware import statusbar_middleware

@statusbar_middleware
def budget(calls: Annotated[int, Variable("llm_calls", default=0)]) -> DataInput | None:
    return DataInput({"llm_calls_so_far": calls, "limit": 5})

agent = Agent(..., middleware=[budget])   # the decorated function is already a factory
```

Callback (sync/async, DI-enabled) returns `Input`, a list of `Input`, or `None`; parts are not stored in history unless `persist=True`.

### Assembly policies (replaces deprecated `Agent(assembly=[...])`)

```python
from ag2.assembly import AssemblerMiddleware
from ag2.middleware import HaltCheckMiddleware, Middleware
from ag2.policies import AlertPolicy, SlidingWindowPolicy

middleware = [
    Middleware(AssemblerMiddleware, policies=[AlertPolicy(), SlidingWindowPolicy(max_events=50)]),
    Middleware(HaltCheckMiddleware),           # right after, so FATAL alerts stop the call
]
```

## Going deeper

- `references/builtin_middleware.md` — built-in parameters, `describe()` / `DescribableMiddleware`, `ScopedMiddlewareFactory`, stacking order.
- Upstream: `website/docs/user-guide/middleware.mdx`, `status_bars.mdx`, `advanced/assembly.mdx`, `tools/tool_middleware.mdx`, `metrics.mdx`.
- One-tool behaviour → tool middleware (`@tool(middleware=[fn])`, plain `async def fn(call_next, event, context)`) — `ag2-add-custom-tool`. Agent middleware runs outside tool middleware.

## Common pitfalls

- **Passing an instance of your subclass** — `TypeError: '...' object is not callable`. Pass the class or `Middleware(Cls, **kw)`.
- **Passing the class when `__init__` needs kwargs** — `TypeError: ... missing 1 required positional argument`. Wrap with `Middleware(...)`.
- **Storing results on `self`** — gone after the turn; use a shared object or `context.variables`.
- **Per-call middleware replacing agent middleware** — it is appended; both run.
- **Using `assembly=`** — deprecated (removed 1.4.0); use `AssemblerMiddleware` as above.
- **Branching on `event.name` in `on_tool_execution` for one tool** — use tool middleware or `ConditionalMiddleware`.
