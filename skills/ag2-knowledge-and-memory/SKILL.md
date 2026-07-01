---
name: ag2-knowledge-and-memory
description: Persist agent state across runs, shape what the LLM sees per turn, and cap history to fit a context window. Covers `KnowledgeStore` (memory / sqlite / disk / redis), `KnowledgeConfig` (`store=`, `expose_tool=`, `write_event_log=`, `compact=`, `aggregate=`, `bootstrap=`) and its opt-out flags, aggregation strategies (`WorkingMemoryAggregate` with `prompt=` override, `ConversationSummaryAggregate`), assembly policies (`WorkingMemoryPolicy`, `EpisodicMemoryPolicy`, `ConversationPolicy`, `SlidingWindowPolicy`, `TokenBudgetPolicy`, `AlertPolicy`), compaction (`TailWindowCompact`, `SummarizeCompact`), and the lifecycle events (`AggregationStarted/Failed`, `CompactionStarted/Failed`, `EventLogFailed`). Use when the user wants the agent to remember between conversations, manage long histories, or control prompt assembly.
license: Apache-2.0
---

# Knowledge, memory, and context assembly

This skill covers three related primitives that work together:

| Primitive | Lives in | Role |
|---|---|---|
| **`KnowledgeStore`** | `ag2.knowledge` | Path-based persistent storage (memory / sqlite / disk / redis) |
| **Assembly policies** | `ag2.policies` | Shape `(prompts, events)` per turn before the LLM call |
| **Aggregation / Compaction** | `ag2.aggregate` / `.compact` | Write structured knowledge to the store / trim event history |

`KnowledgeConfig` wires all three onto an `Agent` via the `knowledge=` constructor parameter; assembly policies go via `assembly=`.

## When to use what

| User intent | Reach for |
|---|---|
| Remember user preferences / state between conversations | `WorkingMemoryAggregate` + `WorkingMemoryPolicy` (and a persistent store) |
| Summarise each session for next time | `ConversationSummaryAggregate` + `EpisodicMemoryPolicy` |
| Hard-cap event history sent to the LLM | `SlidingWindowPolicy(max_events=N)` |
| Cap by approximate token count | `TokenBudgetPolicy(max_tokens=N)` |
| Drop lifecycle / observer events from the LLM's view | `ConversationPolicy()` |
| Trim stream history (not just LLM view) | `TailWindowCompact` or `SummarizeCompact` |
| Route observer alerts to the LLM | `AlertPolicy()` |

## 60-second recipe — persistent working memory

```python
from ag2 import Agent, KnowledgeConfig
from ag2.aggregate import AggregateTrigger, WorkingMemoryAggregate
from ag2.config import OpenAIConfig
from ag2.knowledge import DiskKnowledgeStore
from ag2.policies import ConversationPolicy, WorkingMemoryPolicy

store = DiskKnowledgeStore("./journal-state")
config = OpenAIConfig(model="gpt-5")

agent = Agent(
    "journal",
    prompt="You are a daily journal companion.",
    config=config,
    knowledge=KnowledgeConfig(
        store=store,
        aggregate=WorkingMemoryAggregate(config=config),
        aggregate_trigger=AggregateTrigger(on_end=True),
    ),
    assembly=[
        WorkingMemoryPolicy(),  # injects /memory/working.md on every LLM call
        ConversationPolicy(),
    ],
)
```

After each conversation the aggregate writes `/memory/working.md`. The next time you build an `Agent` against the same store, `WorkingMemoryPolicy` reads that file in and injects it as prompt context. The agent "remembers" without replaying chat history. Full runnable example: `assets/journal_companion.py`.

> Heads-up: `knowledge=KnowledgeConfig(store=...)` also hands the LLM a `knowledge` tool, seeds `SKILL.md` files into the store (which tell the model to use that tool), and dumps each turn's events to `/log/`. Fine for a journal companion that manages its own memory — but if you want the store available to policies **without** exposing it to the model, pass `expose_tool=False, write_event_log=False`. See *"`KnowledgeConfig(store=...)` does four things — and three of them are now opt-out"* under **Wiring it all on the Agent**.

## `KnowledgeStore` implementations

| Implementation | Use when |
|---|---|
| `MemoryKnowledgeStore()` | Tests, ephemeral sessions |
| `SqliteKnowledgeStore(path)` | Single-process durability — pragmatic default |
| `DiskKnowledgeStore(root)` | Files should be human-readable on disk — first arg is `root`; **requires the `watchdog` extra** (the `on_change` watcher uses it). Without `watchdog` installed, importing the class raises a missing-dependency error. |
| `RedisKnowledgeStore(url_or_client)` | Multi-process / cross-host sharing — first arg is `url_or_client`: pass a Redis URL string *or* an already-built `redis.asyncio` client. A URL string also needs the `redis` package. |
| `LockedKnowledgeStore(store, lock)` | Wrap any store to serialize concurrent writers — first arg is `store`, second is the `lock` (both positional). Reads pass through unlocked; only `write` / `delete` / `append` acquire the lock. |

API (all async):

```python
await store.write("/artifacts/report.md", "# Q3...")
text = await store.read("/artifacts/report.md")
children = await store.list("/")            # immediate children, dirs end in '/'
await store.delete("/artifacts/old.md")
exists = await store.exists("/artifacts/report.md")

off = await store.append("/log/events.jsonl", '{"t":1}\n')   # WAL-style
new_slice = await store.read_range("/log/events.jsonl", off)  # only new bytes
sub = await store.on_change("/log/", on_change_callback)
```

## Assembly chain — what the LLM actually sees

Pass `AssemblyPolicy` instances via `assembly=[...]`. The Agent wires an internal `AssemblerMiddleware` at the outermost middleware position. Each policy transforms `(prompts, events)` and pipes into the next.

**Two kinds of policy — order matters: injection before reduction.**

| Kind | Purpose | Built-ins |
|---|---|---|
| **Injection** | Add to `prompts` | `WorkingMemoryPolicy`, `EpisodicMemoryPolicy`, `AlertPolicy` |
| **Reduction** | Trim `events` | `ConversationPolicy`, `SlidingWindowPolicy`, `TokenBudgetPolicy` |

Validate ordering manually:

```python
from ag2.assembly import AssemblerMiddleware
warnings = AssemblerMiddleware.validate_order(policies)  # returns list of warnings on known bad orderings
```

(`AssemblerMiddleware` and the `AssemblyPolicy` protocol live in `ag2.assembly` for advanced/manual harness wiring; you don't need to import them when just passing built-in policies via `assembly=[...]`.)

### Built-in policies

```python
from ag2.policies import (
    AlertPolicy,
    ConversationPolicy,
    EpisodicMemoryPolicy,
    SlidingWindowPolicy,
    TokenBudgetPolicy,
    WorkingMemoryPolicy,
)

# Injection
WorkingMemoryPolicy()                                 # reads /memory/working.md
EpisodicMemoryPolicy(max_episodes=5, transparent=True) # reads recent /memory/conversations/
AlertPolicy()                                          # delivers ObserverAlerts to LLM, halts on FATAL

# Reduction
ConversationPolicy()                                  # drops non-conversation events
SlidingWindowPolicy(max_events=50, transparent=True)  # last N events
TokenBudgetPolicy(max_tokens=32_000, chars_per_token=4, transparent=True)
```

`transparent=True` appends a `[policy_name] Showing X of Y events.` note to the prompt — useful while tuning. Realistic chain:

```python
assembly=[
    WorkingMemoryPolicy(),
    EpisodicMemoryPolicy(max_episodes=3),
    AlertPolicy(),
    SlidingWindowPolicy(max_events=80),
]
```

## Aggregation — writing knowledge to the store

`AggregateStrategy.aggregate(events, ctx, store) → None` extracts and persists. Two built-ins, both take a `ModelConfig` for a summarisation call (use a cheaper model than the agent's main one):

| Strategy | Writes | Pairs with |
|---|---|---|
| `WorkingMemoryAggregate(config=..., prompt="…")` | `/memory/working.md` (single rolling file) — `prompt=` overrides the merge template (`{existing}` / `{events}` placeholders) | `WorkingMemoryPolicy` |
| `ConversationSummaryAggregate(config=...)` | `/memory/conversations/{ts}_{stream_id}.md` | `EpisodicMemoryPolicy` |

`AggregateTrigger` controls cadence — `every_n_turns`, `every_n_events`, `on_end`. `AggregateTrigger()` alone fires nothing; opt in to at least one. `on_end=True` defaults off because each fire is an LLM call.

## Compaction — trimming stream history

`CompactStrategy.compact(events, ctx, store) → list[BaseEvent]`. Replaces the stream's history. Two built-ins:

| Strategy | Behaviour | Cost |
|---|---|---|
| `TailWindowCompact(target=N)` | Keep last N events; drop the rest (optionally persist to `/log/`) | Zero LLM calls |
| `SummarizeCompact(target=N, config=...)` | Summarise dropped events into one `CompactionSummary`; insert at head | One LLM call per fire |

`CompactTrigger(max_events=N, max_tokens=M, chars_per_token=4)` — fires when any threshold is crossed.

```python
from ag2.compact import CompactTrigger, TailWindowCompact, SummarizeCompact
```

`SummarizeCompact` inserts a `CompactionSummary` event at the head; `ConversationPolicy` allows it through so the LLM still gets that context.

## Wiring it all on the Agent

`KnowledgeConfig` is the bundle:

```python
from dataclasses import dataclass

@dataclass
class KnowledgeConfig:
    store: KnowledgeStore
    expose_tool: bool = True               # auto-inject the `knowledge` LLM tool?
    write_event_log: bool = True           # dump each turn's events to /log/{stream_id}.jsonl?
    compact: CompactStrategy | None = None
    compact_trigger: CompactTrigger | None = None
    aggregate: AggregateStrategy | None = None
    aggregate_trigger: AggregateTrigger | None = None
    bootstrap: StoreBootstrap | None = None    # default: DefaultBootstrap(mention_tool=expose_tool)
```

### `KnowledgeConfig(store=...)` does four things — and three of them are now opt-out

Passing `knowledge=KnowledgeConfig(store=...)` bundles four concerns. Defaults preserve the original behaviour, but **`expose_tool` and `write_event_log` flags now turn most of it off**:

1. **Registers the store** so assembly policies (`WorkingMemoryPolicy`, …) can `context.dependencies.get(KnowledgeStore)`. (Usually the only thing you wanted — and the one piece with no flag, because it's the point.)
2. **Auto-injects a `knowledge` action-group tool** into the agent's tool list (`knowledge(action="write"/"read"/"list"/...)`). Disable with `expose_tool=False`.
3. **Seeds `SKILL.md` files into the store** on first `ask` — `/SKILL.md`, `/artifacts/SKILL.md`, `/memory/SKILL.md`, `/log/SKILL.md` — unless you pass an explicit `bootstrap=`. The default is `DefaultBootstrap(mention_tool=expose_tool)`, so when you set `expose_tool=False` the seeded `SKILL.md` automatically stops telling the LLM to "use the `knowledge` tool" (it can't — there is no tool). For *no* seeding at all, pass `bootstrap=` your own no-op `StoreBootstrap` (a class with `async def bootstrap(self, store, actor_name): pass`); there's still no built-in `NoBootstrap`.
4. **Persists turn events to `/log/`** — after every turn, the agent's full event history is dumped to `/log/{stream_id}.jsonl`. Disable with `write_event_log=False`. Persistence failures emit an `EventLogFailed` event on the stream (and also `logger.exception`).

So "store visible to policies, invisible to the LLM, no `/log/` clutter, no `SKILL.md` files" is:

```python
agent = Agent(
    "summarizer",
    config=config,
    knowledge=KnowledgeConfig(
        store=DiskKnowledgeStore("./agent-state"),
        expose_tool=False, write_event_log=False,   # no `knowledge` tool, no /log/ dump
        aggregate=WorkingMemoryAggregate(config=cheap_config),  # aggregation/compaction wiring still works
        aggregate_trigger=AggregateTrigger(on_end=True),
    ),
    assembly=[WorkingMemoryPolicy()],   # reads /memory/working.md from context.dependencies
)
```

If you also want zero `SKILL.md` seeding, add `bootstrap=NoBootstrap()` where `NoBootstrap` is your own no-op `StoreBootstrap`. The older alternative — skip `KnowledgeConfig` entirely and register the store as a plain `Agent(..., dependencies={KnowledgeStore: store})` — still works and is the absolute minimum (no tool, no bootstrap, no `/log/`, *and* no aggregate/compact wiring); use it when you want to drive `store.write("/memory/working.md", ...)` yourself from, say, a separate "reflector" pass.

Full shape:

```python
agent = Agent(
    "assistant",
    config=main_config,
    knowledge=KnowledgeConfig(
        store=DiskKnowledgeStore("./state"),
        compact=TailWindowCompact(target=100),
        compact_trigger=CompactTrigger(max_events=200),
        aggregate=ConversationSummaryAggregate(config=summarizer_config),
        aggregate_trigger=AggregateTrigger(every_n_turns=10, on_end=True),
        # expose_tool / write_event_log default True; bootstrap defaults to
        # DefaultBootstrap(mention_tool=expose_tool). Set explicitly only to opt out.
    ),
    assembly=[
        WorkingMemoryPolicy(),
        EpisodicMemoryPolicy(max_episodes=3),
        AlertPolicy(),
        SlidingWindowPolicy(max_events=80),
    ],
)
```

The harness wires internal middleware conditionally — `_AssemblerMiddleware`, `_HaltCheckMiddleware`, `_CompactionMiddleware`, `_AggregationMiddleware`. You only pay for what you turn on.

Lifecycle events emitted on the agent's stream:

- `CompactionStarted` → `CompactionCompleted` (`events_before` / `events_after` / `usage`) or `CompactionFailed` (the exception)
- `AggregationStarted` → `AggregationCompleted` (`strategy` / `usage`) or `AggregationFailed` (the exception)
- `EventLogFailed` if persisting the `/log/` event stream raised
- `HaltEvent` when `AlertPolicy` sees a FATAL alert

The `*Failed` ones are the durable, observable signal — the harness also `logger.exception`s them, but you don't need to configure Python logging to see something went wrong: subscribe to the stream (see `ag2-observers-and-alerts`).

## Going deeper

- `assets/journal_companion.py` — runnable end-to-end working-memory demo (mirrors `code_examples/06`).
- `assets/long_doc_chat.py` — assembly + compaction stress test (mirrors `code_examples/07`).
- Source docs:
  - `website/docs/user-guide/advanced/knowledge_store.mdx` — store API, `EventLogWriter`, `LockedKnowledgeStore`.
  - `website/docs/user-guide/advanced/assembly.mdx` — full policy reference and ordering rules.
  - `website/docs/user-guide/advanced/aggregation.mdx` — aggregate strategies and custom strategies.
  - `website/docs/user-guide/advanced/compaction.mdx` — compact strategies and custom strategies.
  - `website/docs/user-guide/agent_harness.mdx` — `KnowledgeConfig` constructor reference, turn-lifecycle middleware order.

## Common pitfalls

- **Reduction before injection** — `SlidingWindowPolicy` before `WorkingMemoryPolicy` means the working memory injection isn't counted against the budget. Always: injections first, then `AlertPolicy`, then reductions.
- **Forgetting `KnowledgeStore` dependency for memory policies** — `WorkingMemoryPolicy` and `EpisodicMemoryPolicy` look up the store via `context.dependencies.get(KnowledgeStore)`. `KnowledgeConfig(store=...)` registers it for you; if you wire the policy manually, register the store in `dependencies` too.
- **Aggregation costs an LLM call per fire** — `on_end=True` on every conversation can add up. Pair `WorkingMemoryAggregate` and `ConversationSummaryAggregate` thoughtfully; consider `every_n_turns=N` for high-volume agents.
- **Mixing `HistoryLimiter` middleware with assembly reduction policies** — they both trim. Pick one mechanism. Assembly is more flexible (rich shaping, transparency notes); `HistoryLimiter` is simpler.
- **`read_range` operates on byte offsets, not character offsets** — multi-byte UTF-8 sequences need careful alignment.
- **Forgetting that `WorkingMemoryAggregate` is destructive** — it overwrites `/memory/working.md` each fire. That's intentional (rolling state, not log) but expect prior content to merge or disappear.
- **Expecting `AlertPolicy` to render alerts to the LLM without being in `assembly=`** — alerts sit on the stream as `ObserverAlert` events but only reach the LLM when `AlertPolicy` injects them.
- **`KnowledgeConfig(store=...)` ≠ "just a storage handle"** — it also auto-injects a `knowledge` LLM tool, seeds `SKILL.md` files into the store, and dumps every turn's events to `/log/`. Turn those off with `KnowledgeConfig(store=..., expose_tool=False, write_event_log=False)` (the default bootstrap then also stops mentioning the tool, since `mention_tool` defaults to `expose_tool`); or skip `KnowledgeConfig` and use `Agent(..., dependencies={KnowledgeStore: store})` for the bare minimum. See "`KnowledgeConfig(store=...)` does four things — and three of them are now opt-out" above.
- **`WorkingMemoryAggregate`'s default prompt is content-oriented** — out of the box it preserves *what the conversation was about* ("preserve important existing context, remove outdated information"), not *strategy*; a research agent that wants memory to track tactics (which phrasings/domains worked) gets stale topical facts instead. Pass `WorkingMemoryAggregate(config=..., prompt="…")` to override it — the template gets `{existing}` (current working memory) and `{events}` (the new conversation) interpolated. For something more radical than "different prompt, same shape", write a custom `AggregateStrategy.aggregate(events, ctx, store)` (or write the file directly from a reflector pass).
- **Watch for `*Failed` events when debugging custom strategies** — if a custom `AggregateStrategy`/`CompactStrategy` (or its trigger) raises, the harness `logger.exception`s it *and* emits `AggregationFailed` / `CompactionFailed` (with the exception) on the stream — and `EventLogFailed` if the `/log/` write blows up. So "trigger didn't fire" vs. "strategy raised" is distinguishable without configuring Python logging — just subscribe (`ag2-observers-and-alerts`). You also get `AggregationStarted` / `CompactionStarted` to confirm the trigger *did* fire.
- **Store filesystem root vs in-store path nest** — `DiskKnowledgeStore("./memory")` roots the store at `./memory`, and the in-store working-memory path is `/memory/working.md`, so the file lands at `./memory/memory/working.md`. Name the FS root something distinct (`./agent-state`, `./journal-state`) to avoid the "memory inside memory" head-scratch.
