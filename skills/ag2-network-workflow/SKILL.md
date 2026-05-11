---
name: ag2-network-workflow
description: Build an AG2 network `workflow` channel — the orchestrated N-party adapter driven by a declarative `TransitionGraph`. Use when the user needs conditional handoffs, multi-step pipelines, context-aware routing, feedback loops, or is migrating from the classic `GroupChat` + `Agent.handoffs` pattern. Covers `TransitionGraph` (with `initial_speaker`, `transitions`, `default_target`, `max_turns`); the convenience factories `TransitionGraph.sequence([...])` and `TransitionGraph.round_robin([...])`; built-in targets (`AgentTarget`, `RoundRobinTarget`, `StayTarget`, `RevertToInitiatorTarget`, `TerminateTarget`); built-in conditions (`Always`, `FromSpeaker`, `ToolCalled`, `ContextEquals`); the typed `Handoff` return for dynamic routing; channel-scoped context variables via `EV_CONTEXT_SET` and `set_context`; `register_target` / `register_condition` for custom serializable subclasses; the packet execution model and idempotent-tool requirement; the eight cookbook patterns (pipeline, hierarchical, star, escalation, redundant, feedback loop, context-aware routing, triage); and side-by-side migration from classic `GroupChat`. Load this after `ag2-network-quickstart`.
license: Apache-2.0
---

# AG2 Network — Workflow Adapter

`workflow` is the orchestrated multi-party adapter. A declarative `TransitionGraph` describes who speaks first, what conditions fire on each turn, and when the channel terminates. It's the modern replacement for the classic `GroupChat + Agent.handoffs` pattern — turn-taking lives in the hub, not in an in-process speaker selector.

> Prerequisite: read `ag2-network-quickstart` first. This skill assumes you know `Hub.open`, `HubClient.register`, the channel lifecycle, and the basic `agent_client.open(...)` / `channel.send(...)` flow.

## When to use

Load this skill when the user asks for any of:

- "Two/three/N agents with conditional handoffs"
- "Pipeline / sequence of agents (researcher → writer → editor)"
- "Triage agent routes to specialists"
- "Drafter / reviewer feedback loop"
- "Coordinator + specialists / hierarchical orchestration"
- "Migrate from `GroupChat` / `ConversableAgent.handoffs` / `ReplyResult(target=...)`"
- "I want context-driven routing (`OnContextCondition`, `StringContextCondition`)"

**Don't use `workflow` for:**

- Strict 1Q1R between two agents → `consulting` (see `ag2-network-quickstart`).
- Free-form 2-party chat with no turn order → `conversation` (see `ag2-network-quickstart`).
- Fixed round-robin with no conditions → `discussion` (see `ag2-network-discussion`).

If the user is unsure, the rule of thumb: **need any condition more complex than "next speaker in a fixed list"? Use `workflow`.**

## Shape

| | |
|---|---|
| Participants | 2+ |
| Turn order | Whatever `TransitionGraph` says |
| Auto-close | Yes — when graph emits `TerminateTarget` or `max_turns` is hit |
| Default view | `WindowedSummary(recent_n=N*2)` |
| Default expectations | `turn_within(120s, warn)`, `turn_within(600s, auto_close)` |
| Required knob | `{"graph": <TransitionGraph.to_dict()>}` |

The expectation is strict: a stuck speaker auto-closes the channel after 10 minutes. Tune via `ag2-network-governance`.

## Register workflow agents with `attach_plugin=False`

`HubClient.register(...)` auto-attaches `NetworkPlugin`, which gives every agent a `say` tool. On a workflow channel an LLM that calls `say` mid-turn emits an `EV_TEXT` envelope — `WorkflowAdapter.fold` runs immediately, advances turn state (or closes the channel via `ContextEquals` / `ToolCalled` rules), and the default handler's subsequent round-end `EV_PACKET` then raises `channel is closed` or `expects <other> to speak`. The LLM picks `say` because the plugin's context policy advertises it as a network tool.

For pure workflow agents — agents whose only tools are the domain tools you wrote — register with `attach_plugin=False`:

```python
worker = await wc.register(agent, Passport(...), Resume(), attach_plugin=False)
```

The default handler still wraps the LLM's plain-text response into the workflow packet. You lose `delegate` / `peers` / `channels` / `tasks` / `context`; for a graph-driven pipeline that's the right trade.

All examples in this skill assume this registration pattern. Full reasoning and the keep-the-plugin alternative are in `ag2-network-tools-and-views` → "Gotcha — `say` collides with adapter-managed channels".

## Smallest example — pipeline via `sequence`

```python
import asyncio

from autogen.beta import Agent
from autogen.beta.config import AnthropicConfig
from autogen.beta.knowledge import MemoryKnowledgeStore
from autogen.beta.network import (
    EV_CHANNEL_CLOSED,
    Hub,
    HubClient,
    LocalLink,
    Passport,
    Resume,
    TransitionGraph,
)


async def main() -> None:
    config = AnthropicConfig(model="claude-sonnet-4-6")
    hub = await Hub.open(MemoryKnowledgeStore(), ttl_sweep_interval=0)
    link = LocalLink(hub)

    rhc, whc, ehc = (HubClient(link, hub=hub) for _ in range(3))

    researcher = await rhc.register(
        Agent("researcher", prompt="Bullet-point three facts on the topic.", config=config),
        Passport(name="researcher"), Resume(), attach_plugin=False,
    )
    writer = await whc.register(
        Agent("writer", prompt="Turn the bullets into one paragraph.", config=config),
        Passport(name="writer"), Resume(), attach_plugin=False,
    )
    editor = await ehc.register(
        Agent("editor", prompt="Tighten the paragraph. Reply with the final text.", config=config),
        Passport(name="editor"), Resume(), attach_plugin=False,
    )

    graph = TransitionGraph.sequence([
        researcher.agent_id, writer.agent_id, editor.agent_id,
    ])

    channel = await researcher.open(
        type="workflow",
        target=[writer.agent_id, editor.agent_id],
        knobs={"graph": graph.to_dict()},
    )
    await channel.send("Topic: how does HTTPS work?")

    close_env = await researcher.wait_for_channel_event(
        channel_id=channel.channel_id,
        predicate=lambda e: e.event_type == EV_CHANNEL_CLOSED,
        timeout=180.0,
    )
    print(f"closed: {close_env.event_data.get('reason')!r}")  # 'sequence_complete'

    for hc in (rhc, whc, ehc):
        await hc.close()
    await hub.close()


asyncio.run(main())
```

`TransitionGraph.sequence([a, b, c])` builds three `FromSpeaker(x) → AgentTarget(next)` transitions plus a `TerminateTarget("sequence_complete")` default. After `c` speaks no transition matches, the default fires, and the channel auto-closes.

## `TransitionGraph` anatomy

```python
@dataclass(slots=True)
class TransitionGraph:
    initial_speaker: str            # agent_id of the first speaker (must be a participant)
    transitions: list[Transition]   # ordered list, walked in priority order on every fold
    default_target: TransitionTarget  # consulted if no transition matches
    max_turns: int | None = None    # hard turn cap; force-terminates on reaching


@dataclass(slots=True)
class Transition:
    when: TransitionCondition       # evaluated against the just-accepted envelope + state
    then: TransitionTarget          # if when() is True, this resolves the next speaker
    priority: int = 0               # LOWER runs first; ties break by insertion order
```

On every accepted substantive envelope (text or packet), the adapter walks the `transitions` list. The **first** matching condition's target resolves the next speaker. If none match, `default_target` is consulted. A `TerminateTarget` resolves with `next_speaker=None`, which makes the adapter return `AdapterResult(next_state=CLOSING, ...)` and the hub posts `EV_CHANNEL_CLOSED`.

**`priority` sorts ascending — a *lower* number is checked first.** The adapter does `sorted(transitions, key=lambda t: t.priority)` then walks the result, so `priority=0` (the default) beats `priority=100`. If you want a rule consulted before the others, either put it at the top of the list (the sort is stable, so equal priorities keep list order) or give it a *negative* priority. A common mistake: writing `priority=100` on a `ContextEquals(...) → TerminateTarget(...)` rule expecting it to win — it gets pushed to the *end* and a `FromSpeaker(...)` fallback fires first instead, so the channel never terminates.

## Gotcha — the kickoff `channel.send()` is the `initial_speaker`'s first turn

When you do `agent.open(type="workflow", ...)` then `await channel.send(text)`, that first send is folded as `initial_speaker`'s turn — it's an envelope from that agent, and `expected_next_speaker` starts equal to `initial_speaker`, so `validate_send` accepts it and the graph immediately routes onward. **The `initial_speaker`'s `Agent.ask` never runs for that turn** — the text you sent *is* its contribution.

So if your design wants the *first agent that should actually think* (the drafter, the researcher, the planner) to respond to the kickoff prompt, that agent must **not** be `initial_speaker` and must **not** be the one calling `channel.send()`. Make a thin "intake" / "requester" agent the `initial_speaker` and channel-opener, have *it* send the brief, and route `FromSpeaker(intake) → AgentTarget(drafter)`:

```python
# `requester` never runs Agent.ask — no transition routes back to it — so its
# prompt is immaterial. It exists only to own the kickoff send.
requester_agent = Agent("requester", prompt="(submits the brief)", config=config)
requester = await req_hc.register(requester_agent, Passport(name="requester"), Resume(), attach_plugin=False)
drafter   = await drf_hc.register(drafter_agent,   Passport(name="drafter"),   Resume(), attach_plugin=False)
reviewer  = await rev_hc.register(reviewer_agent,  Passport(name="reviewer"),  Resume(), attach_plugin=False)

graph = TransitionGraph(
    initial_speaker=requester.agent_id,
    transitions=[
        Transition(when=ContextEquals("approved", value=True), then=TerminateTarget("approved")),
        Transition(when=FromSpeaker(requester.agent_id), then=AgentTarget(drafter.agent_id)),   # brief → drafter
        Transition(when=FromSpeaker(drafter.agent_id),   then=AgentTarget(reviewer.agent_id)),
        Transition(when=FromSpeaker(reviewer.agent_id),  then=AgentTarget(drafter.agent_id)),   # revise loop
    ],
    default_target=TerminateTarget("max_revisions"),
    max_turns=12,
)

channel = await requester.open(
    type="workflow",
    target=[drafter.agent_id, reviewer.agent_id],
    knobs={"graph": graph.to_dict()},
)
await channel.send("Brief: …")   # consumed as `requester`'s turn → routes to `drafter`, who drafts
```

This is exactly the shape the `intake`-led feedback-loop example below uses, and why it has an `intake` agent rather than letting the drafter open the channel. The same applies to `TransitionGraph.sequence([a, b, c])`: `channel.send(...)` is `a`'s turn, so if `a` is supposed to *produce* the first artifact (not just relay a prompt), prepend a kickoff agent — `sequence([kickoff, a, b, c])` — or seed differently.

(Symptom when you get this wrong: the second agent in the chain receives the bare kickoff prompt as if it were the first agent's output, "responds" to something that isn't there, and the pipeline produces nonsense — often hallucinating that an artifact exists.)

## Built-in targets

| Target | Decision |
|---|---|
| `AgentTarget(agent_id)` | Hand off to a specific named agent. |
| `RoundRobinTarget()` | Advance through the participant order. |
| `StayTarget()` | Same speaker continues (rare; "let me elaborate" patterns). |
| `RevertToInitiatorTarget()` | Hand back to whoever opened the channel. |
| `TerminateTarget(reason="…")` | End the channel; reason flows on `EV_CHANNEL_CLOSED.event_data["reason"]`. |

## Built-in conditions

| Condition | Fires when |
|---|---|
| `Always()` | Every accepted envelope. |
| `FromSpeaker(agent_id)` | The just-accepted envelope was sent by this agent. |
| `ToolCalled(tool_name)` | The previous turn called this tool by name (matched via `routing.tool` on the packet). |
| `ContextEquals(key, value)` | Channel-scoped `context_vars[key]` equals `value`. Missing keys compare as `None`. |

For combinations beyond what these compose to, see "Custom targets / conditions" below.

## Convenience factories

```python
# Pipeline: a → b → c → terminate.
graph = TransitionGraph.sequence([alice.agent_id, bob.agent_id, carol.agent_id])

# Round-robin with a cap: a → b → c → a → b → c → terminate after 6 turns.
graph = TransitionGraph.round_robin(
    participants=[alice.agent_id, bob.agent_id, carol.agent_id],
    max_turns=6,
)
```

`round_robin` uses `Always() → RoundRobinTarget()` with a `TerminateTarget("round_robin_complete")` default. `sequence` uses `FromSpeaker(steps[i]) → AgentTarget(steps[i+1])` chains with `TerminateTarget("sequence_complete")`.

## Conditional handoff (the most common manual graph)

A triage agent inspects each request and routes to a specialist via a tool call:

```python
from autogen.beta.network import (
    AgentTarget, FromSpeaker, RevertToInitiatorTarget,
    TerminateTarget, ToolCalled, Transition, TransitionGraph,
)

graph = TransitionGraph(
    initial_speaker=triage.agent_id,
    transitions=[
        # Triage's `escalate` tool fired → route to security.
        Transition(when=ToolCalled("escalate"),
                   then=AgentTarget(security.agent_id)),
        # Once security has spoken, hand back to whoever opened the channel.
        Transition(when=FromSpeaker(security.agent_id),
                   then=RevertToInitiatorTarget()),
        # Otherwise triage's reply goes to the generic responder.
        Transition(when=FromSpeaker(triage.agent_id),
                   then=AgentTarget(general.agent_id)),
    ],
    default_target=TerminateTarget(reason="triage_complete"),
    max_turns=20,
)
```

For each `ToolCalled(name) → AgentTarget(agent)` transition, attach an `@tool`-decorated function on the speaker's `Agent`. The simplest form returns a typed `Handoff` — the framework reads it from the tool's result and routes:

```python
from autogen.beta.network import Handoff


# `.tool` lives on `Agent`. The `AgentClient` returned by `hc.register(...)`
# is the network handle (used for `.open()`, `.send()`, `.agent_id`) and does
# NOT expose `.tool`. So: build the Agent, attach tools, *then* register.
triage_agent = Agent("triage", prompt="…", config=config)


@triage_agent.tool
async def escalate(reason: str = "") -> Handoff:
    """Escalate this ticket to the security reviewer."""
    return Handoff(target=security.agent_id, reason=reason)


triage = await triage_hc.register(
    triage_agent, Passport(name="triage"), Resume(), attach_plugin=False,
)
```

The typed `Handoff` return supersedes the matching `ToolCalled` rule when both are configured — useful when the *target* depends on runtime state. The graph's `ToolCalled` rule remains useful as documentation and as a fallback.

## Context variables — the read/write loop

Channel-scoped mutable state lives on `WorkflowState.context_vars: dict[str, Any]`. It's the modern equivalent of classic `ContextVariables` from `autogen.agentchat.group`, scoped to one workflow channel and persisted as `EV_CONTEXT_SET` envelopes on the WAL.

### Writing context (from a tool)

```python
from autogen.beta.network import ChannelInject, EV_CONTEXT_SET
from autogen.beta.network.workflow_helpers import set_context


async def set_route(route: str, channel: ChannelInject) -> str:
    """Record the routing decision for this channel."""
    if channel is None:
        return "no active channel"
    await set_context(channel, "route", route)
    return f"route set to {route!r}"
```

`set_context(channel, key, value)` is a thin wrapper that emits `EV_CONTEXT_SET` with `audience=[]` (state-only; no participant is notified). Equivalent raw form:

```python
await channel.send(
    "",
    event_type=EV_CONTEXT_SET,
    event_data={"set": {"route": route}},
    audience=[],
)
```

The full event_data shape supports both set and delete: `{"set": {...}, "delete": [...]}`. Either is optional; within one envelope `delete` runs before `set`.

### Reading context (in a transition)

```python
Transition(
    when=ContextEquals(key="route", value="security"),
    then=AgentTarget(security.agent_id),
)
```

`ContextEquals(key, value=None)` fires when the key is unset or explicitly deleted. The mutation is folded *before* substantive turn checks, so a `ContextEquals` rule on the same fold sees the just-set value.

### Reading context (in a tool)

```python
from autogen.beta.network import ChannelStateInject


async def increment_counter(channel: ChannelInject, state: ChannelStateInject) -> str:
    if state is None or channel is None:
        return "no active channel"
    current = state.context_vars.get("counter", 0)
    await set_context(channel, "counter", current + 1)
    return f"counter now {current + 1}"
```

### Initial values

```python
channel = await alice.open(
    type="workflow",
    target=[bob.agent_id, carol.agent_id],
    knobs={
        "graph": graph.to_dict(),
        "context_vars": {"escalation_level": 0, "ticket_id": ticket_id},
    },
)
```

### The stuck-routing trap

`ContextEquals` is **sticky** — once a key is set, every subsequent fold re-evaluates it. If you have `ContextEquals("route", "security") → AgentTarget(security)` near the top of the list, you'll bounce back to security forever (or until `max_turns`). Two fixes:

1. **List terminate rules before context-conditions:** `FromSpeaker(security) → TerminateTarget(...)` placed earlier short-circuits the loop after security speaks.
2. **Have the routed agent clear the key:** the security agent's tool emits `EV_CONTEXT_SET` with `{"delete": ["route"]}` when done.

Fix #1 is the more common pattern.

## Feedback loop — context-driven termination

```python
graph = TransitionGraph(
    initial_speaker=intake.agent_id,
    transitions=[
        # Approve sets done=True → terminate.
        Transition(when=ContextEquals("done", value=True),
                   then=TerminateTarget("approved")),
        # intake → drafter.
        Transition(when=FromSpeaker(intake.agent_id),
                   then=AgentTarget(drafter.agent_id)),
        # Alternate drafter ↔ reviewer.
        Transition(when=FromSpeaker(drafter.agent_id),
                   then=AgentTarget(reviewer.agent_id)),
        Transition(when=FromSpeaker(reviewer.agent_id),
                   then=AgentTarget(drafter.agent_id)),
    ],
    default_target=TerminateTarget("max_iterations"),
    max_turns=10,
)


# `reviewer_agent` is the `Agent`; `reviewer` is the `AgentClient` returned
# by `register()` and used in the graph above (for `reviewer.agent_id`).
# Tools must be attached to the Agent before registration.
@reviewer_agent.tool
async def approve(reason: str, channel: ChannelInject) -> str:
    """Mark the draft approved; the graph terminates with reason='approved'."""
    if channel is None:
        return "no channel"
    await set_context(channel, "done", True)
    return f"approved: {reason}"
```

The reviewer's `approve` call writes `done=True`; the reviewer's reply text lands on the same packet fold; `ContextEquals("done", True)` matches and the channel terminates with `"approved"`. Without `approve`, the drafter/reviewer alternation continues until `max_turns` fires `"max_iterations"`.

## The eight cookbook patterns

The AG2 docs ship a [Pattern Cookbook](https://docs.ag2.ai/docs/beta/network/pattern_cookbook/pattern_cookbook) with runnable examples. One-line summaries — each one is a `workflow` channel with a specific graph:

| Pattern | Graph | When |
|---|---|---|
| **Pipeline** | `TransitionGraph.sequence([a, b, c])` | Linear A→B→C→terminate (research → write → edit). |
| **Hierarchical** | Coordinator + `FromSpeaker(specialist) → RevertToInitiatorTarget()` | Coordinator dispatches; specialist returns to coordinator. |
| **Star** | Hub queries N spokes; `ToolCalled("synthesise") → TerminateTarget` | Multi-source aggregation, parallel data gathering. |
| **Escalation** | Tiered `Handoff` + `ToolCalled("resolve") → TerminateTarget("resolved")` | Support tiers, multi-level approval. |
| **Redundant** | `sequence` with N proposers + evaluator | Niche; novelty/ideation with comparison. |
| **Feedback loop** | `ContextEquals("done", True) → TerminateTarget` + `max_turns` | Drafter ↔ reviewer until approved. |
| **Context-aware routing** | Triage sets `context_vars["category"]`; `ContextEquals(category, X) → AgentTarget(specialist_X)` | Support triage, skill-based dispatch. |
| **Triage with tasks** | `TransitionGraph.sequence` + `knobs["context_vars"]` initial values | Triage produces plan; sequence executes the plan. |

Pipeline, escalation, context-aware routing, and feedback loop together cover most real-world needs. The cookbook page has runnable end-to-end code for each.

## Custom targets and conditions

When the built-ins don't fit, implement the `Protocol` and register the class so it round-trips through `TransitionGraph.to_dict()`:

```python
from typing import ClassVar
from dataclasses import dataclass, field

from autogen.beta.network import (
    Envelope,
    TransitionDecision,
    TransitionTarget,
    register_target,
)


@dataclass(slots=True)
class HighestRankedReviewer(TransitionTarget):
    name: ClassVar[str] = "highest_ranked_reviewer"
    role_priority: list[str] = field(default_factory=list)

    def resolve(self, state, envelope: Envelope) -> TransitionDecision:
        # ... look up the next reviewer based on domain logic ...
        return TransitionDecision(next_speaker=chosen_id)


register_target(HighestRankedReviewer)
```

Same pattern for conditions — implement `evaluate(state, envelope) -> bool`, set a `ClassVar[str] name`, call `register_condition(MyCondition)`. Both customisations persist via `to_dict()`: the `name` field is the key, dataclass fields become the args dict reconstituted by `loads(...)`.

## The packet execution model and idempotent tools

Each `Agent.ask` round on a workflow channel commits to the WAL atomically as a single `EV_PACKET` envelope. The packet carries the agent's routing decision (`routing.tool` matched against `ToolCalled` rules, or a pre-resolved `routing.target` from a typed `Handoff`), the round's body text, and a reserved `context_updates` slot. `EV_CONTEXT_SET` envelopes from tool calls land *before* the packet, so a `ContextEquals` rule on the same fold sees the just-set value.

**Atomicity has a consequence: if the agent crashes mid-packet, the channel reverts to its pre-packet state and the input is re-dispatched. Tool calls in that packet execute again on retry.**

Tools that touch external systems **must be idempotent under retry**:

- Use the external service's idempotency-key feature where available (Stripe, S3). Derive a stable key from `(channel_id, round_counter, tool_name)`.
- For database writes, use upsert (`INSERT ... ON CONFLICT`) rather than blind insert.
- For tools that genuinely cannot be idempotent, gate them behind HITL or run them in single-tool rounds so the packet boundary is tighter.

## Migration from classic GroupChat

The translation is mostly mechanical. The two systems share the vocabulary — speakers, conditions, targets, terminations — but the new one is data-first (a `TransitionGraph` is JSON-serialisable and survives `Hub.hydrate()`).

### Concept mapping

| Classic | Beta network | Notes |
|---|---|---|
| `GroupChat(agents=[...])` | `workflow` channel with participants | The hub plays `GroupChatManager`'s role. |
| `GroupChatManager` | `WorkflowAdapter` + `Hub` | Turn-taking moves into the hub. |
| `Agent.handoffs` | `TransitionGraph` (per-channel, not per-agent) | Handoffs described once at channel level. |
| `AgentTarget(agent)` | `AgentTarget(agent_id)` | Takes id, not reference. |
| `RevertToInitiator()` | `RevertToInitiatorTarget()` | Same semantics. |
| `Stay()` | `StayTarget()` | Same. |
| `Terminate()` | `TerminateTarget(reason="…")` | Now carries a reason. |
| `OnContextCondition(...)` | `ContextEquals` or custom `TransitionCondition` | Register via `register_condition`. |
| `OnCondition(...)` | Custom `TransitionCondition` | Same. |
| `ReplyResult(target=...)` from a tool | Typed `Handoff` return | `Handoff(target=..., reason=...)`. |
| `FunctionTarget(fn)` | Custom `TransitionTarget` | Implement `resolve()`, `register_target(...)`. |
| `max_round=N` | `max_turns=N` on the graph | Same hard cap. |

### Side-by-side: round-robin

```python
# Classic
groupchat = GroupChat(agents=[alice, bob, carol],
                     speaker_selection_method="round_robin", max_round=6)
manager = GroupChatManager(groupchat=groupchat, llm_config=llm_config)
alice.initiate_chat(manager, message="Topic: …")

# Beta network
graph = TransitionGraph.round_robin(
    participants=[alice.agent_id, bob.agent_id, carol.agent_id],
    max_turns=6,
)
channel = await alice.open(
    type="workflow",
    target=[bob.agent_id, carol.agent_id],
    knobs={"graph": graph.to_dict()},
)
await channel.send("Topic: …")
```

### Side-by-side: conditional handoff (`ReplyResult` → `Handoff`)

```python
# Classic
@triage.register_for_llm(description="Escalate to security review.")
def escalate(reason: str) -> ReplyResult:
    return ReplyResult(target=AgentTarget(security_reviewer), message=...)

# Beta network — attach to the Agent *before* registering with the hub
@triage_agent.tool
async def escalate(reason: str = "") -> Handoff:
    return Handoff(target=security_reviewer.agent_id, reason=reason)

# Plus the matching transition in the graph (as documentation/fallback):
Transition(when=ToolCalled("escalate"), then=AgentTarget(security_reviewer.agent_id))
```

### Migration checklist

1. Stand up a hub (`Hub.open(MemoryKnowledgeStore())`).
2. Wrap each existing `Agent` via `hc.register(...)` with a `Passport` and `Resume`.
3. Translate `Handoffs` config into a `TransitionGraph`. Use `sequence` / `round_robin` factories where they fit; build manually for conditional logic.
4. Replace `initiate_chat(...)` with `alice.open(type="workflow", target=[...], knobs={"graph": graph.to_dict()})` followed by `channel.send(text)`.
5. Wait for `EV_CHANNEL_CLOSED` via `wait_for_channel_event(...)`.
6. Inspect via `hub.read_wal(channel_id)` and `hub._audit_log.read_all()`.

## State object

```python
@dataclass(slots=True)
class WorkflowState:
    participant_order: list[str]
    expected_next_speaker: str | None
    last_speaker_id: str | None = None
    last_envelope_id: str | None = None
    turn_count: int = 0
    pending_close_reason: str = ""
    creator_id: str = ""
    graph_data: dict = field(default_factory=dict)
    context_vars: dict[str, Any] = field(default_factory=dict)
```

`expected_next_speaker = None` signals "channel should terminate" — the adapter's `on_accepted` reads this and returns `AdapterResult(next_state=CLOSING, ...)`. `graph_data` is the serialised graph; the adapter rebuilds `TransitionGraph` on every fold so there's no mutable graph state in memory.

## Quick reference — imports

```python
from autogen.beta.network import (
    # Graph and transitions
    TransitionGraph,
    Transition,
    TransitionTarget,
    TransitionCondition,
    TransitionDecision,
    register_target,
    register_condition,
    # Built-in targets
    AgentTarget,
    RoundRobinTarget,
    StayTarget,
    RevertToInitiatorTarget,
    TerminateTarget,
    # Built-in conditions
    Always,
    FromSpeaker,
    ToolCalled,
    ContextEquals,
    # Typed handoff return for tools
    Handoff,
    # Context variables
    EV_CONTEXT_SET,
    ChannelInject,
    ChannelStateInject,
    # Channel types
    WORKFLOW_TYPE,
)
from autogen.beta.network.workflow_helpers import set_context, delete_context
```
