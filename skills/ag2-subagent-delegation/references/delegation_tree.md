# Delegation tree — depth reference (AG2 1.3.0)

## Migrating from `TaskConfig` (deprecated 1.3.0, removed 1.4.0)

| `TaskConfig` | Explicit worker |
|---|---|
| `config=None` (subtask used the parent's config) | Pass the config to the worker — `config=` is not inherited |
| `prompt` default ("You are a task agent...") | Set the worker's `prompt=`; there is no default task prompt |
| parent tools filtered by `include_tools` / `exclude_tools` | List the tools in the worker's `tools=` (fixed at construction) |
| `extra_tools` | Add them to the worker's `tools=` |
| `max_concurrency` | One function tool middleware with a semaphore on every delegate (see SKILL.md) |
| `run_subtasks([...], parallel=True)` | Prompt the model to emit several `task_worker` calls in one response |
| "subtasks cannot recurse" | A worker can only delegate via tools you give it — keep delegate tools off workers |
| generic worker per call | `dynamic_agent()` from `ag2.tools.dynamic` |

Remove `tasks=` from the parent and rewrite prompts that mention `run_subtask(s)` to name `task_<worker>` and its `objective` / `context` parameters.

## What a parent receives from descendants

| Source event | Ancestor receives | Filter |
|---|---|---|
| Model requests/responses, tool calls/results, other operational events | `ChildEvent` wrapping the original | `@observer(ChildEvent)`, then `event.source_event` |
| Transient events (`ModelMessageChunk`) | `TransientChildEvent` (subclass of `ChildEvent`) | same filter |
| `UsageEvent`, `TaskStarted`/`TaskCompleted`/`TaskFailed`/`TaskCancelled` | the event itself, with `path` (+ `task_id`, `agent_name`) | `@observer(UsageEvent)`, `@observer((TaskStarted, TaskCompleted, TaskFailed))` |

`ChildEvent` fields: `inner`, `source_event` (property), `task_id`, `path` (delegation task ids, outermost first), `agent_name`, `origin_event_id`. Grandchildren are forwarded with the same envelope (not nested). Observers on the root therefore see the whole tree.

Local-only lifecycle/usage: filter `event.path == ()`.

`BaseEvent.source_event` returns the event itself for local events, so shared inspection code can call it on either form.

### Streaming a worker's tokens live

```python
from ag2 import observer
from ag2.events import ChildEvent, ModelMessageChunk

@observer(ChildEvent)
def stream_worker(event: ChildEvent) -> None:
    if isinstance(event.source_event, ModelMessageChunk):
        print(event.agent_name, event.source_event.content, end="")
```

Enable `streaming=True` on the worker's config.

### Replying to an event's author

While the author is running, `await event.origin.send(SomeEvent(...))` delivers an event into that exact invocation's pipeline (works for `ChildEvent` and forwarded inert events). Use non-conversational custom events (`__conversational__ = False`) to exchange guidance without a model call. Handles expire when the invocation ends; sending via an expired handle raises `DeliveryError` (`ag2.tools.subagents`) and publishes `DeliveryFailed`. Replayed history has no live `origin` (`RuntimeError`).

## Usage accounting

- `await reply.usage()` → `UsageReport` with `.total`, `.by_model`, `.by_provider`, `.by_kind`; includes every descendant, deduplicated by source `event_id`.
- Custom totals: `@observer(UsageEvent)` and count each `event_id` once.
- `TaskCompleted.usage` / `ModelResponse.usage` are snapshots — never sum them.
- `TokenMonitor` (see `ag2-observers-and-alerts`) on the root counts descendant spend too.

## Variable scopes

| Scope | Child receives | Changes visible |
|---|---|---|
| `Scope.COPIED` (default for plain values and `ScopedValue(v)`) | deep copy | only in that branch |
| `Scope.LOCAL` | nothing | only the owning branch |
| `Scope.SHARED` | the same binding | whole delegation tree of this invocation |

Rules:
- Declare shared bindings in the root agent's defaults or the root call's `variables=`. Introducing a new shared name below the root, changing a scope, or sharing reserved `ag:`/`a2a:` keys raises `ScopeError` (inside a worker's tool it becomes that tool's error result).
- For open-ended contributions, share a dict (`ScopedValue({}, scope=Scope.SHARED)`) and add keys to it.
- Shared values belong to *this* invocation tree; the caller's input container is not mutated.
- No synchronization and no rollback on worker failure.
- Tools receive the underlying value, not the `ScopedValue`.
- `approval_required` "always" answers are reserved `ag:` keys and are **not** inherited by workers.

Dependencies: shallow-copied; the parent's constructor defaults fill only keys the worker does not set; values passed via `ask(dependencies=...)` override.

## Sub-task streams

`stream=` on `as_tool()` / `subagent_tool()` / `background_agent_tool()` accepts exactly: `None` (fresh `MemoryStream` per call), a `Stream` **instance**, or a factory `(agent, context) -> Stream`. Passing the class (`stream=MemoryStream`) raises `TypeError` at build time.

```python
from ag2 import MemoryStream
from ag2.tools.subagents import persistent_stream

researcher.as_tool(description="Research", stream=persistent_stream())  # history accumulates per parent context
sub_stream = MemoryStream()
researcher.as_tool(description="Research", stream=sub_stream)  # inspect sub_stream.history afterwards
```

Both make the worker stateful (token cost grows) and serialize calls on that stream. To *observe* without accumulating history, use parent observers instead.

## Failure semantics

- A worker exception is returned to the parent model as the tool result `"Sub-task '<name>' failed: <error>"` (cancellation: `"... cancelled: ..."`); `TaskFailed` / `TaskCancelled` is forwarded to parent observers.
- A worker's human-input failure (`HumanInputError`) fails the whole delegating turn.
- Workers' `hitl_hook` questions are bridged to the parent (`HumanInputRequest.task_id` says which delegation asks).
