---
name: ag2-subagent-delegation
description: "Delegate work from one AG2 `Agent` to explicit worker agents exposed as tools via `Agent.as_tool()` / `subagent_tool()` (blocking), `background_agent_tool()` (fire-and-forget) or `dynamic_agent()` (LLM-composed workers). Covers parallel fan-out (several delegate calls in one model response), a shared concurrency cap via one function tool middleware, observing which worker ran (`TaskStarted`/`TaskCompleted` with `agent_name`/`path`, `@observer(ChildEvent)` + `event.source_event`), tree-wide usage via `reply.usage()`, scoped variables across workers (`Scope`, `ScopedValue`, `ScopeError`), and sub-task streams (`persistent_stream`, shared `Stream`, factories). Replaces the deprecated `TaskConfig` / `tasks=` / `run_subtask(s)` (removed in 1.4.0). For agents collaborating through a registry, channels and governance use `ag2-network-quickstart`; for event plumbing see `ag2-observers-and-alerts`."
license: Apache-2.0
metadata:
  ag2_version: "1.3.0"
---

# Subagent delegation

## When to use

- "Coordinator + specialists" — a parent hands parts of a task to a researcher, a math expert, a writer.
- "Fan out then collect" — independent sub-questions that should run concurrently.
- "Which worker ran?" — observe delegations, their tool calls and token spend from the parent.

> `TaskConfig`, `Agent(tasks=...)` and auto-injected `run_subtask` / `run_subtasks` are **deprecated in 1.3.0, removed in 1.4.0**. Build workers explicitly (below). Migration table: `references/delegation_tree.md`.

## Recipe — explicit workers

```python
from ag2 import Agent
from ag2.config import OpenAIResponsesConfig

config = OpenAIResponsesConfig(model="gpt-4.1-mini")

poet = Agent("poet", prompt="Write a 2-line poem on the topic.", config=config)
fact_checker = Agent("fact_checker", prompt="State one verified fact on the topic.", config=config)

coordinator = Agent(
    "coordinator",
    prompt="Call task_poet and task_fact_checker in ONE response (parallel), then combine.",
    config=config,
    tools=[
        poet.as_tool(description="Write a short poem."),
        fact_checker.as_tool(description="State one fact."),
    ],
)
reply = await coordinator.ask("Topic: the Moon")
print(reply.body)
```

- The LLM sees `task_{agent.name}` with params `objective` (required) and `context` (optional). Override with `name=`.
- `as_tool(description=..., name=None, stream=None, middleware=())`. `description` is required.
- **Workers inherit nothing**: pass `config=`, `prompt=`, `tools=` to each worker yourself.
- Each call runs the worker on a **fresh stream** (no history between calls); the same worker Agent object is reused.

## Parallel execution and a shared concurrency cap

When the model returns several tool calls in one response, AG2 runs them concurrently — prompt for it explicitly ("call both in one response"). `as_tool()` has no `max_concurrency`; share **one function** tool middleware across delegates:

```python
import asyncio
from ag2.middleware import ToolMiddleware

def concurrency_limit(limit: int) -> ToolMiddleware:
    slots = asyncio.Semaphore(limit)
    async def limited(call_next, event, context):
        async with slots:
            return await call_next(event, context)
    return limited

cap = concurrency_limit(3)   # create inside the running event loop
tools = [poet.as_tool(description="...", middleware=[cap]), fact_checker.as_tool(description="...", middleware=[cap])]
```

Use a function/closure, **not** a callable object: tool middleware objects are deep-copied per tool, so each delegate would get its own semaphore.

## Observe which worker ran (1.3.0 delegation tree)

Register observers on the **parent**; every descendant event reaches it.

```python
from ag2 import observer
from ag2.events import ChildEvent, TaskStarted, ToolCallEvent

workers: list[str] = []

@observer(TaskStarted)               # lifecycle arrives as itself, with provenance
def on_start(event: TaskStarted) -> None:
    if event.path:                   # () = local run; non-empty = a delegation
        workers.append(event.agent_name)

@observer(ChildEvent)                # everything else a worker does, wrapped
def on_child(event: ChildEvent) -> None:
    if isinstance(event.source_event, ToolCallEvent):
        print(event.agent_name, event.source_event.name, event.source_event.arguments)

coordinator = Agent(..., observers=[on_start, on_child])
reply = await coordinator.ask("...")
total = (await reply.usage()).total.total_tokens   # includes every worker
```

- `TaskStarted` / `TaskCompleted` / `TaskFailed` / `TaskCancelled` and `UsageEvent` are forwarded **as themselves** (fields `agent_name`, `task_id`, `path`, `objective`; `TaskStarted.parent_tool_call_id`).
- Model/tool/other events arrive as `ChildEvent` (`TransientChildEvent` for chunks): `.source_event`, `.agent_name`, `.path`. A parent `@observer(ToolCallEvent)` sees only the parent's own calls (`task_poet`, ...).
- `ChildEvent`s never enter the parent's transcript or trigger its tools.

## Variables across workers

Plain variables are **deep-copied** into each worker; changes never flow back. To share, declare at the root:

```python
from ag2 import Scope, ScopedValue

reply = await coordinator.ask("...", variables={
    "notebook": ScopedValue([], scope=Scope.SHARED),   # workers' appends visible to root
    "secret": ScopedValue("x", scope=Scope.LOCAL),     # not passed to workers
    "draft": [],                                        # copied per worker
})
print(reply.context.variables["notebook"])   # read results here, not from your input dict
```

Shared names must be declared at the root (new shared names below the root raise `ScopeError`). Sharing is not locking — pass an `asyncio.Lock` via dependencies if workers race.

## Other delegate shapes

- `background_agent_tool(agent, description=...)` (`ag2.tools.subagents`) — returns a task id immediately; the result arrives later as a follow-up turn; `ask()` still waits for it.
- `dynamic_agent(available_tools=[...], config=...)` (`ag2.tools.dynamic`) — the LLM composes a worker per objective; spawned workers cannot spawn further.
- Self-delegation: `agent.add_tool(agent.as_tool(description=..., name="sub_task"))` — can recurse; there is no built-in guard.

## Going deeper

- `assets/research_squad.py` — runnable: two workers in parallel, shared cap, lifecycle + `ChildEvent` observers, shared `ScopedValue`, tree usage.
- `references/delegation_tree.md` — TaskConfig migration table, `event.origin.send(...)`, usage accounting, scope rules, `persistent_stream()` / shared stream / factories.
- Upstream: `website/docs/user-guide/subagents.mdx`, `advanced/stream.mdx#observe-a-delegation-tree`, `context/variables.mdx#variable-scopes-across-subagents`.

## Common pitfalls

- **Expecting worker mutations of plain variables to reach the parent** — they are deep copies. Use `Scope.SHARED`, or return data via the tool result.
- **Reading the dict you passed to `ask(variables=...)`** — it is copied; read `reply.context.variables`.
- **Parallel workers on one shared/persistent stream** — calls on one stream run one turn at a time; keep the default fresh stream for parallel workers.
- **Hard-coding which workers ran** — observe `TaskStarted.agent_name` instead.
- **Summing `TaskCompleted.usage`** — it is a cumulative snapshot; use `reply.usage()` or `UsageEvent`s (dedupe by `event_id`).
