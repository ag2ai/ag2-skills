---
name: ag2-knowledge-and-memory
description: "Persist agent memory across runs, shape per-turn LLM context, cap history in AG2. Covers `KnowledgePlugin` (`plugins=[KnowledgePlugin(store, expose_tool=, aggregate=, compact=, ...)]`), stores `MemoryKnowledgeStore`, `SqliteKnowledgeStore`, `DiskKnowledgeStore` (needs watchdog), `RedisKnowledgeStore` (ag2[redis]), `LockedKnowledgeStore`; assembly policies via `Middleware(AssemblerMiddleware, policies=[...])` (`WorkingMemoryPolicy`, `EpisodicMemoryPolicy`, `ConversationPolicy`, `SlidingWindowPolicy`, `TokenBudgetPolicy`, `AlertPolicy` + `HaltCheckMiddleware`); aggregation (`WorkingMemoryAggregate`, `ConversationSummaryAggregate`, `AggregateTrigger`); compaction (`TailWindowCompact`, `SummarizeCompact`, `CompactTrigger`); `EventLogWriter`; `AggregationCompleted`/`CompactionCompleted`/`*Failed` events. Use for remembering between conversations, long histories, or prompt assembly; replaces deprecated `knowledge=`/`assembly=`. See `ag2-middleware`, `ag2-observers-and-alerts`."
license: Apache-2.0
metadata:
  ag2_version: "1.3.0"
---

# Knowledge, memory, and context assembly

| Primitive | Import | Role |
|---|---|---|
| `KnowledgeStore` | `ag2.knowledge` | Path-based async persistent storage |
| `KnowledgePlugin` | `ag2.knowledge` | Puts a store on an Agent + optional `knowledge` tool, bootstrap, `/log/` dump, aggregation, compaction |
| Assembly policies | `ag2.policies` (+ `AssemblerMiddleware` from `ag2.assembly`) | Shape `(prompts, events)` before each LLM call |
| Aggregation / compaction | `ag2.aggregate` / `ag2.compact` | Write memory into the store / trim stream history |

## When to use what

| Goal | Reach for |
|---|---|
| Remember user facts between conversations | `WorkingMemoryAggregate` (writes) + `WorkingMemoryPolicy` (reads) + persistent store |
| Summarise each session for next time | `ConversationSummaryAggregate` + `EpisodicMemoryPolicy` |
| Cap what the LLM sees | `SlidingWindowPolicy(max_events=N)` / `TokenBudgetPolicy(max_tokens=N)` |
| Hide lifecycle/observer noise from the LLM | `ConversationPolicy()` |
| Trim the stream history itself | `TailWindowCompact` (free) / `SummarizeCompact` (1 LLM call) |
| Let the model read/write files itself | `KnowledgePlugin(store)` (default `expose_tool=True`) |

## Recipe — persistent working memory

```python
from ag2 import Agent
from ag2.aggregate import AggregateTrigger, WorkingMemoryAggregate
from ag2.assembly import AssemblerMiddleware
from ag2.config import OpenAIResponsesConfig
from ag2.knowledge import KnowledgePlugin, SqliteKnowledgeStore
from ag2.middleware import Middleware
from ag2.policies import ConversationPolicy, WorkingMemoryPolicy

config = OpenAIResponsesConfig(model="gpt-4.1-mini")
store = SqliteKnowledgeStore("./journal.db")   # no extra install needed

agent = Agent(
    "journal",
    prompt="You are a daily journal companion.",
    config=config,
    plugins=[KnowledgePlugin(
        store,
        expose_tool=False,         # memory is policy-driven; no `knowledge` tool
        write_event_log=False,     # no /log/{stream_id}.jsonl dumps
        aggregate=WorkingMemoryAggregate(config=config),   # cheaper model is fine
        aggregate_trigger=AggregateTrigger(on_end=True),
    )],
    middleware=[Middleware(AssemblerMiddleware, policies=[
        WorkingMemoryPolicy(),     # injection first: reads /memory/working.md
        ConversationPolicy(),      # then reduction
    ])],
)
reply = await agent.ask("I'm choosing between a Silvia Pro and a Linea Mini.")
print(await store.read("/memory/working.md"))   # written when ask() returned
```

`on_end` aggregation fires when each `ask()` / `reply.ask()` finishes. A new `Agent` (even a new process) on the same DB gets the memory injected. Runnable: `assets/journal_companion.py`; assembly + compaction demo: `assets/long_doc_chat.py`.

## Store backends and installs

| Store | Install | Notes |
|---|---|---|
| `MemoryKnowledgeStore()` | core | Tests / ephemeral |
| `SqliteKnowledgeStore(path, *, poll_interval_s=0.5)` | core | Pragmatic durable default |
| `DiskKnowledgeStore(root)` | `pip install "watchdog>=4.0,<7"` (**no ag2 extra**) | Human-readable files. Import works without watchdog, *constructing* raises `ImportError` with that hint |
| `RedisKnowledgeStore(url_or_client, ..., poll_interval_s=0.5)` | `pip install "ag2[redis]"` | Multi-process / cross-host |
| `LockedKnowledgeStore(store, lock)` | core | Serialises `write`/`delete`/`append` via your `acquire(name, ttl)`/`release(name)` lock |

All methods are async: `read`, `write`, `list`, `delete`, `exists`, `append` (returns byte offset), `read_range(path, start, end=None)`, `on_change(path, cb)`. Import `KnowledgeStore` from `ag2.knowledge` (not top-level `ag2`).

## `KnowledgePlugin(store, *, ...)` defaults

- `expose_tool=True` — adds a `knowledge(action="read"|"write"|"list"|"delete", path, content)` tool.
- `write_event_log=True` — dumps the stream to `/log/{stream_id}.jsonl` after each `ask()`.
- `bootstrap=None` — seeds `/SKILL.md`, `/log/`, `/memory/`, `/artifacts/` on first use (`DefaultBootstrap(mention_tool=expose_tool)`); pass your own `StoreBootstrap` to change/disable.
- `aggregate` + `aggregate_trigger`, `compact` + `compact_trigger` — give them **in pairs**: `compact` without a trigger, or an active trigger without `aggregate`, raises `ValueError` at construction; `aggregate` without a trigger (or `AggregateTrigger()` with nothing enabled) silently never fires. On turn end aggregation runs before compaction.
- Only **one** `KnowledgePlugin` per invocation. `dependencies={KnowledgeStore: other}` overrides the plugin's store for tools, policies and maintenance.
- Bare minimum without a plugin: `Agent(..., dependencies={KnowledgeStore: store})` — policies can read the store; no tool, no bootstrap, no logs, no maintenance.

## Assembly policies

Register via `middleware=[Middleware(AssemblerMiddleware, policies=[...]), Middleware(HaltCheckMiddleware)]`. **Injection before reduction**: `WorkingMemoryPolicy`, `EpisodicMemoryPolicy(max_episodes=5)`, `AlertPolicy()` → `ConversationPolicy()`, `SlidingWindowPolicy(max_events=N, transparent=False)`, `TokenBudgetPolicy(max_tokens=N, chars_per_token=4)`. Add `Middleware(HaltCheckMiddleware)` right after the assembler when using `AlertPolicy` so FATAL alerts stop the call. `AssemblerMiddleware.validate_order(policies)` returns warnings for bad orderings. Window limits are *targets*: orphaned tool results are dropped, so the sent window can be smaller (or larger) than asked.

## Migration (deprecated in 1.3, removed in 1.4)

`Agent(knowledge=KnowledgeConfig(...))` → `plugins=[KnowledgePlugin(store, ...)]` (same kwargs). `Agent(assembly=[...])` → `middleware=[Middleware(AssemblerMiddleware, policies=[...]), Middleware(HaltCheckMiddleware)]` (the old form added the halt check automatically; drop it and FATAL alerts stop halting). Mixing `knowledge=` with a `KnowledgePlugin` raises `ValueError`.

## Reference

- `references/details.md` — full wiring example, aggregation/compaction strategies, custom policies/strategies, lifecycle events, `EventLogWriter`, pitfalls.
- Upstream: `website/docs/user-guide/agent_harness.mdx`, `advanced/knowledge_store.mdx`, `advanced/assembly.mdx`, `advanced/aggregation.mdx`, `advanced/compaction.mdx`; ADR `docs/adr/0033-knowledge-is-a-plugin-with-scoped-maintenance.md`.
