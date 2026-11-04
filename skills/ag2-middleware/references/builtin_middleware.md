# Built-in middleware reference (AG2 1.3.0)

Everything below imports from `ag2.middleware` (`TelemetryMiddleware` / `MetricsMiddleware` need the `tracing` / `metrics` extras; without them the names exist but raise a missing-dependency error when used).

Built-ins are **factory instances** — pass `LoggingMiddleware()`, not the class. Your own `BaseMiddleware` subclasses are passed as the class or `Middleware(Cls, **kw)`.

## `LoggingMiddleware(logger: logging.Logger | None = None)`

Logs turn start/end, each LLM call and its duration, each tool execution and result. Default logger `"ag2"`. Quick debugging; use `TelemetryMiddleware` for production traces.

## `RetryMiddleware(max_retries: int = 3, retry_on: tuple[type[Exception], ...] = (Exception,))`

Retries failed LLM calls. A call that already published something to the stream (a chunk, reasoning, a server-side tool call) is **not** retried — the exception propagates. Connection errors, rate limits and 5xx before any output retry normally.

```python
import httpx
RetryMiddleware(max_retries=3, retry_on=(httpx.HTTPError, TimeoutError))
```

## `HistoryLimiter(max_events: int)`

Count-based trim before each model call. Keeps the first `ModelRequest` when possible and drops anything the cut orphaned (e.g. tool results without their call), so `max_events` is a target, not a hard cap.

## `TokenLimiter(max_tokens: int, chars_per_token: int = 4)`

Character-estimate token budget trim. Safety net, not a meter. Also a target, not a hard cap.

## `MetricsMiddleware(registry: CollectorRegistry)` — `pip install "ag2[metrics]"`

Prometheus counters/histograms: `ag2_agent_turns_total`, `ag2_llm_calls_total`, `ag2_llm_tokens_total`, `ag2_tool_calls_total`, `ag2_human_input_requests_total`, `ag2_*_duration_seconds`. Create **one per `CollectorRegistry`** and reuse it across agents (a second one on the same registry raises `ValueError`).

```python
from prometheus_client import CollectorRegistry, start_http_server
from ag2.middleware import MetricsMiddleware

registry = CollectorRegistry()
metrics = MetricsMiddleware(registry=registry)
start_http_server(8000, registry=registry)
agent = Agent("assistant", config=config, middleware=[metrics])
```

## `TelemetryMiddleware(...)` — `pip install "ag2[tracing]"`

OpenTelemetry GenAI spans. See `ag2-telemetry`.

## `ConditionalMiddleware(middleware, condition)`

Gate any factory so each hook only runs when its own event matches. `on_llm_call` checks the turn's initial event.

```python
from ag2.events import ToolCallEvent
from ag2.middleware import ConditionalMiddleware, Middleware

ConditionalMiddleware(Middleware(AuditMiddleware, logger=log),
                      condition=(ToolCallEvent.name == "search") | (ToolCallEvent.name == "browse"))
```

Conditions compose with `&`, `|`, `~`; a bare event class is accepted.

## `statusbar_middleware(func=None, *, persist=False)`

Decorator/factory. Calls `func` (sync or async, DI: `Context`, `Variable`, `Inject`, `Depends`) before **every** model call — including after tool rounds — and appends the returned `Input` / `list[Input]` to that request. `None` or `[]` skips. Not stored in history unless `persist=True` (then a `StatusObservation` is published). Typical: remaining attempts, plan progress, recovery hints computed from `context.stream.history.where(...)`. Upstream guide: `website/docs/user-guide/status_bars.mdx`.

## `HaltCheckMiddleware` + `AssemblerMiddleware`

`Middleware(AssemblerMiddleware, policies=[...])` (from `ag2.assembly`) runs assembly policies before each model call. `Middleware(HaltCheckMiddleware)` placed right after it short-circuits model calls with `HALTED: <reason>` once a `HaltEvent` appeared in the current turn. This replaces the deprecated `Agent(assembly=[...])` (which auto-wired the halt check). Policies: `ag2-knowledge-and-memory`; alerts/halts: `ag2-observers-and-alerts`.

## Describing middleware (`describe()`, `DescribableMiddleware`)

```python
from ag2.middleware import DescribableMiddleware, MiddlewareDescription, TokenLimiter

TokenLimiter(max_tokens=100).describe()
# MiddlewareDescription(kind='TokenLimiter', config={'max_tokens': 100, 'chars_per_token': 4}, complete=True)

class RateLimit:                       # opt in by implementing describe()
    def __init__(self, per_minute: int) -> None:
        self._per_minute = per_minute
    async def __call__(self, call_next, event, context):
        return await call_next(event, context)
    def describe(self) -> MiddlewareDescription:
        return MiddlewareDescription(kind=type(self).__qualname__, config={"per_minute": self._per_minute})

isinstance(RateLimit(10), DescribableMiddleware)   # True (runtime-checkable Protocol)
```

- `agent.middleware` / `tool.middleware` → entries with `.middleware` and `.description`.
- `Middleware(Cls, **kw).describe()` reports option **names** only and `complete=False` (values may be secrets); read `.cls` / `.options` for values.
- Keep `config` to settings — no counters (flaky snapshots), no credentials.

## Bracketing the whole execution (`ScopedMiddlewareFactory`)

A factory with `scope(event, context) -> AsyncContextManager` enters before tool-schema resolution and exits after observers complete. `Middleware(...)` does not forward `scope`; write the factory class yourself:

```python
from contextlib import asynccontextmanager
from ag2.middleware import BaseMiddleware

class AuditScope:
    def __call__(self, event, context) -> BaseMiddleware:
        return BaseMiddleware(event, context)

    @asynccontextmanager
    async def scope(self, event, context):
        print("before tool resolution")
        yield
        print("after observers completed")
```

## Typical stack

```python
agent = Agent(
    "assistant",
    config=config,
    middleware=[
        TelemetryMiddleware(tracer_provider=tp, agent_name="assistant"),  # outermost: sees retries as separate spans
        LoggingMiddleware(),
        status,                                                           # statusbar before retry: one snapshot per call
        RetryMiddleware(max_retries=2),
        HistoryLimiter(max_events=200),                                   # closest to the model
    ],
)
```

| Want to | Reach for |
|---|---|
| See what's happening | `LoggingMiddleware` |
| Survive flaky providers | `RetryMiddleware` |
| Dashboards / alerts on rates & latency | `MetricsMiddleware` |
| Request-level traces | `TelemetryMiddleware` |
| Cap history | `HistoryLimiter` / `TokenLimiter`, or assembly + compaction (`ag2-knowledge-and-memory`) |
| Keep live facts in front of the model | `statusbar_middleware` |
| Approve one tool call | `approval_required()` (`ag2-hitl`) |
