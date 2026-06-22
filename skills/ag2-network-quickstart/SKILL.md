---
name: ag2-network-quickstart
description: Build a multi-agent AG2 beta network — load this whenever two or more `Agent`s need to interact. The network is the standard multi-agent pattern in `autogen.beta`. Covers `Hub.open`, `LocalLink`, `HubClient.register`, `Passport`, `Resume`, channel lifecycle (PENDING → ACTIVE → CLOSING → CLOSED), the two 2-party channel adapters (`consulting` for strict 1Q1R and `conversation` for free-form), the `Envelope` wire format, audience routing, `wait_for_channel_event`, WAL replay via `hub.read_wal`, and the five channel-close routes. Entry point — routes to `ag2-network-discussion` (N-party round-robin), `ag2-network-workflow` (declarative orchestration / GroupChat migration), `ag2-network-governance` (rules / expectations / audit), or `ag2-network-tools-and-views` (custom handlers / peer discovery) for deeper needs.
license: Apache-2.0
---

# AG2 Network — Quickstart

The network turns a collection of `Agent` instances into a coordinated multi-agent system. A central `Hub` holds authoritative state (registry, audit log, write-ahead logs per channel); each agent lives behind a thin `AgentClient` that connects through a `HubClient`. Every send goes through the hub, so every interaction is replayable and observable.

The network is **opt-in**. Bare `Agent` continues to work standalone — the network only activates when you import from `autogen.beta.network`.

## When to use

Load this skill whenever the user wants two or more agents to interact. Concrete trigger phrases:

- "Have two agents talk to each other"
- "Set up a multi-agent system / multi-agent chat / agent network"
- "Agents that can call each other"
- "Replace the classic `GroupChat` / `ConversableAgent.handoffs`"
- "Add a registry / audit trail / shared inbox for my agents"

**Not a network task:** if the user is asking *one agent* to spawn its own sub-tasks (recursive task fan-out, parallel sub-task execution), use `ag2-subagent-delegation` instead. The network is for *distinct, registered* agents collaborating.

## Mental model

```
                        ┌────────────────┐
                        │      Hub       │  ←── audit log, registry, channels,
                        │ ── adapters ── │       WAL, sweepers, expectation
                        │ ── channels ── │       evaluators
                        │ ── audit log ──│
                        └────────┬───────┘
                                 │  in-process duplex (LocalLink)
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
        ┌──────────┐       ┌──────────┐       ┌──────────┐
        │AgentClient│      │AgentClient│      │AgentClient│
        │  alice    │      │   bob     │      │   carol   │
        │  Agent    │      │  Agent    │      │  Agent    │
        └──────────┘       └──────────┘       └──────────┘
```

| Concept | Lives in | Purpose |
|---|---|---|
| `Hub` | One per network | Authoritative state — registry, audit log, channel table, WAL, sweepers |
| `Passport` | Hub registry | Stable identity (`name`, hub-stamped `agent_id`, optional `owner`, `model`) |
| `Resume` | Hub registry | Capability claims plus hub-mutated `observed` track record |
| `HubClient` | One per process | Per-process broker; manages registration |
| `AgentClient` | One per registered Agent | Wraps the Agent; sends envelopes, runs the notify handler |
| `Envelope` | The wire format | Hub-stamped record of one event in one channel |
| `Channel` | Created by `agent_client.open(...)` | A bounded multi-party exchange governed by an adapter |

The hub assigns the `agent_id` at registration. Use it (`alice.agent_id`) for routing rather than the human-readable name.

## The smallest possible network

A single-process hub, two agents, a `consulting` channel (strict 1Q1R, auto-closes after the reply):

```python
import asyncio

from autogen.beta import Agent
from autogen.beta.config import AnthropicConfig
from autogen.beta.knowledge import MemoryKnowledgeStore
from autogen.beta.network import (
    EV_CHANNEL_CLOSED,
    EV_TEXT,
    Hub,
    HubClient,
    LocalLink,
    Passport,
    Resume,
)


async def main() -> None:
    config = AnthropicConfig(model="claude-sonnet-4-6")

    # 1. Boot the hub. MemoryKnowledgeStore for in-process; DiskKnowledgeStore(path)
    #    for durability across restarts.
    hub = await Hub.open(MemoryKnowledgeStore(), ttl_sweep_interval=0)
    link = LocalLink(hub)  # in-process duplex transport

    # 2. One HubClient per process boundary; here we use two for clarity.
    alice_hc = HubClient(link, hub=hub)
    bob_hc = HubClient(link, hub=hub)

    # 3. Register each Agent. The hub stamps the agent_id and returns an AgentClient.
    #    attach_plugin=False gives a bare agent (no peers/channels/tasks/context/
    #    delegate tools) — fine here since alice & bob just send/reply. The `say`
    #    tool is offered per-turn by the adapter, not the plugin (see below).
    alice = await alice_hc.register(
        Agent("alice", prompt="Ask one focused question and stop.", config=config),
        Passport(name="alice"),
        Resume(),
        attach_plugin=False,
    )
    bob = await bob_hc.register(
        Agent("bob", prompt="Answer in one short sentence.", config=config),
        Passport(name="bob"),
        Resume(),
        attach_plugin=False,
    )

    # 4. Open a consulting channel. The adapter posts EV_CHANNEL_INVITE to bob,
    #    bob's default handler auto-acks, channel goes ACTIVE.
    channel = await alice.open(type="consulting", target="bob")
    await channel.send(
        "What's the single most important property of a distributed system?",
        audience=[bob.agent_id],
    )

    # 5. Bob's default handler probes can_send, runs Agent.ask on the inbound
    #    text, sends the reply. ConsultingAdapter sees both flags set and
    #    auto-closes with reason "consulting_complete".
    close_env = await alice.wait_for_channel_event(
        channel_id=channel.channel_id,
        predicate=lambda e: e.event_type == EV_CHANNEL_CLOSED,
        timeout=60.0,
    )
    print(f"closed: {close_env.event_data.get('reason')!r}")

    # 6. Replay the conversation from the hub's WAL.
    wal = await hub.read_wal(channel.channel_id)
    for env in wal:
        if env.event_type == EV_TEXT:
            speaker = "alice" if env.sender_id == alice.agent_id else "bob"
            print(f"{speaker}: {env.event_data['text']}")

    await alice_hc.close()
    await bob_hc.close()
    await hub.close()


asyncio.run(main())
```

That's the entire mandatory surface. Everything else below is variation.

## Picking a channel adapter

Every channel is governed by an adapter that defines participants, turn order, and termination. Four adapters ship; pick one when you call `agent_client.open(type=..., target=...)`.

| Adapter | Participants | Turn order | Termination | Default view |
|---|---|---|---|---|
| `consulting` | Exactly 2 | Strict 1Q1R (initiator → respondent) | Auto-closes after respondent's reply | `FullTranscript()` |
| `conversation` | Exactly 2 | Free-form (either side, any time) | Explicit `channel.close()` or TTL | `WindowedSummary(recent_n=10)` |
| `discussion` | 2+ | Round-robin via `ordering="round_robin"` | Explicit close or TTL | `WindowedSummary(recent_n=N*2)` |
| `workflow` | 2+ | Declarative `TransitionGraph` | Graph terminates (`TerminateTarget` / `max_turns`) | `WindowedSummary(recent_n=N*2)` |

**This skill covers `consulting` and `conversation` (the 2-party adapters).** For the others:

- N-party round-robin → load **`ag2-network-discussion`**.
- Declarative orchestration with `TransitionGraph`, conditional handoffs, or migrating from classic `GroupChat` → load **`ag2-network-workflow`**.

### Plugin tools vs. adapter tools — `attach_plugin` and `say`

`HubClient.register(...)` attaches `NetworkPlugin` by default. The plugin contributes the *identity-level cross-cutting* tools — `peers` / `channels` / `tasks` / `context` / `delegate` — and nothing channel-specific. Channel-specific tools (notably `say`, which posts a text envelope into the channel) are offered **per turn by the channel's adapter** via `adapter.tools_for(...)`, regardless of `attach_plugin`:

| Adapter | Offers `say`? |
|---|---|
| `consulting` | only to the participant whose turn it is in the 1Q1R |
| `conversation` | always (no turn order) |
| `discussion` | only to `expected_next_speaker` |
| `workflow` | never — routing is your `@tool` handoff functions |

So `attach_plugin=False` no longer means "no `say`" — it means "no `delegate` / `peers` / `channels` / `tasks` / `context`". Pass it for a bare agent (tests, pure pipeline workers); keep the default if the agent needs those cross-cutting verbs.

You normally don't need to think about `say` at all: the default handler always posts the round-end envelope from the agent's plain reply, so an agent that just answers is fine. `say` is for the rare cases of posting an *extra* message in a turn, or posting into a *different* channel the agent is also in. (Edge case to avoid: an agent on a `consulting` channel that calls `say(...)` **and** also returns a non-empty reply double-sends — the second envelope trips `channel is closed` on the strict 1Q1R adapter. If you don't want the agent calling `say`, just don't prompt it to.) Full detail: `ag2-network-tools-and-views` → "Adapter-owned tools (`tools_for`)".

### `consulting` — strict 1Q1R

The example above. Initiator sends exactly one substantive envelope; respondent sends exactly one reply; the adapter auto-closes with `event_data["reason"] == "consulting_complete"`.

State on the hub side:

```python
@dataclass(slots=True)
class ConsultingState:
    initiator_sent: bool = False
    respondent_replied: bool = False
```

`ConsultingAdapter.validate_send` rejects:

- Out-of-order sends (respondent speaking before initiator's first envelope).
- Any send after both flags are set — raises `ProtocolError`, propagated back to the caller's `channel.send(...)`.

Default expectations are strict: `acks_within(30s, auto_close)` and `reply_within(600s, auto_close)`. If the respondent doesn't ack the invite within 30s, or doesn't reply within 10 minutes, the hub auto-closes with `"expectation_violated:acks_within"` or `"expectation_violated:reply_within"`. Tune these via `ag2-network-governance`.

### `conversation` — free-form 2-party

Either side sends at any time, any order. The adapter **never auto-closes**; you halt via `channel.close()` or rely on TTL.

```python
channel = await alice.open(type="conversation", target="bob")
await channel.send("Hi bob, what's a good first ML concept to learn?")
```

Both default handlers run `Agent.ask` on every inbound `EV_TEXT`, so once started the conversation auto-drives. Two halt patterns:

1. **Application-side cap** — poll the WAL until you've seen N text envelopes, then `await channel.close()`.
2. **Empty reply as halt signal** — the default handler treats an empty body as "don't send", so an LLM that replies with `""` ends the chain. Pair with a prompt like `"reply with empty string when you have nothing useful to add"`.

`ConversationAdapter.validate_send` only checks "is the sender a participant?" — same-side sends in a row are allowed because the adapter doesn't model turn order.

Default expectation is lenient: `max_silence(3600s, audit)` (logs to the audit kind `AUDIT_KIND_EXPECTATION_VIOLATED` but does *not* close).

**Use `conversation` when:** two specialists genuinely converse without a fixed order (analyst ↔ critic); chat UIs where your application controls the stop signal.

**Don't use `conversation` for:** strict 1Q1R (use `consulting`); multi-participant chats (use `discussion` or `workflow`).

## A non-LLM participant — `HumanClient`

Not every participant is an `Agent`. `HumanClient` is a network member with no LLM, no `NetworkPlugin`, no assembly policies — a person at a UI, a bridge to another system, or a scripted "user" that seeds a channel. Register it with `register_human` (not `register` — that path now *rejects* `kind="human"` and points you here):

```python
from autogen.beta.network import HumanClient, Passport

user_hc = HubClient(link, hub=hub)
user = await user_hc.register_human(Passport(name="user", kind="human"))   # resume=, rule=, auto_ack_invites= optional
```

It implements the same `NetworkClient` surface as `AgentClient` for *outbound* — `user.open(type=..., target=...)`, `user.send(channel_id, text, audience=...)`, `user.post_envelope(env)` (the escape hatch for adapter-shaped envelopes like a workflow `EV_PACKET`) — and gives you two ways to consume *inbound* envelopes:

- **Push** — `user.on_envelope(callback)`; the coroutine fires once per inbound envelope. Multiple callbacks compose; a raising callback is logged, never propagated.
- **Pull** — `await user.next_envelope(predicate=..., timeout=...)` blocks for the next match; `async for env in user.envelopes(): ...` streams everything until `user.disconnect()`.

It auto-acks channel invites (`auto_ack_invites=True` default) so the hub's join handshake completes without UI round-trips; pass `auto_ack_invites=False` to gate joins yourself. Discover humans via `hub.list_agents(kind="human")`.

Common uses: the kickoff seeder for a `workflow` channel (`FromSpeaker(user) → AgentTarget(first_agent)` — see `ag2-network-workflow`), the human leg of a `consulting` Q&A, or a participant in a `discussion` round-robin. The agent-side surface (custom handlers, headless workers, gateways) is in `ag2-network-tools-and-views`.

## Identity — `Passport` and `Resume`

Three dataclasses describe an agent on the network. Tenant supplies most fields; the hub stamps the rest.

```python
from autogen.beta.network import Passport, Resume, ResumeExample

passport = Passport(
    name="alice",          # required, unique within the hub
    owner="acme",          # optional, tenant id for multi-tenant deployments
    model="claude-sonnet-4-6",  # optional, surfaces on peer-lookup results
    kind="agent",          # optional: "agent" (default / None) | "human" | "remote_agent"
)

resume = Resume(
    claimed_capabilities=["analysis", "policy"],
    domains=["finance"],
    summary="Senior policy analyst — scenario synthesis and rebuttal review.",
    examples=[ResumeExample(title="Q3 risk brief", note="…")],
)
```

The hub stamps `Passport.agent_id` and `Passport.created_at` at registration. `Passport.kind` (type alias `PassportKind`) discriminates participant types — `None`/`"agent"` for the usual LLM-backed `AgentClient`, `"human"` for a `HumanClient` (use `register_human`, not `register`), `"remote_agent"` reserved for A2A/federation. `hub.list_agents(kind=...)` filters by it. The `Resume.observed` field (per-capability `ObservedStat` counts) is hub-mutated as the agent runs capability-tagged tasks — see `ag2-network-governance` for how to wire that up.

For the smallest case you can pass `Passport(name="alice")` and `Resume()` and call it done.

## Channel lifecycle

```
agent_client.open(type=..., target=...)
    │
    ▼
PENDING ──┬─ all targets ack ──→ ACTIVE ──┬─ adapter terminates ──→ CLOSED
          │                                │
          └─ ack timeout ──→ CLOSED       └─ explicit channel.close() ──→ CLOSING ──→ CLOSED
                  (invite_timeout)               or TTL expired
```

The default `invite_ack_timeout` on `Hub.open(...)` is 30s; if any invited target doesn't ack within that window, `create_channel` raises `ProtocolError` and the channel goes straight to CLOSED with reason `"invite_timeout"`. (The separate expectation-sweeper path — the `acks_within` expectation — auto-closes with `"expectation_violated:acks_within"` instead; see the consulting section below.)

## Sending an envelope

```python
await channel.send(text, audience=[bob.agent_id])
```

`audience` controls who sees the envelope (default: all participants). The hub stamps `Envelope.envelope_id`, `sender_id`, `created_at`, and writes it to the WAL.

For custom event types, send raw envelopes via `agent_client.send_envelope(envelope)`. The full `Envelope` shape and the `EV_*` event constants are documented in `ag2-network-tools-and-views`.

## The five channel-close routes

Every channel terminates with an `EV_CHANNEL_CLOSED` envelope. Five routes lead there; pick by *who decides*:

| Pattern | Who decides | Best for |
|---|---|---|
| `channel.close(reason=...)` | Your orchestration code | Custom caps (turn count, time, predicate) |
| Agent-side tool (`ChannelInject`) | The LLM | "Agent decides we're done" |
| Adapter sentinel (subclass) | The framework | Content-based stop ("TERMINATE" keyword) |
| Workflow `TerminateTarget` | A declarative graph | Multi-step orchestrations |
| TTL / expectations | The hub's sweepers | Time- or expectation-based safety nets |

Adapter compatibility:

| | Auto-close | App close | Agent tool | Sentinel | Workflow graph |
|---|---|---|---|---|---|
| `consulting` | Yes (after reply) | Yes (early bail) | Yes | Subclass | n/a |
| `conversation` | Never | Yes (typical) | Yes | Canonical | n/a |
| `discussion` | Never | Yes (typical) | Yes | Subclass | n/a |
| `workflow` | Yes (graph) | Yes (override) | Yes (via `ToolCalled`) | n/a | Canonical |

### Pattern: agent-side close tool

The LLM decides when to stop. Inject the active `Channel` into the tool:

```python
from autogen.beta.network.client.inject import ChannelInject


async def end_conversation(reason: str, channel: ChannelInject) -> str:
    """Close the active channel. The reason flows on EV_CHANNEL_CLOSED."""
    if channel is None:
        return "no active channel"
    await channel.close(reason=f"agent_close:{reason}")
    return f"closed: {reason}"


alice_agent.tool(end_conversation)
bob_agent.tool(end_conversation)
```

The default handler stamps the active `Channel` into `context.dependencies` before each LLM turn, so `ChannelInject` resolves automatically. Outside a network turn it resolves to `None` — the guard above keeps the tool safe in non-network contexts.

Prefer the tool-call pattern over an adapter sentinel ("TERMINATE" keyword) — tool calls are typed, traceable on the WAL, multilingual, and resist prompt injection.

### Watching for close

All five routes produce the same `EV_CHANNEL_CLOSED` envelope, so observers only need one predicate:

```python
close_env = await alice.wait_for_channel_event(
    channel_id=channel.channel_id,
    predicate=lambda e: e.event_type == EV_CHANNEL_CLOSED,
    timeout=180.0,
)
print(f"reason: {close_env.event_data.get('reason')!r}")
```

`ChannelMetadata.close_reason` is also stored on the channel record — `await hub.get_channel(channel_id)` returns the reason without re-reading the WAL.

## Replay and inspection

The hub's WAL is the single source of truth for "what happened in this channel":

```python
wal = await hub.read_wal(channel.channel_id)
for env in wal:
    print(env.created_at, env.event_type, env.sender_id, env.event_data)
```

Every envelope is hub-stamped (`envelope_id`, `created_at`, `sender_id`, `audience`, `event_type`, `event_data`). The WAL is keyed by channel id; reading it works for any channel this process can see.

For the audit log (hub-level events: agent registered, channel created/closed, expectation violated), see `ag2-network-governance`.

## Closing down

```python
await alice_hc.close()
await bob_hc.close()
await hub.close()
```

Always pair `Hub.open(...)` with `hub.close()` (typically in `try/finally`). `hub.close()` cancels the sweeper tasks, closes the underlying store, drains pending I/O. `HubClient.close()` cancels the link's listening task and unsubscribes the clients.

## What to load next

| User goal | Skill |
|---|---|
| N-party round-robin / fixed turn order | `ag2-network-discussion` |
| Declarative orchestration / `TransitionGraph` / GroupChat migration | `ag2-network-workflow` |
| Rate limits, access policy, expectations, audit, capability tracking, swappable arbiter / hub listeners | `ag2-network-governance` |
| Adapter-owned tools (`say` / `tools_for`), plugin tools (`delegate` / `peers` / …), custom handlers, `HumanClient` internals, views, peer discovery | `ag2-network-tools-and-views` |

## Quick reference — imports

```python
from autogen.beta.network import (
    # Hub + transport
    Hub,
    HubClient,
    LocalLink,
    # Participants
    HumanClient,           # register via HubClient.register_human(...)
    # Identity
    Passport,
    PassportKind,          # Literal["agent", "human", "remote_agent"] | None
    Resume,
    ResumeExample,
    # Envelopes + events
    Envelope,
    EV_TEXT,
    EV_CHANNEL_INVITE,
    EV_CHANNEL_INVITE_ACK,
    EV_CHANNEL_OPENED,
    EV_CHANNEL_CLOSED,
    EV_CHANNEL_EXPIRED,
    Priority,
    # Errors
    AccessDeniedError,
    InboxFull,
    ProtocolError,
)
from autogen.beta.knowledge import MemoryKnowledgeStore  # or DiskKnowledgeStore
```
