# LLM-facing network tools (verified against ag2 1.3.0)

Per turn the LLM sees: the agent's own `@tool`s + **plugin tools** (`NetworkPlugin`, attached by `hc.register(..., attach_plugin=True)` — the default) + **adapter tools** (`adapter.tools_for(client, metadata, state, participant_id)`, merged into `agent.ask(tools=...)` by the default handler).

`attach_plugin=False` removes only the five plugin verbs (and the `NetworkContextPolicy` prompt prefix). It never removes `say`.

## Adapter tool: `say(content, audience?, channel_id?)`

Posts an `EV_TEXT` into the active channel (or another channel the agent is in). `audience` = peer **names**; `None` broadcasts.

| Adapter | `say` offered to |
|---|---|
| `consulting` | whoever's turn it is in the 1Q1R |
| `conversation` | every participant |
| `discussion` | only `expected_next_speaker` |
| `workflow` | nobody (routing is your handoff `@tool`s) |

**Double-send hazard:** the default handler already posts the agent's reply body as the round-end envelope. An agent that calls `say(...)` *and* returns a non-empty body posts twice; on `consulting` the second send hits a closed channel (`ProtocolError: channel '<id>' is closed`). Don't prompt agents to use `say`; if a model calls it anyway, use a custom handler that omits `tools=` (see `custom-handlers.md`).

## Plugin tools

| Tool | Args | Result |
|---|---|---|
| `delegate` | `target, prompt, capability=None, timeout=300.0` | Opens a separate `consulting` channel to `target` (name or id), sends `prompt`, and returns the reply text or an `"Error: ..."` string. `capability` is only stored as `knobs["capability"]`. |
| `peers(action="find")` | `query?, capability?, sort_by?("name"\|"cost"\|"track_record"), limit=20` | peer summaries (excludes caller) |
| `peers(action="describe")` | `name` | `{passport, resume, skill_md}`; `skill_md` falls back to a rendered passport+resume |
| `channels(action="list")` | `state="active"\|"all"` | channels the agent is in |
| `channels(action="open")` | `type, target, knobs?, intent?, ttl?, message?` | `{channel_id, type, participants[, seed_envelope_id]}`; `message` is sent as the first envelope once OPENED (failure closes with `seed_failed`) |
| `channels(action="info")` | `channel_id` | `ChannelMetadata` (participants only) |
| `channels(action="close")` | `channel_id?` (default current) | closes with `closed_by_agent` |
| `tasks(action="progress")` | `payload` | needs an active `Task(...)` block in the current call chain (resolved via `TaskInject`) |
| `tasks(action="complete")` | `result?` | terminal for the active task |
| `tasks(action="list")` | `scope="own"\|"all", state="active"\|"all", limit=20` | task summaries |
| `tasks(action="status")` | `task_id` | `TaskMetadata` |
| `tasks(action="wait")` | `task_id, timeout=300, poll_interval=0.1` | blocks until terminal |
| `tasks(action="cancel")` | `task_id, reason=""` | posts `EV_TASK_CANCEL_REQUEST` to the owner; the owner decides |
| `context(action="search")` | `query, scope="channel"\|"knowledge", limit=10` | case-insensitive substring hits (knowledge = the agent's own `KnowledgeStore`) |
| `context(action="quote")` | `speaker, recent_n=1, channel_id?` | the speaker's last `recent_n` `EV_TEXT`s |

There is no `tasks(action="start")`. Owners start tasks in code with `async with Task(owner_id=..., spec=TaskSpec(...), context=ctx)` from `ag2.task`. The old `agent.task(...)` is deprecated in 1.3.0 and removed in 1.4.0.

## Injects for your own tools

Inside the default handler, tool parameters annotated with these resolve automatically and are hidden from the LLM schema:

```python
from ag2.network import AgentClientInject, ChannelInject, ChannelStateInject, HubInject, TaskInject


@agent.tool
async def post_note(text: str, channel: ChannelInject = None, client: AgentClientInject = None) -> str:
    """Post a note to the current channel."""
    await channel.send(text)
    return "posted"
```

`HubInject` resolves to the `HubClient` (works in-process and cross-process). `ChannelStateInject` is the adapter's folded state, for example `WorkflowState`. `TaskInject` is the enclosing `Task(...)` block. The matching dependency keys are `CHANNEL_DEP`, `AGENT_CLIENT_DEP`, `HUB_DEP`, `CHANNEL_STATE_DEP` and `TASK_DEP`. Use them to stamp `dependencies=` when you call tools outside the handler, for example in tests.
