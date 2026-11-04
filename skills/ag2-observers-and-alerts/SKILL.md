---
name: ag2-observers-and-alerts
description: "Monitor an AG2 agent's stream — log events, detect repeated tool calls, track token spend (including sub-agents), build trigger-driven observers, route alerts to the model, and halt on FATAL alerts. Covers `@observer(...)` (stateless), `BaseObserver` (stateful, `process()`, `timeout=`), built-ins `TokenMonitor` and `LoopDetector`, `Watch` primitives (`EventWatch`, `CadenceWatch`, `DelayWatch`, `IntervalWatch`, `CronWatch`, `AllOf`, `AnyOf`, `Sequence`), `ObserverAlert` + `Severity` (INFO/WARNING/CRITICAL/FATAL), `AlertPolicy` wired via `Middleware(AssemblerMiddleware, policies=[...])` + `Middleware(HaltCheckMiddleware)`, `HaltEvent`, and escalating CRITICAL to a halt. Use for observability, runtime safety guards, budget alerts, batch/time-based reactive logic. Sub-agent event trees (`ChildEvent`): `ag2-subagent-delegation`; traces: `ag2-telemetry`; blocking a tool before it runs: `ag2-hitl` / `ag2-middleware`."
license: Apache-2.0
metadata:
  ag2_version: "1.3.0"
---

# Observers, watches, and alerts

## When to use

- **Observability** — log model responses, tool calls, token usage.
- **Runtime safety** — detect dangerous tool arguments and halt the agent's next model call.
- **Budgets / loops** — token thresholds, repeated identical tool calls.
- **Reactive metrics** — every Nth event, every T seconds, cron.

## Two observer shapes

| Shape | When | API |
|---|---|---|
| Stateless function | per-event hook (logging, metrics) | `@observer(EventType)` |
| Stateful class | counters, windows, thresholds, composed triggers | subclass `BaseObserver` |

```python
from ag2 import Agent, observer
from ag2.config import OpenAIResponsesConfig
from ag2.events import ModelResponse

@observer(ModelResponse)
async def log_response(event: ModelResponse) -> None:
    print("Model said:", event.content)

agent = Agent("assistant", config=OpenAIResponsesConfig(model="gpt-4.1-mini"), observers=[log_response])
```

Also `@agent.observer(...)` and per-call `agent.ask(..., observers=[...])`. Conditions: a type, `A | B`, a tuple `(A, B)`, field filters (`ToolCallEvent.name == "search"`), negation `~A`. Callbacks support DI (`Context`, `Variable`, `Inject`, `Depends`); options `interrupt=True`, `sync_to_thread=False`, `timeout=5.0`. A root agent's observers also receive descendants' events (as `ChildEvent`, or `UsageEvent` / task lifecycle as themselves) — see `ag2-subagent-delegation`.

## Built-in stateful observers

```python
from ag2.observers import LoopDetector, TokenMonitor

monitor = TokenMonitor(warn_threshold=50_000, alert_threshold=100_000)
agent = Agent("assistant", config=config, observers=[monitor, LoopDetector(window_size=10, repeat_threshold=3)])
...
print(monitor.total_tokens)   # cumulative across turns until monitor.reset()
```

- **`TokenMonitor`** — sums `UsageEvent`s (model calls, sub-agents, compaction). One `WARNING` at `warn_threshold`, one **`CRITICAL`** at `alert_threshold` (if both are crossed at once only CRITICAL is emitted).
- **`LoopDetector`** — `WARNING` when `repeat_threshold` consecutive identical calls (name + arguments) occur, tracked per worker.

## Custom `BaseObserver`

```python
from ag2 import Context
from ag2.events import BaseEvent, ModelResponse, ObserverAlert, Severity
from ag2.observers import BaseObserver
from ag2.watch import CadenceWatch

class AvgCompletion(BaseObserver):
    def __init__(self, window: int = 5) -> None:
        super().__init__("avg-completion", watch=CadenceWatch(n=window, condition=ModelResponse), timeout=5.0)

    async def process(self, events: list[BaseEvent], ctx: Context) -> ObserverAlert | None:
        tokens = [e.usage.completion_tokens for e in events if isinstance(e, ModelResponse) and e.usage]
        if not tokens:
            return None
        return ObserverAlert(source=self.name, severity=Severity.INFO, message=f"avg completion {sum(tokens) / len(tokens):.0f}")
```

A returned `ObserverAlert` is published on the stream; `await ctx.send(...)` emits anything else. Errors/timeouts in `process()` are logged, not raised.

## Watches — *when* to fire (`ag2.watch`)

| Need | Watch |
|---|---|
| every matching event | `EventWatch(EventType)` |
| every N / every T seconds / either | `CadenceWatch(n=N, max_wait=T, condition=EventType)` (needs `n` or `max_wait`) |
| once after delay / periodic / cron | `DelayWatch(s)` / `IntervalWatch(s)` / `CronWatch("0 9 * * MON")` |
| composition | `AllOf(...)`, `AnyOf(...)`, `Sequence(...)` |

Time-driven watches call `process()` with `events=[]`.

## Alerts → model, and what halts

`ObserverAlert` is on the stream but **not shown to the model** by default. Route alerts through `AlertPolicy` with the middleware form (the old `Agent(assembly=[AlertPolicy()])` is deprecated, removed in 1.4.0):

```python
from ag2.assembly import AssemblerMiddleware
from ag2.middleware import HaltCheckMiddleware, Middleware
from ag2.policies import AlertPolicy

agent = Agent(
    "guarded", config=config, observers=[...],
    middleware=[
        Middleware(AssemblerMiddleware, policies=[AlertPolicy()]),  # alerts -> prompt; FATAL -> HaltEvent
        Middleware(HaltCheckMiddleware),                           # HaltEvent -> stop model calls
    ],
)
```

| Severity | Effect with the wiring above |
|---|---|
| `INFO`, `WARNING`, `CRITICAL` | added to the system prompt once (dedup on source+severity+message). **No halt.** |
| `FATAL` | `HaltEvent` emitted; the next model call in **this turn** returns `HALTED: FATAL: <message>` instead of calling the provider |

Halting is turn-scoped and only stops *model calls*: a tool call already dispatched still runs, and the next `ask()` starts fresh. To block a tool before it runs, use tool middleware / `approval_required()` (`ag2-hitl`).

### Make CRITICAL (e.g. `TokenMonitor`) halt

Escalate it to FATAL with an observer — `AlertPolicy` then halts:

```python
from ag2 import Context, observer
from ag2.events import ObserverAlert, Severity

@observer(ObserverAlert)
async def escalate(event: ObserverAlert, context: Context) -> None:
    if event.severity == Severity.CRITICAL:
        await context.send(ObserverAlert(source="escalator", severity=Severity.FATAL, message=event.message))

agent = Agent(
    "writer", config=config,
    observers=[TokenMonitor(warn_threshold=30, alert_threshold=50), escalate],
    middleware=[Middleware(AssemblerMiddleware, policies=[AlertPolicy()]), Middleware(HaltCheckMiddleware)],
)
```

Usage is only known after a model call, so a single-call turn finishes before any halt can apply; the halt stops the next call (e.g. after a tool round).

## Going deeper

- `assets/token_watchdog.py` — Anthropic: `TokenMonitor` + `LoopDetector` + alert console + CRITICAL→FATAL escalation.
- `assets/safety_guard.py` — OpenAI: `PathGuardian` FATAL → `AlertPolicy` → `HaltEvent` → `HALTED:` reply.
- Upstream: `website/docs/user-guide/advanced/observers.mdx`, `watches.mdx`, `stream.mdx`, `assembly.mdx`.

## Common pitfalls

- **Expecting CRITICAL to halt** — only FATAL does; escalate as above.
- **No `HaltCheckMiddleware`** — `AlertPolicy` emits `HaltEvent` but nothing stops the model call.
- **`HaltCheckMiddleware` before `AssemblerMiddleware`** — put it right *after*, or the halt lands one call late.
- **Expecting an observer to block a tool** — observers react after the event; gate tools with tool middleware.
- **Sharing one `AlertPolicy()` / `TokenMonitor` across agents** — state lives on the instance.
- **Assuming `events` is non-empty** in time-driven watches.
- **`BaseObserver.process` not `async def`.**
