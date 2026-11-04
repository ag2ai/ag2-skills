# Network quickstart — channels, lifecycle, identity

Depth for `ag2-network-quickstart`. Verified against ag2 1.3.0 (`ag2/network/adapters/`, `ag2/network/client/`, `ag2/network/hub/core.py`).

## Contents

- Mental model
- Adapters at a glance (views, expectations, `say`)
- `consulting` — strict 1Q1R
- `conversation` — free-form 2-party
- Plugin tools vs adapter tools (`attach_plugin`, `say`)
- `HumanClient`
- Identity — `Passport` and `Resume`
- Channel lifecycle
- Sending envelopes
- The five close routes
- Replay and inspection
- Imports

## Mental model

```
                 ┌────────────────┐
                 │      Hub       │  audit log, registry, channels,
                 │ adapters, WAL  │  sweepers, expectation evaluators
                 └───────┬────────┘
                         │ LocalLink (in-process) / WebSocket
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
  AgentClient       AgentClient       HumanClient
  (alice Agent)     (bob Agent)       (UI / seeder)
```

One `HubClient` per process boundary (the examples use one per agent for clarity). The hub stamps `agent_id` at registration — use it for routing.

## Adapters at a glance

| Adapter | Default view | Default expectations | Offers `say`? |
|---|---|---|---|
| `consulting` | `FullTranscript()` | `acks_within(30s, auto_close)`, `reply_within(600s, auto_close)` | to whoever holds the 1Q1R floor |
| `conversation` | `WindowedSummary(recent_n=10)` | `max_silence(3600s, audit)` | always |
| `discussion` | `NamedWindowedSummary(recent_n=max(2N, 4))` | `turn_within(120s, warn)`, `turn_within(600s, hide)` | never |
| `workflow` | `NamedWindowedSummary(recent_n=max(2N, 4))` | `turn_within(120s, warn)`, `turn_within(600s, auto_close)` | never |

`turn_within` is declared but inert by default: `Hub.open` ships evaluators only for `acks_within` / `reply_within` / `max_silence` and handlers only for `audit` / `notify_channel` / `auto_close`. Tune or add expectations via `ag2-network-governance`; views via `ag2-network-tools-and-views`.

## `consulting` — strict 1Q1R

Initiator sends one substantive envelope, respondent replies once, adapter auto-closes with `"consulting_complete"`.

```python
@dataclass(slots=True)
class ConsultingState:
    initiator_sent: bool = False
    respondent_replied: bool = False
```

`validate_send` rejects out-of-order sends (respondent first) and any send after both flags are set (`ProtocolError` back to `channel.send`). If the respondent doesn't ack within 30s or reply within 10 min the hub closes with `"expectation_violated:acks_within"` / `"expectation_violated:reply_within"`.

## `conversation` — free-form 2-party

```python
channel = await alice.open(type="conversation", target="bob")
await channel.send("Hi bob, what's a good first ML concept to learn?")
```

Either side may send any time; `validate_send` only checks participation. Never auto-closes. Halt patterns:

1. **App-side cap** — poll `hub.read_wal` until N `EV_TEXT`, then `await channel.close(reason="cap_reached")`.
2. **Agent close tool** — `ChannelInject` tool (below). Executed with gpt-4.1-mini: works, but models that also call `say` can produce many more messages than "turns" you asked for — prefer an app-side cap as the hard bound.
3. **Empty reply** — the default handler doesn't send an empty body; prompt "reply with an empty string when you have nothing to add".

Default expectation `max_silence(3600s, audit)` only logs `AUDIT_KIND_EXPECTATION_VIOLATED`.

Use for analyst ↔ critic exchanges or chat UIs where your app owns the stop signal; not for strict 1Q1R (`consulting`) or 3+ parties (`discussion` / `workflow`).

## Plugin tools vs adapter tools (`attach_plugin`, `say`)

`HubClient.register(...)` attaches `NetworkPlugin` by default: the identity-level tools `peers` / `channels` / `tasks` / `context` / `delegate`. Channel-specific tools come from `adapter.tools_for(...)` each turn, regardless of `attach_plugin` — see the `say` column above. `attach_plugin=False` therefore means "no `delegate` / `peers` / ...", not "no `say`".

You rarely need `say`: the default handler posts the agent's plain reply as the round-end envelope. `say` is for an *extra* message in a turn or posting into another channel. Hazard: on `consulting`, an agent that calls `say(...)` **and** returns a non-empty reply double-sends; the second envelope fails with `channel is closed`. Full detail: `ag2-network-tools-and-views`.

## `HumanClient`

```python
from ag2.network import HumanClient, Passport

user = await user_hc.register_human(Passport(name="user", kind="human"))
# register_human(passport, *, resume=None, rule=None, auto_ack_invites=True)
```

- Outbound (same `NetworkClient` surface as `AgentClient`): `user.open(type=..., target=...)`, `user.send(channel_id, text, audience=...)`, `user.post_envelope(env)` (adapter-shaped envelopes, e.g. a workflow `EV_PACKET`), `user.wait_for_channel_event(...)`.
- Inbound push: `user.on_envelope(callback)` — fires per envelope; callbacks compose; a raising callback is logged, not propagated.
- Inbound pull: `await user.next_envelope(predicate=..., timeout=...)` (non-matching envelopes are discarded; raises `TimeoutError`); `async for env in user.envelopes(): ...` until `user.disconnect()`.
- `auto_ack_invites=False` to gate joins yourself. Discover humans: `await hub.list_agents(kind="human")`.
- `HubClient.register(...)` with `kind="human"` raises `ValueError`.

Uses: kickoff seeder for `workflow`, human leg of `consulting`, a seat in a `discussion` (gate on `await hc.can_send(channel_id, user.agent_id)`).

## Identity — `Passport` and `Resume`

```python
from ag2.network import Passport, Resume, ResumeExample

passport = Passport(
    name="alice",              # required, unique within the hub
    owner="acme",              # optional tenant id
    model="claude-sonnet-5",   # optional, surfaces on peer lookup
    kind="agent",              # PassportKind: None/"agent" | "human" | "remote_agent"
)
resume = Resume(
    claimed_capabilities=["analysis", "policy"],
    domains=["finance"],
    summary="Senior policy analyst.",
    examples=[ResumeExample(title="Q3 risk brief", note="...")],
)
```

Hub stamps `agent_id` and `created_at`. `Resume.observed` (per-capability `ObservedStat`) is hub-mutated when the agent runs capability-tagged work inside a network turn — `agent.ask(..., capability="x")` or `Task(spec=TaskSpec(capability="x"), ...)`; see `ag2-network-governance`. Minimal: `Passport(name="alice")`, `Resume()`.

## Channel lifecycle

```
open(type=..., target=...)
   │
PENDING ── all targets ack ──→ ACTIVE ── adapter terminates ──────────→ CLOSED
   │                              └─ channel.close() / TTL / expectation → CLOSING → CLOSED
   └─ ack timeout (invite_ack_timeout=30s) → CLOSED ("invite_timeout"; open raises ProtocolError)
```

## Sending envelopes

`await channel.send(text, audience=[bob.agent_id])` — `audience=None` means all participants (and may only name participants). The hub stamps `envelope_id`, `sender_id`, `created_at` and writes the WAL. Custom event types: `await agent_client.send_envelope(envelope)` / `channel.send("", event_type=..., event_data=...)`. Envelope shape and `EV_*` constants: `ag2-network-tools-and-views`.

## The five close routes

All produce one `EV_CHANNEL_CLOSED` (`event_data["reason"]`, also `ChannelMetadata.close_reason`).

| Route | Who decides | Best for |
|---|---|---|
| `channel.close(reason=...)` | your code | turn/time caps, predicates |
| Agent tool with `ChannelInject` | the LLM | "we're done" |
| Adapter subclass sentinel | framework | content stop ("TERMINATE") |
| Workflow `TerminateTarget` / `Finish` | graph / tool | orchestrations |
| TTL / expectations | hub sweepers | safety nets |

| | Auto-close | App close | Agent tool | Sentinel | Graph |
|---|---|---|---|---|---|
| `consulting` | after reply | early bail | yes | subclass | n/a |
| `conversation` | never | typical | yes | canonical | n/a |
| `discussion` | never | typical | yes | subclass | n/a |
| `workflow` | graph | override | yes (`Finish` / `ToolCalled`) | n/a | canonical |

Prefer a close tool over a "TERMINATE" sentinel — typed, traceable on the WAL, injection-resistant. `ChannelInject` resolves to the active `Channel` during a network turn and to `None` elsewhere (import from `ag2.network` or `ag2.network.client.inject`). After a tool closes the channel, the default handler's round-end reply is rejected and logged; harmless.

Watch for close:

```python
close_env = await alice.wait_for_channel_event(
    channel_id=channel.channel_id,
    predicate=lambda e: e.event_type == EV_CHANNEL_CLOSED,
    timeout=180.0,
)
```

## Replay and inspection

```python
for env in await hub.read_wal(channel.channel_id):
    print(env.created_at, env.event_type, hub.name_for(env.sender_id), env.event_data)
```

`EV_TEXT` → `event_data["text"]`; workflow `EV_PACKET` → `event_data["body"]` + `routing`. Hub-level events (registered, channel created/closed, expectation violated): `await hub.audit_log.read_all()` — see `ag2-network-governance`.

## Imports

```python
from ag2.network import (
    Hub, HubClient, LocalLink,
    HumanClient,
    Passport, PassportKind, Resume, ResumeExample,
    Envelope, EV_TEXT, EV_PACKET, EV_CHANNEL_INVITE, EV_CHANNEL_INVITE_ACK,
    EV_CHANNEL_OPENED, EV_CHANNEL_CLOSED, EV_CHANNEL_EXPIRED, Priority,
    ChannelInject,
    AccessDeniedError, InboxFull, ProtocolError,
)
from ag2.knowledge import MemoryKnowledgeStore  # SqliteKnowledgeStore / DiskKnowledgeStore for durability
```
