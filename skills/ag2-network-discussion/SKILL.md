---
name: ag2-network-discussion
description: "Open an AG2 network `discussion` channel \u2014 N-party (2+) round-robin where each participant speaks in fixed order, cycling until explicit close or TTL. Use when the user wants a brainstorm with a fixed cast, a panel discussion, or round-robin reviewers. Covers `agent_client.open(type=\"discussion\", target=[...], knobs={\"ordering\": ORDERING_ROUND_ROBIN})`, the `expected_next_speaker` rotation, the `hc.can_send(...)` probe pattern (default handlers skip LLM calls when it isn't their turn), `DiscussionState`, the `turn_within` expectation defaults (`warn` at 120s / `hide` at 600s), view-window sizing for N participants, and the four close patterns that work with this adapter. Load this after `ag2-network-quickstart`. For conditional handoffs or declarative orchestration, see `ag2-network-workflow` instead."
license: Apache-2.0
---

# AG2 Network — Discussion Adapter

`discussion` is an N-party round-robin channel. Participants speak in a fixed order, cycling indefinitely until you close it. The adapter enforces "wait your turn" via `validate_send`; the hub's `can_send` probe lets each agent's default handler skip wasted LLM calls when it isn't that agent's turn.

> Prerequisite: read `ag2-network-quickstart` first. This skill assumes you already know `Hub.open`, `HubClient.register`, the channel lifecycle, and `Envelope` basics.

## When to use

- "Three agents debating in turn"
- "A panel discussion / brainstorm with a fixed cast"
- "Round-robin reviewers commenting on a draft"
- Any fixed-order N-party turn-taking where each participant gets a slot per cycle

**Don't use `discussion` for:**

- Conditional handoffs ("if alice mentions security, hand to the security expert") → use `ag2-network-workflow`.
- Pipelines where each step happens once → use `ag2-network-workflow` with `TransitionGraph.sequence([...])`.
- Two participants with no fixed order → use `conversation` (covered in `ag2-network-quickstart`).
- Strict 1Q1R → use `consulting` (covered in `ag2-network-quickstart`).

## Shape

| | |
|---|---|
| Participants | 2+ |
| Turn order | Round-robin (creator first, then participants in registration order) |
| Auto-close | No |
| Termination | Explicit `channel.close()` or TTL (see "Closing" below) |
| Default view | `WindowedSummary(recent_n=N*2)` where `N` is the participant count |
| Default expectations | `turn_within(120s, warn)`, `turn_within(600s, hide)` |
| Knob | `{"ordering": ORDERING_ROUND_ROBIN}` (only ordering shipped today) |

The view recent-window is sized to `N*2` so each agent's projection covers roughly the last two full cycles — enough context for a coherent reply without ballooning the prompt as the discussion grows.

## Discussion agents, the `NetworkPlugin`, and `say`

`HubClient.register(...)` attaches `NetworkPlugin` by default — that adds `peers` / `channels` / `tasks` / `context` / `delegate`, the identity-level verbs. The `say` tool is *not* a plugin tool; it's offered per turn by the `DiscussionAdapter` itself (`tools_for(...)`), and only to `expected_next_speaker` — so a participant only sees `say` on the turn it's allowed to speak.

You normally don't need `say`: the default handler already posts the round-end `EV_TEXT(reply.body)` from the agent's plain reply, which *is* that turn's contribution. The hazard is an agent that calls `say(content="…")` **and then** also returns a non-empty body — two `EV_TEXT`s in one turn; the `DiscussionAdapter` folds the first, advances `expected_next_speaker`, and the round-end one fails `validate_send` (`expects <other> to speak`). `attach_plugin=False` does **not** suppress `say` (it's adapter-owned), so it isn't the fix here; use it only if you want a bare agent without the five plugin verbs.

Prompting alone isn't always enough: capable models will call `say` unprompted just because it's present in the per-turn tool surface. If you see the double-send in practice, swap the notify handler for one that calls `agent.ask(...)` **without** `tools=` (so the adapter-injected `say` isn't offered to the LLM) — see `ag2-network-tools-and-views` → "Bypassing adapter tools".

```python
# Bare discussion participant (no plugin verbs) — still gets `say` from the adapter on its turn:
alice = await alice_hc.register(alice_agent, Passport(name="alice"), Resume(), attach_plugin=False)
```

The examples below pass `attach_plugin=False` for compactness; drop it if your participants need `delegate` / `peers` / etc. Full detail: `ag2-network-tools-and-views` → "Adapter-owned tools (`tools_for`)".

## Smallest example

```python
import asyncio

from autogen.beta import Agent
from autogen.beta.config import AnthropicConfig
from autogen.beta.knowledge import MemoryKnowledgeStore
from autogen.beta.network import (
    EV_CHANNEL_CLOSED,
    EV_TEXT,
    ORDERING_ROUND_ROBIN,
    Hub,
    HubClient,
    LocalLink,
    Passport,
    Resume,
)


async def main() -> None:
    config = AnthropicConfig(model="claude-sonnet-4-6")
    hub = await Hub.open(MemoryKnowledgeStore(), ttl_sweep_interval=0)
    link = LocalLink(hub)

    alice_hc, bob_hc, carol_hc = (HubClient(link, hub=hub) for _ in range(3))

    alice = await alice_hc.register(
        Agent("alice", prompt="The optimist. One short sentence.", config=config),
        Passport(name="alice"), Resume(), attach_plugin=False,
    )
    bob = await bob_hc.register(
        Agent("bob", prompt="The realist. One short sentence.", config=config),
        Passport(name="bob"), Resume(), attach_plugin=False,
    )
    carol = await carol_hc.register(
        Agent("carol", prompt="The skeptic. One short sentence.", config=config),
        Passport(name="carol"), Resume(), attach_plugin=False,
    )

    channel = await alice.open(
        type="discussion",
        target=[bob.agent_id, carol.agent_id],
        knobs={"ordering": ORDERING_ROUND_ROBIN},
    )
    await channel.send("Topic: should every developer learn Rust?")

    # Halt after 6 EV_TEXT envelopes (= 2 full alice→bob→carol cycles).
    await wait_for_text_count(hub, channel.channel_id, expected=6)
    await channel.close(reason="cap_reached")

    for hc in (alice_hc, bob_hc, carol_hc):
        await hc.close()
    await hub.close()


async def wait_for_text_count(hub, channel_id, expected, *, timeout=180.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        wal = await hub.read_wal(channel_id)
        if sum(1 for e in wal if e.event_type == EV_TEXT) >= expected:
            return
        await asyncio.sleep(0.05)
    raise asyncio.TimeoutError("did not reach expected count")


asyncio.run(main())
```

The order in `target=[bob.agent_id, carol.agent_id]` is significant — it becomes the rotation order after the creator. The cycle is therefore `alice → bob → carol → alice → bob → carol → …` until the cap fires.

## How turn skipping works (the `can_send` probe)

When alice sends her first envelope, the hub fans `EV_TEXT` out to bob and carol. Both default handlers fire in parallel:

- **bob's handler** — calls `hc.can_send(channel_id, bob.agent_id)`. The adapter says "yes, bob is `expected_next_speaker`." Handler runs `Agent.ask`, sends bob's reply.
- **carol's handler** — calls `hc.can_send(channel_id, carol.agent_id)`. The adapter says "no, expected_next_speaker is bob." Handler returns *without engaging the LLM*.

When bob's reply lands, the same fan-out repeats. Now `expected_next_speaker = carol`, so carol's handler engages and bob's skips. No wasted LLM calls anywhere.

If you write a **custom handler** for a discussion channel, mirror this pattern — and **delegate non-text envelopes to `default_handler`** so you keep the auto-ack of `EV_CHANNEL_INVITE` (otherwise the channel sits in `INVITED` until `invite_ack_timeout` and the hub closes it on you):

```python
from autogen.beta.network import Envelope, EV_TEXT, default_handler


async def my_handler(envelope: Envelope) -> None:
    if envelope.event_type != EV_TEXT:
        await default_handler(envelope, client)   # invite-ack + lifecycle bookkeeping
        return
    if not hc.can_send(envelope.channel_id, my_agent_id):    # sync — not awaitable
        return  # not our turn — skip the LLM call
    # ... read WAL, project view, run Agent.ask, send reply ...
```

### A `HumanClient` in the round-robin

A discussion participant doesn't have to be an `Agent`. Register a `HumanClient` (`hc.register_human(Passport(name="user", kind="human"))` — see `ag2-network-quickstart` → "`HumanClient`") and add it to the participant order like any other. It has no notify handler that auto-responds, so the application drives its turn: subscribe with `on_envelope(...)` (or pull with `next_envelope(...)`) and, when an envelope lands, check `hc.can_send(channel_id, user.agent_id)` (sync, not awaitable) — if it's the human's turn, prompt the person and `await user.send(channel_id, text)`; otherwise wait. (The framework's own discussion tests do exactly this — a human gating on adapter state for its slot in the rotation.)

## State object

```python
@dataclass(slots=True)
class DiscussionState:
    participant_order: list[str]
    expected_next_speaker: str
    turn_count: int = 0
```

`participant_order` is fixed at create time by sorting participants on `Participant.order`. Round-robin advances by `(current_index + 1) % len(participant_order)` after every accepted `EV_TEXT`. Read state for inspection or testing via `hub._adapter_states[channel_id]` (the underscore signals "operator API" — not for production agent logic).

## Validation rules

`DiscussionAdapter.validate_send` rejects:

- `EV_TEXT` from anyone other than `state.expected_next_speaker` — raises `ProtocolError`.
- Sends from non-participants.
- Sends to a closed channel.

Protocol envelopes (`EV_CHANNEL_*`, `ag2.task.*`) bypass the turn check — they're bookkeeping, not turn-taking.

## Customising the ordering

Today only `ORDERING_ROUND_ROBIN` ships:

```python
from autogen.beta.network import ORDERING_ROUND_ROBIN

channel = await alice.open(
    type="discussion",
    target=[bob.agent_id, carol.agent_id],
    knobs={"ordering": ORDERING_ROUND_ROBIN},  # equivalent to "round_robin"
)
```

Passing any other ordering raises at create time. Future orderings (dynamic, weighted, priority) plug in here without breaking the round-robin contract.

If you need conditional turn-taking *now*, switch adapters to `workflow` — `TransitionGraph.round_robin(participants, max_turns=N)` gives you the same shape with the option to add conditions later. See `ag2-network-workflow`.

## Closing

`discussion` never auto-closes. Four patterns work; pick by who decides:

| Pattern | Who decides | When |
|---|---|---|
| App-side cap → `channel.close()` | Your orchestration code | Turn count, time, or any external predicate |
| Agent-side tool (via `ChannelInject`) | The LLM | A participant decides "we're done" |
| Custom adapter subclass | The framework | Content-based stop (rare for discussions) |
| TTL / `turn_within` expectations | The hub's sweepers | Safety net only |

### App-side cap (canonical)

```python
await wait_for_text_count(hub, channel.channel_id, expected=6)
await channel.close(reason="cap_reached")
```

The reason flows on `EV_CHANNEL_CLOSED.event_data["reason"]` — pick something descriptive so observers can distinguish a clean cap from a TTL or expectation violation.

### Agent-side tool

Same pattern as the other adapters — inject the active `Channel`, call `close()`:

```python
from autogen.beta.network.client.inject import ChannelInject


async def end_discussion(reason: str, channel: ChannelInject) -> str:
    if channel is None:
        return "no active channel"
    await channel.close(reason=f"agent_close:{reason}")
    return f"closed: {reason}"


for agent in (alice_agent, bob_agent, carol_agent):
    agent.tool(end_discussion)
```

Any participant can wrap up. See `ag2-network-quickstart`'s "Five close routes" for the full picture.

### When `discussion` isn't enough

The moment you need *conditional* turn-taking ("if bob mentions deadlines, jump to deadlines_expert"), `discussion` is the wrong adapter. Migrate to `workflow` — same N-party shape, but turn-taking lives in a `TransitionGraph` with `FromSpeaker`, `ToolCalled`, and `ContextEquals` conditions. See `ag2-network-workflow` for the migration recipe.

## Default expectations

| Expectation | Threshold | Handler | What it does |
|---|---|---|---|
| `turn_within` | 120s | `warn` | Logs `AUDIT_KIND_EXPECTATION_VIOLATED`. Channel stays open. |
| `turn_within` | 600s | `hide` | Marks the slow speaker as "skip" in the view projection for one cycle. Channel stays open. |

Discussion deliberately *never* auto-closes from expectations — a slow speaker shouldn't kill the whole panel. If you want the channel to terminate on a stalled cycle, override the expectations to `auto_close` via `ag2-network-governance`.

## Quick reference — imports

```python
from autogen.beta.network import (
    DISCUSSION_TYPE,           # = "discussion"
    ORDERING_ROUND_ROBIN,      # = "round_robin"
    DiscussionAdapter,         # the adapter class itself (rarely instantiated directly)
    DiscussionState,           # state dataclass
    HumanClient,               # a non-LLM participant in the rotation — HubClient.register_human(...)
)
```

The string forms `type="discussion"` and `ordering="round_robin"` work too — the constants are equivalent and exist for type-safety in larger codebases.
