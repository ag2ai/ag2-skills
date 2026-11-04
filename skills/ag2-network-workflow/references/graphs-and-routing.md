# Workflow — graphs, routing, context, internals

Depth for `ag2-network-workflow`. Verified against ag2 1.3.0 (`ag2/network/adapters/workflow.py`, `ag2/network/transitions.py`).

## Contents

- Graph anatomy
- The kickoff turn and seeding
- Conditional handoff (triage)
- `Handoff` and `Finish` returns
- Context variables — the read/write loop
- Feedback loop — context-driven termination
- Custom targets and conditions
- Packet execution model and idempotent tools
- `WorkflowState`
- Plugin tools vs `attach_plugin=False`
- Cookbook patterns
- Imports

## Graph anatomy

```python
@dataclass(slots=True)
class TransitionGraph:
    initial_speaker: str              # agent_id of the first speaker (must be a participant)
    transitions: list[Transition]     # walked in priority order on every fold
    default_target: TransitionTarget  # consulted if no transition matches
    max_turns: int | None = None      # hard turn cap


@dataclass(slots=True)
class Transition:
    when: TransitionCondition   # evaluated against the just-accepted envelope + state
    then: TransitionTarget      # resolves the next speaker if when() is True
    priority: int = 0           # LOWER runs first; ties break by list order
```

On every accepted substantive envelope (`EV_TEXT` or `EV_PACKET`) the adapter does `sorted(transitions, key=lambda t: t.priority)` and takes the **first** match. If none match, `default_target` resolves. `TerminateTarget` resolves to `next_speaker=None` → the adapter returns `CLOSING` and the hub posts `EV_CHANNEL_CLOSED` with the target's `reason`.

`max_turns` is a separate, harder cap: when `turn_count` reaches it the channel closes with reason `"max_turns"` — it does *not* fall through to `default_target`. A `default_target=TerminateTarget("...")` reason only appears when a turn produced no matching transition.

**Priority trap:** `priority=100` on a `ContextEquals(...) → TerminateTarget(...)` rule pushes it to the *end*, so a `FromSpeaker(...)` fallback fires first and the channel never terminates. Put such rules at the top of the list or give them a negative priority. (Upstream `workflow.mdx` says "higher priority runs first" — the source sorts ascending; trust the source.)

`TransitionGraph.loads(data)` tolerates `"transitions": null` and `"args": null` (1.3.0).

`sequence` vs `round_robin`:

- `sequence(steps)` → `FromSpeaker(steps[i]) → AgentTarget(steps[i+1])`, default `TerminateTarget("sequence_complete")` — reachable after the last step speaks.
- `round_robin(participants, max_turns=N)` → `Always() → RoundRobinTarget()`, default `TerminateTarget("round_robin_complete")` — unreachable because `Always()` matches; the channel closes with `"max_turns"`. `RoundRobinTarget` advances through *all* participants (`participant_order`), so a `HumanClient` seeder in the channel gets the turn back and the channel stalls (executed: `expected_next_speaker == user`). With a seeder, use an explicit `FromSpeaker(a) → AgentTarget(b)` … `FromSpeaker(c) → AgentTarget(a)` cycle and `max_turns = 1 + agent_turns`.

## The kickoff turn and seeding

`agent.open(type="workflow", ...)` then `channel.send(text)`: `expected_next_speaker` starts at `initial_speaker`, the send is accepted as that participant's turn, and the graph routes onward. The `initial_speaker`'s `Agent.ask` never runs for it. Seed with a `HumanClient`:

```python
user = await user_hc.register_human(Passport(name="user", kind="human"))  # not register()
graph = TransitionGraph(
    initial_speaker=user.agent_id,
    transitions=[
        Transition(when=ContextEquals("approved", value=True), then=TerminateTarget("approved")),
        Transition(when=FromSpeaker(user.agent_id), then=AgentTarget(drafter.agent_id)),
        Transition(when=FromSpeaker(drafter.agent_id), then=AgentTarget(reviewer.agent_id)),
        Transition(when=FromSpeaker(reviewer.agent_id), then=AgentTarget(drafter.agent_id)),
    ],
    default_target=TerminateTarget("max_revisions"),
    max_turns=12,
)
channel = await user.open(type="workflow", target=[drafter.agent_id, reviewer.agent_id],
                          knobs={"graph": graph.to_dict()})
await channel.send("Brief: ...")   # user's turn → drafter drafts
await user.wait_for_channel_event(channel_id=channel.channel_id,
                                  predicate=lambda e: e.event_type == EV_CHANNEL_CLOSED, timeout=300)
```

`register_human(passport, *, resume=None, rule=None, auto_ack_invites=True)` returns a `HumanClient`; `HubClient.register(...)` rejects `kind="human"` with `ValueError`. A throwaway "intake" `Agent` that no transition routes back to also works, but costs a model config. For `sequence`, prepend the seeder: `sequence([user, a, b, c])`.

## Conditional handoff (triage)

```python
graph = TransitionGraph(
    initial_speaker=user.agent_id,
    transitions=[
        Transition(when=FromSpeaker(user.agent_id), then=AgentTarget(triage.agent_id)),
        Transition(when=ToolCalled("escalate"), then=AgentTarget(security.agent_id)),
        Transition(when=FromSpeaker(security.agent_id), then=RevertToInitiatorTarget()),
        Transition(when=FromSpeaker(triage.agent_id), then=AgentTarget(general.agent_id)),
    ],
    default_target=TerminateTarget(reason="triage_complete"),
    max_turns=20,
)
```

`ToolCalled(name)` matches the packet's `routing.tool`: at round end the framework walks the agent's `ToolCallEvent`s in emission order and records the first one whose name matches a `ToolCalled` rule. Note `ToolCalled` and `FromSpeaker(triage)` both match a triage turn that escalated — list order decides, so put `ToolCalled` first.

## `Handoff` and `Finish` returns

```python
from ag2.network import Finish, Handoff

triage_agent = Agent("triage", prompt="...", config=config)   # attach tools BEFORE register()

@triage_agent.tool
async def escalate(reason: str = "") -> Handoff:
    """Escalate this ticket to the security reviewer."""
    return Handoff(target="security", reason=reason)

@security_agent.tool
async def finish(summary: str) -> Finish:
    """Close the ticket."""
    return Finish(summary=summary, reason="resolved")
```

- `Handoff(target, reason="")` — `target` is a passport **name** or an **agent_id** (an exact id wins over a name since 1.3.0). Resolved to `routing.target`; the next speaker is set directly, shadowing graph rules for that turn.
- `Finish(summary="", reason="finished")` — closes the channel; `reason` → `close_reason`, `summary` → `routing.summary`. No `ToolCalled("finish") → TerminateTarget` glue rule needed.
- First emitted wins if a round produces several.

## Context variables — the read/write loop

Channel-scoped state lives on `WorkflowState.context_vars`, persisted as `EV_CONTEXT_SET` envelopes on the WAL.

Write from a tool:

```python
from ag2.network import ChannelInject, ChannelStateInject, EV_CONTEXT_SET
from ag2.network.workflow_helpers import delete_context, set_context

async def set_route(route: str, channel: ChannelInject) -> str:
    """Record the routing decision for this channel."""
    if channel is None:
        return "no active channel"
    await set_context(channel, "route", route)
    return f"route set to {route!r}"

# Raw equivalent:
await channel.send("", event_type=EV_CONTEXT_SET, event_data={"set": {"route": "security"}})
# event_data = {"set": {...}, "delete": [...]} — both optional; delete runs before set.
```

Read in a transition: `Transition(when=ContextEquals(key="route", value="security"), then=AgentTarget(security.agent_id))`. Context envelopes fold *before* the packet, so a rule on the same fold sees the just-set value. `ContextEquals(key, None)` matches unset/deleted keys.

Read in a tool:

```python
async def increment_counter(channel: ChannelInject, state: ChannelStateInject) -> str:
    if state is None or channel is None:
        return "no active channel"
    current = state.context_vars.get("counter", 0)
    await set_context(channel, "counter", current + 1)
    return f"counter now {current + 1}"
```

Initial values: `knobs={"graph": graph.to_dict(), "context_vars": {"escalation_level": 0}}`.

**Stuck-routing trap:** `ContextEquals` is re-evaluated every fold. `ContextEquals("route", "security") → AgentTarget(security)` near the top bounces back to security forever (until `max_turns`). Fix: list `FromSpeaker(security) → TerminateTarget(...)` before it, or have the routed agent `delete_context(channel, "route")`.

## Feedback loop — context-driven termination

```python
graph = TransitionGraph(
    initial_speaker=user.agent_id,
    transitions=[
        Transition(when=ContextEquals("done", value=True), then=TerminateTarget("approved")),
        Transition(when=FromSpeaker(user.agent_id), then=AgentTarget(drafter.agent_id)),
        Transition(when=FromSpeaker(drafter.agent_id), then=AgentTarget(reviewer.agent_id)),
        Transition(when=FromSpeaker(reviewer.agent_id), then=AgentTarget(drafter.agent_id)),
    ],
    default_target=TerminateTarget("max_iterations"),
    max_turns=10,
)

@reviewer_agent.tool
async def approve(reason: str, channel: ChannelInject) -> str:
    """Mark the draft approved; the graph terminates with reason='approved'."""
    if channel is None:
        return "no channel"
    await set_context(channel, "done", True)
    return f"approved: {reason}"
```

Executed: closes with `"approved"`; the reviewer's packet has `routing.kind == "text"` — termination came from the context rule. Without `approve` the loop runs until `"max_turns"` (the `default_target` here is unreachable because a `FromSpeaker` rule always matches).

## Custom targets and conditions

```python
from dataclasses import dataclass, field
from typing import ClassVar

from ag2.network import Envelope, TransitionDecision, TransitionTarget, register_target

@dataclass(slots=True)
class HighestRankedReviewer(TransitionTarget):
    name: ClassVar[str] = "highest_ranked_reviewer"
    role_priority: list[str] = field(default_factory=list)

    def resolve(self, state, envelope: Envelope) -> TransitionDecision:
        chosen_id = self.role_priority[0]          # your domain logic
        return TransitionDecision(next_speaker=chosen_id)

register_target(HighestRankedReviewer)
```

Conditions: implement `evaluate(state, envelope) -> bool`, set `name: ClassVar[str]`, call `register_condition(MyCondition)`. Both round-trip through `to_dict()` / `loads()` (`name` is the key; dataclass fields become `args`). Register in every process that loads the graph.

## Packet execution model and idempotent tools

Each `Agent.ask` round commits atomically as one `EV_PACKET`: `routing` (from `ToolCalled` match, `Handoff`, or `Finish`), `body` (reply text), reserved `context_updates`. `EV_CONTEXT_SET` from tools lands before the packet. A round with no body and no routing tool posts nothing.

If the agent crashes mid-packet, the channel reverts to its pre-packet state and the input is re-dispatched — **tool calls run again**. Make external side effects idempotent: idempotency keys derived from `(channel_id, round, tool_name)`, upserts instead of inserts, or gate non-idempotent tools behind HITL / single-tool rounds.

Since 1.3.0 the next speaker receives a packet as separate input parts (`[Handed off via <tool>] <reason>` and the body) rather than one joined string; views keep them apart (`Rendered` / `ensure_parts`, see `ag2-network-tools-and-views`).

## `WorkflowState`

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

`expected_next_speaker = None` means terminate. The graph is rebuilt from `graph_data` on every fold, so it survives `Hub.hydrate()`.

## Plugin tools vs `attach_plugin=False`

`HubClient.register(...)` attaches `NetworkPlugin` (`peers` / `channels` / `tasks` / `context` / `delegate`). `WorkflowAdapter.tools_for(...)` returns `[]` — no `say` on a workflow channel; the default handler wraps the plain reply into the `EV_PACKET`. Keep the plugin if agents should `delegate` or look up peers (those open *separate* channels and don't disturb the workflow); pass `attach_plugin=False` for bare pipeline workers and tests.

## Cookbook patterns

Upstream: `website/docs/user-guide/network/pattern_cookbook/` (runnable; `pip install "ag2[anthropic]" python-dotenv`).

| Pattern | Graph | When |
|---|---|---|
| Pipeline | `sequence([seed, a, b, c])` | Linear research → write → edit |
| Hierarchical | coordinator + `FromSpeaker(specialist) → RevertToInitiatorTarget()` | Coordinator dispatches, specialists return |
| Star | hub queries N spokes; `Finish` / `ToolCalled("synthesise") → TerminateTarget` | Multi-source aggregation |
| Escalation | tiered `Handoff` + `ToolCalled("resolve") → TerminateTarget("resolved")` | Support tiers, approvals |
| Redundant | `sequence` with N proposers + evaluator | Ideation with comparison |
| Feedback loop | `ContextEquals("done", True) → TerminateTarget` + `max_turns` | Drafter ↔ reviewer |
| Context-aware routing | triage sets `category`; `ContextEquals(category, X) → AgentTarget(X)` | Skill-based dispatch |
| Triage with tasks | `sequence` + `knobs["context_vars"]` | Plan then execute |

## Imports

```python
from ag2.network import (
    TransitionGraph, Transition, TransitionTarget, TransitionCondition, TransitionDecision,
    register_target, register_condition,
    AgentTarget, RoundRobinTarget, StayTarget, RevertToInitiatorTarget, TerminateTarget,
    Always, FromSpeaker, ToolCalled, ContextEquals,
    Handoff, Finish,
    EV_CONTEXT_SET, EV_PACKET, ChannelInject, ChannelStateInject,
    WORKFLOW_TYPE, HumanClient,
)
from ag2.network.workflow_helpers import set_context, delete_context
```
