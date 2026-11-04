# Knowledge & memory — details (AG2 1.3)

## Full wiring

```python
from ag2 import Agent
from ag2.aggregate import AggregateTrigger, ConversationSummaryAggregate, WorkingMemoryAggregate
from ag2.assembly import AssemblerMiddleware
from ag2.compact import CompactTrigger, SummarizeCompact
from ag2.config import OpenAIResponsesConfig
from ag2.knowledge import KnowledgePlugin, SqliteKnowledgeStore
from ag2.middleware import HaltCheckMiddleware, Middleware
from ag2.policies import (
    AlertPolicy, ConversationPolicy, EpisodicMemoryPolicy,
    SlidingWindowPolicy, WorkingMemoryPolicy,
)

main = OpenAIResponsesConfig(model="gpt-4.1")
cheap = OpenAIResponsesConfig(model="gpt-4.1-mini")

agent = Agent(
    "assistant",
    config=main,
    plugins=[KnowledgePlugin(
        SqliteKnowledgeStore("./state.db"),
        compact=SummarizeCompact(target=40, config=cheap),
        compact_trigger=CompactTrigger(max_events=120),
        aggregate=ConversationSummaryAggregate(config=cheap),
        aggregate_trigger=AggregateTrigger(every_n_turns=10, on_end=True),
    )],
    middleware=[
        Middleware(AssemblerMiddleware, policies=[
            WorkingMemoryPolicy(),
            EpisodicMemoryPolicy(max_episodes=3),
            AlertPolicy(),                       # one instance per Agent (dedup state)
            SlidingWindowPolicy(max_events=80),
        ]),
        Middleware(HaltCheckMiddleware),         # right after the assembler when using AlertPolicy
    ],
)
```

Pass middleware **classes** (or `Middleware(Cls, **kwargs)`), never instances. Plugin-contributed middleware (aggregation/compaction) follows your explicit `middleware=` list.

## Aggregation (`ag2.aggregate`)

`AggregateStrategy.aggregate(events, ctx, store) -> None`.

| Strategy | Writes | Read by |
|---|---|---|
| `WorkingMemoryAggregate(config, *, prompt=...)` | `/memory/working.md` (overwritten each fire) | `WorkingMemoryPolicy` |
| `ConversationSummaryAggregate(config)` | `/memory/conversations/{ts}_{stream_id}.md` | `EpisodicMemoryPolicy` |

- `prompt=` overrides the merge template; placeholders `{existing}` and `{events}`. The default keeps *topical facts*; override it to track e.g. tactics.
- `AggregateTrigger(every_n_turns=0, every_n_events=0, on_end=False)` — all off by default; each fire is one LLM call.
- Path constants: `WORKING_MEMORY_PATH`, `CONVERSATIONS_PREFIX`, `LOG_PREFIX` in `ag2.knowledge`.

## Compaction (`ag2.compact`)

`CompactStrategy.compact(events, ctx, store) -> list[BaseEvent]` replaces stream history.

| Strategy | Behaviour |
|---|---|
| `TailWindowCompact(target)` | Keep last ~`target` events (zero LLM calls) |
| `SummarizeCompact(target, config)` | Summarise dropped events into a `CompactionSummary` (from `ag2.compact`) at the head; `strategy.last_usage` has token usage |

Both persist dropped events to `/log/{stream_id}.dropped-N.jsonl`. `CompactTrigger(max_events=0, max_tokens=0, chars_per_token=4)` fires when any threshold is crossed. Boundaries snap to whole turns (a tool call is never split from its result), so retained count can be slightly below `target`. `ConversationPolicy` lets `CompactionSummary` through.

Assembly policies change only the LLM's *view*; compaction changes the *stream*. `HistoryLimiter`/`TokenLimiter` middleware also trim the view — pick one mechanism.

## Custom policy

```python
class RunbookPolicy:
    name = "runbook"
    def __init__(self, text: str) -> None:
        self._text = text
    async def apply(self, prompts, events, context):
        return prompts + [f"## Runbook\n{self._text}"], events
```

Policies must be side-effect free (no stream events). Memory policies find the store via `context.dependencies.get(KnowledgeStore)` and are no-ops without one. `Plugin.add_policy(...)` also contributes policies; they join the first registered `AssemblerMiddleware`.

## Lifecycle events (`ag2.events`)

`AggregationStarted` → `AggregationCompleted(strategy, usage)` | `AggregationFailed`; `CompactionStarted` → `CompactionCompleted(strategy, events_before, events_after, usage)` | `CompactionFailed`; `EventLogFailed` if the `/log/` dump raised (the reply still returns); `HaltEvent` when `AlertPolicy` sees a FATAL alert. Subscribe: `stream.where(CompactionCompleted).subscribe(cb)` with `agent.ask(..., stream=stream)`.

## EventLogWriter

```python
from ag2.knowledge import EventLogWriter
writer = EventLogWriter(store)
await writer.persist(stream.id, list(await stream.history.get_events()))
events = await writer.load(stream.id)     # unknown types come back as UnknownEvent
```

`KnowledgePlugin` does this automatically after each `ask()` unless `write_event_log=False`.

## Pitfalls

- **Reduction before injection** — reducers can't budget for injected context. Validate with `AssemblerMiddleware.validate_order(policies)`.
- **`AlertPolicy` without `HaltCheckMiddleware`** — FATAL alerts are injected but the model call isn't stopped.
- **`DiskKnowledgeStore` without watchdog** — `ImportError` at construction; install `watchdog>=4.0,<7` or use `SqliteKnowledgeStore`.
- **Root vs in-store path** — `DiskKnowledgeStore("./memory")` puts working memory at `./memory/memory/working.md`; name the root `./agent-state`.
- **`on_end` fires per `ask()` call** — every `reply.ask(...)` in a multi-turn chain is its own execution scope, so `ConversationSummaryAggregate(on_end=True)` writes one summary file per call. Use `every_n_turns=N` or aggregate once at session end if that's too many.
- **`read_range` uses byte offsets**, not characters.
- **Workers don't inherit the parent's knowledge tool** — give a delegated worker its own `KnowledgePlugin` (a per-request `ask(dependencies={KnowledgeStore: s})` does reach workers).
- **Approval** — `approval_required()` middleware gates the `knowledge` tool, not automatic bootstrap/aggregation/compaction/log writes.
