# Task observation and capability track records (verified against ag2 1.3.0)

`Resume.observed[capability]` is an `ObservedStat(n, completed, failed, expired, p50_latency_ms)` the hub updates when a **capability-tagged** task reaches a terminal state on a stream that has a `TaskMirror` attached.

> Migration: `Agent.task(...)` is deprecated in 1.3.0 and removed in 1.4.0. Replace
> `async with agent.task(title, capability=..., context=ctx)` with
> `async with Task(owner_id=agent.name, spec=TaskSpec(title=..., capability=...), context=ctx)` (`from ag2.task import Task, TaskSpec`), or — for a whole model run — `agent.ask(..., capability=..., ttl_seconds=...)`.

## Three ways to produce an observation

| Path | When | How the mirror sees it |
|---|---|---|
| `Task(...)` inside a `@tool` | a tool's unit of work is the capability | pass `context=ctx`; the default handler's per-turn `TaskMirror` is on that stream |
| `agent.ask(..., capability=...)` | the whole model run is the capability | you call `ask` yourself on a stream with a mirror attached (custom handler or script) |
| peer `delegate(..., capability=...)` | — | only stored as `knobs["capability"]` on the consulting channel; it does **not** update anyone's `observed` by itself |

The mirror always records under **its own** `owner_id` (`client.agent_id`), regardless of `Task(owner_id=...)`.

### 1. `Task` in a tool (executed, default handler)

```python
from ag2 import Agent, Context
from ag2.task import Task, TaskSpec

worker_agent = Agent("worker", prompt="Always call the research tool, then answer.", config=config)


@worker_agent.tool  # decorate the Agent before hc.register(...); AgentClient has no .tool
async def research(topic: str, ctx: Context) -> str:
    """Research a topic."""
    async with Task(
        owner_id=worker_agent.name,
        spec=TaskSpec(title=f"research: {topic}", capability="research"),
        context=ctx,  # REQUIRED: emits on the turn's stream, where the TaskMirror listens
    ) as task:
        await task.progress({"step": "gather"})
        await task.complete({"items_found": 7})
    return f"7 sources on {topic}"


worker = await hc.register(worker_agent, Passport(name="worker"), Resume(claimed_capabilities=["research"]))
```

Leaving the block without `complete()` completes it; an exception fails it; cancellation emits `TaskCancelled`. Inside the block the LLM's `tasks(action="progress"|"complete")` plugin tool resolves the active task via `TaskInject`.

### 2. `ask(capability=...)` with a manual mirror (executed)

```python
from ag2.network import TaskMirror
from ag2.stream import MemoryStream

mirror = TaskMirror(hub_client=worker._hub_client, owner_id=worker.agent_id, channel_id=None)
stream = MemoryStream()
subs = mirror.attach(stream)
try:
    reply = await worker_agent.ask("Summarise X", stream=stream, capability="summarisation", ttl_seconds=60)
finally:
    mirror.detach(stream, subs)
```

The run's task title is the agent name (never the prompt). `ttl_seconds` is an observation deadline enforced by the hub's TTL sweeper, not a local timeout; if hub expiry wins, `EXPIRED` is recorded once and a late completion is ignored. A tagged run waits for its background descendants before returning. To tag every network turn, use a custom notify handler that calls `client.agent.ask(*parts, stream=stream, dependencies=deps, capability="...")` — full handler in `ag2-network-tools-and-views/references/custom-handlers.md`.

Don't call `ask` on `ctx.stream` from inside a tool of the same running turn — the stream's turn lock is held.

## What gets written

On the accepted terminal:

- `Resume.observed[capability]` updated (deduped per `(owner_id, task_id)`), audit `AUDIT_KIND_RESUME_SET` with `source=RESUME_SOURCE_OBSERVED`, listener `on_agent_event(..., "observation_recorded", ...)`.
- `AUDIT_KIND_TASK_TERMINATED` record: `task_id`, `owner_id`, `channel_id`, `outcome`, `capability`, `reason`.
- `HubListener.on_task_event(task_id, "completed"|"failed"|"expired"|"cancelled", payload)`.

Untagged tasks are still observed (`hub.list_tasks()`, `task_terminated` audit) but don't touch `observed`.

```python
resume = await hub.get_resume(worker.agent_id)
stat = resume.observed.get("research")
if stat:
    print(stat.completed, stat.n, stat.failed, stat.expired, stat.p50_latency_ms)

for t in await hub.list_tasks():
    print(t.spec.title, t.state, t.spec.capability)
```

Latency is `started_at → terminal` on the hub's clock (deterministic with a fake `clock=`). Observed capabilities join the hub's capability index, so `peers(action="find", capability=..., sort_by="track_record")` can rank by them.

## Manual mirror in a custom handler

The default handler attaches a fresh `TaskMirror(hub_client=client._hub_client, owner_id=client.agent_id, channel_id=metadata.channel_id)` per inbound substantive envelope and detaches it after `ask`. Replicate that in any custom handler that runs the LLM, or tasks never reach the hub. The mirror swallows hub errors (reported as `mirror_failed`) so a flaky hub never crashes a turn. `Rule.limits.max_concurrent_tasks` is enforced when the hub first observes a task.
