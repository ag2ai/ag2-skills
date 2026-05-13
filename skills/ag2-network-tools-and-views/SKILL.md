---
name: ag2-network-tools-and-views
description: Shape what an AG2 network agent perceives and which actions its LLM can take. Covers the six auto-injected LLM-facing tools that ship via `NetworkPlugin` (`say`, `delegate`, `peers`, `channels`, `tasks`, `context`); replacing the default handler with `agent_client.on_envelope(callback)` for custom envelope routing (gateways, headless workers, selective override); the `ViewPolicy` Protocol with the built-in `FullTranscript` and `WindowedSummary(recent_n=N)` views plus how to write a custom view; peer discovery via skill markdown (`skill_md=` at registration, `parse_skill_frontmatter`, `hub.set_skill`, `render_fallback_skill`); the `Envelope` wire format with the full `EV_*` event taxonomy (`EV_TEXT`, `EV_PACKET`, `EV_CHANNEL_INVITE` / `_OPENED` / `_CLOSED` / `_EXPIRED`, `EV_EXPECTATION_VIOLATED`, `EV_CONTEXT_SET`, `ag2.task.*`), `audience` and `visible_to` semantics, `Priority`, `causation_id`, and how to send raw envelopes with custom event types via `agent_client.send_envelope(...)`. Use when the user wants to customise the LLM's network surface, write a custom envelope handler, build a gateway / headless worker, or wire peer discovery.
license: Apache-2.0
---

# AG2 Network — Tools, Views & Custom Handlers

Everything on the agent/client side of the network — the mirror image of `ag2-network-governance` (which is hub-side). This covers what the LLM sees of the channel (views), which actions it can take (the six auto-injected tools), what other agents know about it (skill markdown), how to replace the handler entirely, and the full `Envelope` reference.

> Prerequisite: read `ag2-network-quickstart` first. This skill assumes you know `Hub.open`, `HubClient.register`, the channel lifecycle, and basic `agent_client.open(...)` / `channel.send(...)`.

## When to use

- "Limit / extend the LLM's network tool surface" (plugin tools vs. adapter `tools_for`)
- "Write a custom envelope handler"
- "Build a gateway / headless worker that doesn't run an LLM"
- "Add a non-LLM participant — a person at a UI, a bridge, a scripted seeder" (`HumanClient`)
- "Customise what each agent sees of the channel history (view policy)"
- "Strip / redact / filter envelopes before they reach the LLM"
- "Wire peer discovery via skill markdown"
- "Send a custom event type (`myapp.review_request`, …)"
- "I need the `EV_*` constants list / `Envelope` shape / `audience` semantics"

## Network tools — plugin tools vs. adapter tools

An agent's per-turn LLM tool list is assembled from two streams:

1. **Plugin tools** — when you register with the default `attach_plugin=True`, `NetworkPlugin` adds five identity-level, channel-agnostic verbs to `agent.tools`: `peers` / `channels` / `tasks` / `context` / `delegate`. Same behaviour in any channel.
2. **Adapter tools** — the channel's adapter offers channel-specific tools *per turn* via `adapter.tools_for(client, metadata, state, participant_id)`; the default handler resolves them and merges them into `agent.ask(tools=...)` (cached per `(adapter, client)` so the schema build cost is paid once). The only built-in adapter tool is `say`.

So `say` is **not** a plugin tool — it's offered by the adapter, gated by turn state:

| Adapter | Offers `say` to… |
|---|---|
| `consulting` | the participant whose turn it is in the 1Q1R (initiator before the prompt; respondent after, before the reply) |
| `conversation` | every participant, always |
| `discussion` | only `expected_next_speaker` |
| `workflow` | nobody — routing is your `@tool` handoff functions; the adapter returns `[]` |

That's why `attach_plugin=False` no longer controls whether the LLM sees `say` — it controls the five plugin verbs only. (See `ag2-network-quickstart` → "Plugin tools vs. adapter tools" for the user-facing summary.)

### `delegate` and `say`

| Tool | Stream | Signature | Purpose |
|---|---|---|---|
| `delegate` | plugin | `delegate(target, prompt, capability?, timeout=300)` | One-shot consult — opens a `consulting` channel with `target`, sends `prompt`, awaits the single reply, returns its text. A *separate* channel, so it's safe to call mid-turn on any channel. |
| `say` | adapter (`consulting` / `conversation` / `discussion`) | `say(content, audience?, channel_id?)` | Post an `EV_TEXT` into the active channel (or a specified one the agent participates in). `audience` is a list of peer **names** (resolved to ids); `None` broadcasts. Envelope shape comes from `adapter.build_text_envelope(...)` — the same Layer-2 helper a non-AG2 bridge would call. |

```python
# The LLM emits, e.g.:
delegate(target="bob", prompt="What's the right way to model X?", capability="modeling")
say(content="Here's my answer: …")
```

The framework resolves `ChannelInject` (current channel), `ChannelStateInject`, and `AgentClientInject` automatically inside the notify handler — the LLM never sees those parameters.

### Four grouped action-dispatch tools

Each takes an `action` literal plus action-specific args, keeping the LLM's tool list short.

**`peers(action)` — discovery**

| Action | Args | Returns |
|---|---|---|
| `"find"` | `query?, capability?, sort_by?, limit=20` | List of peer summaries (excludes the caller). |
| `"describe"` | `name` | One peer's full profile: `{passport, resume, skill_md}`. `skill_md` falls back to a rendered passport+resume when no `SKILL.md` is registered. |

**`channels(action)` — lifecycle**

| Action | Args | Returns |
|---|---|---|
| `"list"` | `state="active"\|"all"` | Channels this agent participates in. |
| `"open"` | `type, target, knobs?, intent?, ttl?, message?` | Mirrors `agent_client.open`. If `message` is given, it's sent as the first envelope on the initiator's behalf right after the channel reaches OPENED (handy for `consulting`/`workflow` seeding); a failed seed closes the channel with reason `seed_failed`. Returns `{channel_id, type, participants[, seed_envelope_id]}`. |
| `"info"` | `channel_id` | Full `ChannelMetadata` if the agent participates. |
| `"close"` | `channel_id?` (defaults to current) | Closes with reason `"closed_by_agent"`. |

**`tasks(action)` — task lifecycle**

Two halves: *active actions* (the agent is inside its own `agent.task(...)` block) and *observation actions* (any task the hub has seen).

| Action | Half | Args | Returns |
|---|---|---|---|
| `"progress"` | active | `payload` | Emits `TaskProgress`. |
| `"complete"` | active | `result?` | Terminal — emits `TaskCompleted`. |
| `"list"` | observation | `scope="own"\|"all", state="active"\|"all", limit=20` | Task summaries. |
| `"status"` | observation | `task_id` | Refreshed `TaskMetadata`. |
| `"wait"` | observation | `task_id, timeout=300, poll_interval=0.1` | Blocks until terminal. |

`"start"` is intentionally **not** a tool — calling it from the LLM would bypass the `async with agent.task(...)` lifecycle that scopes `TaskInject` correctly. Owners start tasks in their own code; the LLM uses `progress` / `complete` once a task is active, and `delegate` for one-shot remote work.

**`context(action)` — past content**

| Action | Args | Returns |
|---|---|---|
| `"search"` | `query, scope="channel"\|"knowledge", limit=10` | Excerpts whose text matches `query` (case-insensitive substring). |
| `"quote"` | `speaker, recent_n=1, channel_id?` | The last `recent_n` `EV_TEXT` envelopes from `speaker`. |

`scope="knowledge"` reaches into the calling agent's own `KnowledgeStore` (substring search only — for vector / semantic search, the agent's own loop calls framework-core `recall` directly).

### Adapter-owned tools (`tools_for`) — and the `say` double-send

The default handler resolves `adapter.tools_for(client, metadata, state, participant_id)` each turn and merges the result into `agent.ask(tools=...)` (alongside the agent's own `@tool`s and any plugin tools). Adapters that take no LLM input — `workflow` — return `[]`. The others return `say`, gated by turn state (table above). An adapter could return richer tools too; `say` is just the only built-in.

Implication for `workflow`: a workflow agent **never sees `say`** — its only path to "say something" is the round-end `EV_PACKET` the handler builds from `reply.body`, plus whatever `@tool` handoff functions you wrote. The old "`approve()` then `say(...)` races the round-end `EV_PACKET`" failure mode is structurally impossible now.

For `consulting` / `conversation` / `discussion`, `say` *is* offered — but you rarely need it. The default handler already posts the round-end envelope (`build_round_envelope` → `EV_TEXT(reply.body)`, or `None` if empty), so an agent that just replies with text has already spoken. `say` is for posting an **extra** message in a turn, or posting into a **different** channel the agent participates in (`channel_id=`).

The hazard: an agent that calls `say(content="…")` **and then** also returns a non-empty reply body emits *two* substantive envelopes in one turn. On `conversation` that's harmless. On `consulting` the second one trips the strict 1Q1R adapter — `ProtocolError: channel '<id>' is closed`. Mitigations, most robust first:

- **Don't prompt the agent to call `say`.** Its plain reply is the canonical channel reply; the double-send only happens if you explicitly steer it toward `say`.
- **Replace the default handler** with one that *doesn't* also send a round-end envelope — then `say` is the agent's voice (gateway / headless-worker patterns below).
- `attach_plugin=False` does **not** help here — `say` is adapter-owned, not plugin-owned. It only drops `delegate` / `peers` / `channels` / `tasks` / `context`. Use it for a bare agent, not as a `say` suppressor.

## Non-LLM participants — `HumanClient`

`HumanClient` is the framework's first-class "participant that isn't an `Agent`": no LLM, no `NetworkPlugin`, no assembly policies. A person at a UI, a bridge to another system, a scripted "user" that seeds a workflow — all of these are a `HumanClient`. Register with `register_human` (not `register`, which now rejects `kind="human"` and points you here):

```python
from autogen.beta.network import HumanClient, Passport

user = await hc.register_human(Passport(name="user", kind="human"))   # resume=, rule=, auto_ack_invites= optional
```

It satisfies the `NetworkClient` Protocol — same outbound surface as `AgentClient`:

```python
ch = await user.open(type="consulting", target="analyst")     # initiate a channel
await user.send(ch.channel_id, "What's our Q3 exposure?")     # EV_TEXT convenience
await user.post_envelope(env)                                 # escape hatch — adapter-shaped envelopes (e.g. workflow EV_PACKET via adapter.build_packet_envelope)
```

…plus two ways to consume inbound envelopes (an `AgentClient` only has the notify-handler callback; `HumanClient` adds an explicit queue):

| Surface | Call | Semantics |
|---|---|---|
| **Push** | `user.on_envelope(callback)` | `async` callback fires once per inbound envelope; multiple callbacks compose in registration order; a raising callback is logged, never propagated. `remove_envelope_callback(cb)` to detach. |
| **Pull** | `await user.next_envelope(*, predicate=None, timeout=None)` | Blocks until the next envelope matching `predicate` (or any, if `None`); raises on `timeout`. |
| **Pull (stream)** | `async for env in user.envelopes(): ...` | Yields every inbound envelope until `user.disconnect()`. |

`auto_ack_invites=True` (default) makes the human auto-accept channel invites so the hub's quorum handshake completes without a UI round-trip; pass `auto_ack_invites=False` to gate joins by hand (you'll then `post_envelope` an `EV_CHANNEL_INVITE_ACK` yourself). `hub.list_agents(kind="human")` discovers humans; `hub.list_agents(kind="agent")` / `kind="remote_agent"` filter the others.

Typical roles: the kickoff seeder for a `workflow` channel (`FromSpeaker(user) → AgentTarget(first_agent)` — see `ag2-network-workflow`), the human leg of a `consulting` Q&A, a participant in a `discussion` round-robin, or a WebSocket/CLI bridge in front of any of those. For a *headless agent* (an `AgentClient` that shouldn't run an LLM but still wants the plugin tools / `tools_for` resolution) you replace its notify handler instead — next.

## Replacing the default handler

The default handler does all the "agent receives envelope → auto-ack invites → run LLM (with `adapter.tools_for(...)` merged in) → post round-end envelope" wiring. Replace it for headless workers, gateways, or any agent that shouldn't run an LLM. (For a participant that was never meant to have an LLM at all, reach for `HumanClient` above instead of an `AgentClient` with a custom handler.)

### Opting out of the plugin (and/or the default handler)

```python
worker = await hc.register(agent, passport, resume, attach_plugin=False)  # no peers/channels/tasks/context/delegate
worker.on_envelope(my_custom_handler)                                     # ← this is what swaps the handler
```

These are two independent knobs:

- `attach_plugin=False` skips `NetworkPlugin.register(agent)` — the agent's tool list loses `peers` / `channels` / `tasks` / `context` / `delegate` (and the `NetworkContextPolicy` prefix). It does **not** touch the notify handler, and it does **not** remove `say` (that's adapter-owned — see above).
- The default notify handler is active regardless (it's wired by `AgentClient`, not the plugin). Call `client.on_envelope(callback)` to replace it; `client.remove_envelope_handler()` (or passing `client._run_default_handler` back) restores it.

**What you lose when you call `client.on_envelope(callback)`:**

| The default handler does… | If you don't replicate it… |
|---|---|
| Auto-ack `EV_CHANNEL_INVITE` (post `EV_CHANNEL_INVITE_ACK`) | the channel sits in `INVITED` until `invite_ack_timeout` (30s) and the hub closes it on you |
| Run `_process_substantive` on `EV_TEXT` / `EV_PACKET`: read WAL, project the view, stamp dependencies, attach `TaskMirror`, resolve `adapter.tools_for(...)`, call `agent.ask(...)`, post the round-end envelope built by `adapter.build_round_envelope` | the LLM never runs, no reply ever goes back, the channel stalls until an expectation fires |
| No-op on `ag2.channel.*` / `ag2.task.*` lifecycle envelopes | (harmless to skip, but easy to be surprised when these arrive) |

The cheap way to keep most of that for free: **handle the events you care about yourself, delegate the rest to `default_handler`.**

> **Handler signature.** A callback passed to `client.on_envelope(...)` is called with just `(envelope,)` — the framework does `await self._on_envelope(envelope)`. Any `client` reference inside the handler is captured from the enclosing scope (a closure variable). The exported `default_handler`, by contrast, has signature `(envelope, client)` — pass both when delegating.

### A gateway handler

```python
from autogen.beta.network import Envelope, EV_TEXT, default_handler


async def gateway_handler(envelope: Envelope) -> None:
    """Forward inbound text to an external system instead of running an LLM."""
    if envelope.event_type != EV_TEXT:
        await default_handler(envelope, client)   # auto-ack invites etc. (client from closure)
        return
    text = envelope.event_data.get("text", "")
    await my_external_queue.put({
        "from": envelope.sender_id,
        "text": text,
        "channel": envelope.channel_id,
    })


client.on_envelope(gateway_handler)
```

### Selective override (fall back to default)

```python
from autogen.beta.network import default_handler, EV_CHANNEL_INVITE


async def selective_handler(envelope: Envelope) -> None:
    if envelope.event_type == EV_CHANNEL_INVITE and envelope.sender_id not in TRUSTED_AGENTS:
        return   # untrusted invite → silent drop; hub will time out and close
    await default_handler(envelope, client)   # default for everything else


client.on_envelope(selective_handler)
```

### Filtered forwarding (pre/post hooks)

```python
async def logged_handler(envelope: Envelope) -> None:
    log.info("inbound %s from %s", envelope.event_type, envelope.sender_id)
    try:
        await default_handler(envelope, client)
    finally:
        log.info("processed %s", envelope.envelope_id)
```

### Bypassing adapter tools

Adapters offer per-turn tools via `tools_for(...)` (notably `say` on `consulting` / `conversation` / `discussion`); the default handler merges them into `agent.ask(tools=...)`. If a capable model insists on calling `say` unprompted (Claude often will) and the resulting double-send is breaking your channel, swap in a custom handler that runs the same substantive path but **omits `tools=` from `agent.ask`** — the agent's static `agent.tools` (your `@tool`s + any plugin tools) remain, since `ask(tools=…)` is *additional* tools, not a replacement. The shape:

```python
async def no_say_handler(envelope, client):
    if envelope.event_type != EV_TEXT:
        await default_handler(envelope, client)         # invite-ack + lifecycle for free
        return
    if not client._hub_client.can_send(envelope.channel_id, client.agent_id):
        return                                          # sync — not our turn

    # ... read_wal_until / resolve_view_policy / stamp_dependencies (hooks above) ...
    reply = await client.agent.ask(current_input, stream=stream, dependencies=deps)   # ← no tools=
    out = adapter.build_round_envelope(metadata=meta, sender_id=client.agent_id, reply=reply,
                                       events=events, state=state, hub=client._hub)
    if out is not None:
        out.causation_id = envelope.envelope_id
        await client.send_envelope(out)
```

That's the default handler's substantive path minus one line. Same trick works to add tools (pass your own `tools=[...]`) or to swap the view, the dependencies, the round-envelope shape, etc.

### Hooks for selective override

If you want to *partially* replace the default handler's logic, the handler is decomposed into public hooks:

```python
from autogen.beta.network import (
    read_wal_until,
    resolve_view_policy,
    stamp_dependencies,
)
```

| Hook | Purpose |
|---|---|
| `read_wal_until(client, envelope)` | Slice the WAL up to (excluding) the given envelope. |
| `resolve_view_policy(client, metadata)` | The `ViewPolicy` this participant should use. |
| `stamp_dependencies(client, channel)` | Build the `context.dependencies` dict for the LLM turn (`CHANNEL_DEP`, `AGENT_CLIENT_DEP`, `HUB_DEP`, `TASK_DEP`). |

Use these when your custom handler wants the standard pre-LLM wiring but a custom post-LLM behaviour (or vice versa).

## Views — what each LLM sees

`ViewPolicy` is the projection layer between the channel's WAL and the LLM's history:

```python
class ViewPolicy(Protocol):
    name: ClassVar[str]
    async def project(
        self,
        history: list[Envelope],
        *,
        participant_id: str,
        channel: ChannelMetadata,
    ) -> list[BaseEvent]: ...
```

It takes the WAL up to the current envelope and returns a list of `BaseEvent`s that the framework feeds into the LLM turn as pre-populated stream history. Adapters declare a default; you can override per-channel.

### Built-in views

| View | Behaviour | Default for |
|---|---|---|
| `FullTranscript()` | Every `EV_TEXT` / `EV_HANDOFF` envelope, in order, no filtering beyond audience. | `consulting` |
| `WindowedSummary(recent_n=N)` | The last `N` text envelopes. If the WAL is longer, prepends a `CompactionSummary` placeholder with the count of elided turns. | `conversation`, `discussion`, `workflow` |

Both honour `audience` — an envelope addressed only to `[bob]` doesn't appear in `carol`'s projection.

```python
from autogen.beta.network import FullTranscript, WindowedSummary

view = WindowedSummary(recent_n=12)
projected = await view.project(
    history=wal_slice,
    participant_id=carol.agent_id,
    channel=metadata,
)
```

### Resolving the default

```python
from autogen.beta.network import resolve_view_policy

policy = resolve_view_policy(client, metadata)
```

Reads the adapter manifest's `default_view_policy` and instantiates the matching view from the registry. The default handler calls this once per turn — custom handlers should too unless they're deliberately bypassing the standard projection model.

### Custom views

Implement the protocol, give it a unique `name`, and use it:

```python
from typing import ClassVar
from autogen.beta.events import BaseEvent, ModelMessage, ModelRequest
from autogen.beta.network import Envelope, EV_TEXT, ChannelMetadata, ViewPolicy


class FromOneOnly(ViewPolicy):
    """Show only envelopes from a single named sender."""
    name: ClassVar[str] = "from_one_only"

    def __init__(self, sender_id: str) -> None:
        self.sender_id = sender_id

    async def project(self, history, *, participant_id, channel):
        out: list[BaseEvent] = []
        for env in history:
            if env.event_type != EV_TEXT or env.sender_id != self.sender_id:
                continue
            text = env.event_data.get("text", "")
            out.append(
                ModelMessage(text) if env.sender_id == participant_id
                else ModelRequest(text)
            )
        return out
```

The `BaseEvent` types you emit shape how the LLM sees the history: `ModelRequest` for "from the user," `ModelMessage` for "from the assistant," etc. See `autogen.beta.events` for the full taxonomy.

### Picking a view

- **Short, focused exchanges** → `FullTranscript()`. Token budget isn't the bottleneck; coherence is.
- **Long-running discussions** → `WindowedSummary(recent_n=N)` with `N` tuned to participant count and turn density.
- **Specialist agents that should ignore unrelated chatter** → custom view that filters by audience or tags.
- **Privacy-sensitive workflows** → custom view that strips fields or redacts before projection.

Switching the view doesn't affect the WAL — every envelope is still there, every operator can still inspect it. Only the LLM's perception of history is shaped.

## Skills — how an agent describes itself

Skills are markdown-with-frontmatter that the hub stores verbatim and surfaces to peers during `peers(action="describe", name=...)` lookup. Pass at registration:

```python
agent_client = await hc.register(
    agent,
    Passport(name="researcher"),
    Resume(claimed_capabilities=["research"]),
    skill_md="""\
---
title: Research Assistant
expertise: [policy, finance]
---

# Researcher

A senior policy analyst. Best at:

- Scenario synthesis from multi-source briefs.
- Rebuttal review with confidence scores.

Limitations: not for code review or numerical analysis.
""",
)
```

### Parsing the frontmatter

```python
from autogen.beta.network import parse_skill_frontmatter, ParsedSkill

parsed: ParsedSkill = parse_skill_frontmatter(skill_md)
print(parsed.frontmatter)  # {"title": "Research Assistant", "expertise": [...]}
print(parsed.body)         # the markdown body
```

### Fallback skills

When no `skill_md` is provided, the hub generates one from the resume so peer lookup doesn't return empty handles:

```python
from autogen.beta.network import render_fallback_skill

skill_md = render_fallback_skill(passport, resume)
```

Useful when constructing skills programmatically — e.g. a tenant uploads a resume but no markdown.

### Updating after registration

```python
await hub.set_skill(agent_id, new_skill_md)
```

Emits `AUDIT_KIND_SKILL_SET`. Same audit shape as `set_resume`; tenant code can replace skills at any time.

## The Envelope wire format

```python
@dataclass(slots=True)
class Envelope:
    envelope_id: str            # hub-stamped UUID
    channel_id: str
    sender_id: str              # agent_id
    audience: list[str] | None  # None = broadcast to all participants
    event_type: str             # "ag2.msg.text", "ag2.channel.invite", etc.
    event_data: dict            # event-specific payload
    causation_id: str | None = None  # envelope_id this one is "in reply to"
    priority: Priority = Priority.NORMAL
    created_at: str = ""        # hub-stamped ISO-Z
    sequence: int = 0           # hub-stamped per-channel monotonic counter
```

The hub stamps `envelope_id`, `created_at`, and `sequence` at admission time. Everything else comes from the sender.

### Substantive events

| Constant | String | `event_data` |
|---|---|---|
| `EV_TEXT` | `"ag2.msg.text"` | `{"text": "<body>"}` |
| `EV_PACKET` | `"ag2.packet"` | `{"routing": {...}, "context_updates": {...}, "body": "<text>"}` |

`EV_TEXT` carries plain text. `EV_PACKET` is the workflow adapter's atomic round-end capture — routing decision (matched against `ToolCalled` rules), accumulated `context_vars` mutations, and final body text bundled into one envelope. Posted by the framework after each `Agent.ask` round on a workflow channel; tool authors don't construct these directly.

### Channel lifecycle events

| Constant | When |
|---|---|
| `EV_CHANNEL_INVITE` | Hub posts to each `target` when a channel is created. |
| `EV_CHANNEL_INVITE_ACK` | Each invitee posts when accepting. |
| `EV_CHANNEL_INVITE_REJECT` | Optional — invitee rejects (default handler doesn't, but you can override). |
| `EV_CHANNEL_OPENED` | Hub posts when all acks land. |
| `EV_CHANNEL_CLOSED` | Hub posts on any termination path; `event_data.reason` carries why. |
| `EV_CHANNEL_EXPIRED` | Hub posts when TTL sweeper closes the channel. |
| `EV_EXPECTATION_VIOLATED` | Hub posts when an evaluator's threshold is breached and the handler is `notify`. |

### Context and task events

| Constant | When |
|---|---|
| `EV_CONTEXT_SET` | Tool/participant emits to mutate `WorkflowState.context_vars`. |
| `"ag2.task.started"` | Mirrored from `TaskStarted`. |
| `"ag2.task.progress"` | Mirrored from `TaskProgress`. |
| `"ag2.task.completed"` | Mirrored from `TaskCompleted`. |
| `"ag2.task.failed"` | Mirrored from `TaskFailed`. |
| `"ag2.task.expired"` | Mirrored from `TaskExpired`. |

### Audience and visibility

`audience: list[str] | None` controls who sees the envelope:

- `None` — broadcast to all participants.
- `[agent_id_1, agent_id_2, ...]` — only those participants see it.

```python
from autogen.beta.network import visible_to

if visible_to(env, my_agent_id):
    process(env)
```

Views (`FullTranscript`, `WindowedSummary`) honour audience — an envelope addressed only to `[bob]` doesn't appear in `carol`'s projection.

### Priority

```python
class Priority(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3
```

Higher priority envelopes process ahead of lower in queue order. Use sparingly; most application envelopes leave `priority` at `NORMAL`.

### Causation

`causation_id` marks an envelope as "in reply to" another:

```python
await channel.send(reply_text, causation_id=incoming_envelope.envelope_id)
```

The default handler does this automatically when replying to an inbound `EV_TEXT`. Custom handlers should set it for logical replies — useful for threaded views.

## Sending raw envelopes (custom event types)

`channel.send(text, audience=...)` wraps `EV_TEXT` for you. For custom event types build an `Envelope` and post it directly:

```python
from autogen.beta.network import Envelope

envelope = Envelope(
    channel_id=channel.channel_id,
    sender_id=alice.agent_id,
    audience=[bob.agent_id],
    event_type="myapp.review_request",
    event_data={"document_id": "doc-123", "kind": "security"},
)
await alice.send_envelope(envelope)
```

The hub doesn't validate `event_type` against any allowlist — custom types pass through unmodified. Adapters fold only the event types they recognise (substantive ones, plus `EV_PACKET` under workflow, plus lifecycle ones); custom event types land on the WAL and are delivered to the audience but don't advance turn-taking state.

### Custom event type guidelines

1. Use a **dotted namespace prefix** (`"myapp.review_request"`, not `"review"`) to avoid collision with future `ag2.*` events.
2. Keep `event_data` **JSON-serialisable** (no datetimes, dataclasses, etc.) so it round-trips through the store cleanly.
3. If multiple participants should react, set `audience=None`. If only one, address it specifically; views filter out non-recipients.
4. **Don't rely on adapters to do anything** with custom types — they pass through. Your custom handler is responsible for processing them.

## Reading the WAL

```python
wal = await hub.read_wal(channel.channel_id)
for env in wal:
    print(f"{env.sequence:>3}  {env.event_type}  from={env.sender_id[:8]}")
```

Envelopes appear in admission order. The WAL is the canonical replay surface — `Hub.hydrate()` re-folds it through each adapter to rebuild in-memory state on restart.

## Quick reference — imports

```python
from autogen.beta.network import (
    # Default handler + hooks (for selective override)
    default_handler,
    read_wal_until,
    resolve_view_policy,
    stamp_dependencies,
    # Plugin (auto-attached by attach_plugin=True) — peers/channels/tasks/context/delegate.
    # `say` is NOT here; it comes from the channel adapter's tools_for(...).
    NetworkPlugin,
    # Non-LLM participant
    HumanClient,           # via HubClient.register_human(Passport(name=..., kind="human"))
    PassportKind,          # Literal["agent", "human", "remote_agent"] | None
    # Views
    ViewPolicy,
    FullTranscript,
    WindowedSummary,
    # Skills (peer discovery)
    ParsedSkill,
    parse_skill_frontmatter,
    render_fallback_skill,
    # Envelopes + events
    Envelope,
    Priority,
    visible_to,
    EV_TEXT,
    EV_PACKET,
    EV_CHANNEL_INVITE,
    EV_CHANNEL_INVITE_ACK,
    EV_CHANNEL_INVITE_REJECT,
    EV_CHANNEL_OPENED,
    EV_CHANNEL_CLOSED,
    EV_CHANNEL_EXPIRED,
    EV_EXPECTATION_VIOLATED,
    EV_CONTEXT_SET,
    # Dependency injection keys (for tests outside the notify handler)
    AGENT_CLIENT_DEP,
    CHANNEL_DEP,
    CHANNEL_STATE_DEP,
    HUB_DEP,
    TASK_DEP,
    # Injects (for tool signatures)
    AgentClientInject,
    ChannelInject,
    ChannelStateInject,
    HubInject,
    TaskInject,
)
```
