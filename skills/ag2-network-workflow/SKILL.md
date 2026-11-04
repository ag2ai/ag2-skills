---
name: ag2-network-workflow
description: Build an AG2 network `workflow` channel — the orchestrated N-party adapter driven by a declarative `TransitionGraph`. Use for conditional handoffs, pipelines (researcher → writer → editor), triage/routing, drafter ↔ reviewer feedback loops, coordinator + specialists, or migrating from classic `GroupChat` / `Agent.handoffs` / `ReplyResult`. Covers `TransitionGraph`, `Transition`, factories `TransitionGraph.sequence(...)` / `.round_robin(...)`; targets `AgentTarget`, `RoundRobinTarget`, `StayTarget`, `RevertToInitiatorTarget`, `TerminateTarget`; conditions `Always`, `FromSpeaker`, `ToolCalled`, `ContextEquals`; typed tool returns `Handoff` (route) and `Finish` (close); `set_context` / `delete_context` / `EV_CONTEXT_SET`; seeding with a `HumanClient`; reading what each agent said from `EV_PACKET` `body`; `register_target` / `register_condition`; packet retry and idempotent tools. Load after `ag2-network-quickstart`; fixed round-robin without conditions is `ag2-network-discussion`.
license: Apache-2.0
metadata:
  ag2_version: "1.3.0"
---

# AG2 Network — Workflow Adapter

`workflow` is the orchestrated multi-party channel. A JSON-serialisable `TransitionGraph` says who speaks first, which rule fires after each turn, and when the channel closes. It replaces classic `GroupChat` + handoffs: turn-taking lives in the hub.

> Prerequisite: `ag2-network-quickstart` (`Hub.open`, `HubClient.register`, `register_human`, channel lifecycle).

## When to use

- Conditional handoffs, triage → specialist, escalation tiers
- Pipelines / sequences of agents, drafter ↔ reviewer loops, coordinator + specialists
- Context-driven routing (classic `OnContextCondition`), GroupChat migration

Not for: strict 1Q1R → `consulting`; free 2-party chat → `conversation` (both in `ag2-network-quickstart`); fixed round-robin with no conditions → `ag2-network-discussion`.

## Rule #1 — the kickoff send *is* the initial speaker's turn

`channel.send(text)` from the opener is folded as `initial_speaker`'s turn; that agent's `Agent.ask` **never runs** for it. So `sequence([researcher, writer, editor])` opened by `researcher` skips the researcher: the writer gets the bare prompt. Always make a non-LLM **`HumanClient` seeder** the initial speaker and the opener.

## Canonical example — seeded pipeline (executed against ag2 1.3.0)

```python
import asyncio

from ag2 import Agent
from ag2.config import AnthropicConfig
from ag2.knowledge import MemoryKnowledgeStore
from ag2.network import (
    EV_CHANNEL_CLOSED, EV_PACKET, EV_TEXT,
    Hub, HubClient, LocalLink, Passport, Resume, TransitionGraph,
)


async def main() -> None:
    config = AnthropicConfig(model="claude-sonnet-5")  # any provider config works, e.g. OpenAIResponsesConfig(model="gpt-4.1-mini") — see ag2-quickstart
    hub = await Hub.open(MemoryKnowledgeStore(), ttl_sweep_interval=0)
    link = LocalLink(hub)
    uhc, rhc, whc, ehc = (HubClient(link, hub=hub) for _ in range(4))

    # Seeder: no LLM. Its kickoff send is the graph's first turn.
    user = await uhc.register_human(Passport(name="user", kind="human"))
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

    graph = TransitionGraph.sequence(
        [user.agent_id, researcher.agent_id, writer.agent_id, editor.agent_id]
    )
    channel = await user.open(
        type="workflow",
        target=[researcher.agent_id, writer.agent_id, editor.agent_id],
        knobs={"graph": graph.to_dict()},
    )
    await channel.send("Topic: how does HTTPS work?")  # = user's turn → researcher

    close_env = await user.wait_for_channel_event(
        channel_id=channel.channel_id,
        predicate=lambda e: e.event_type == EV_CHANNEL_CLOSED,
        timeout=300.0,
    )
    print("closed:", close_env.event_data["reason"])  # 'sequence_complete'

    # Transcript in speaking order. EV_TEXT -> event_data["text"];
    # EV_PACKET (every agent turn on a workflow) -> event_data["body"].
    for env in await hub.read_wal(channel.channel_id):
        if env.event_type == EV_TEXT:
            said = env.event_data["text"]
        elif env.event_type == EV_PACKET:
            said = env.event_data.get("body", "")
        else:
            continue  # invites, acks, EV_CONTEXT_SET, EV_CHANNEL_CLOSED, ...
        print(f"--- {hub.name_for(env.sender_id)}:\n{said}")

    for hc in (uhc, rhc, whc, ehc):
        await hc.close()
    await hub.close()


asyncio.run(main())
```

Output order: `user → researcher → writer → editor`, then `closed: sequence_complete`. The final answer is the last `EV_PACKET`'s `body`.

## Reading results

- **What an agent said:** workflow agent turns are `EV_PACKET` envelopes, *not* `EV_TEXT`. `event_data = {"body": str, "routing": {...}, "context_updates": {...}}`. `body` is the agent's reply text; filtering only on `EV_TEXT` shows just the seeder.
- **Who handed off / why:** `routing["kind"]` is `"text"` (plain reply), `"handoff"` (`tool`, `reason`, and `target` agent_id for a `Handoff` return) or `"finish"` (`tool`, `reason`, `summary`).
- **Speaker order:** `hub.read_wal(channel_id)` is in commit order; `hub.name_for(env.sender_id)` gives the passport name.
- **Why it closed:** `EV_CHANNEL_CLOSED.event_data["reason"]`, or `(await hub.get_channel(cid)).close_reason`.
- **Context:** `EV_CONTEXT_SET` envelopes (`{"set": {...}, "delete": [...]}`) in the same WAL.

## Graph essentials

`TransitionGraph(initial_speaker, transitions, default_target, max_turns=None)`; `Transition(when, then, priority=0)`. After each accepted turn the **first** matching transition picks the next speaker; if none match, `default_target` fires. **Lower `priority` is checked first** (stable sort, so list order breaks ties). `max_turns` closes with reason `"max_turns"` without consulting `default_target`.

| Targets | Conditions |
|---|---|
| `AgentTarget(agent_id)`, `RoundRobinTarget()`, `StayTarget()`, `RevertToInitiatorTarget()`, `TerminateTarget(reason=...)` | `Always()`, `FromSpeaker(agent_id)`, `ToolCalled(tool_name)`, `ContextEquals(key, value)` (missing key == `None`) |

Factories: `sequence(steps)` → `FromSpeaker(s[i]) → AgentTarget(s[i+1])`, default `TerminateTarget("sequence_complete")`. `round_robin(participants, max_turns=N)` → `Always() → RoundRobinTarget()`; always closes with `"max_turns"` (set it, or it cycles forever). `RoundRobinTarget` cycles over *every* participant — including a seeder, where it stalls waiting for the human; with a seeder, write explicit `FromSpeaker → AgentTarget` cycles instead.

## Routing from tools

Attach tools to the `Agent` **before** `register(...)` (`AgentClient` has no `.tool`). The workflow adapter offers no `say` tool; agents just reply.

```python
from ag2.network import ChannelInject, Finish, Handoff
from ag2.network.workflow_helpers import set_context


@triage_agent.tool
async def escalate(reason: str = "") -> Handoff:
    """Escalate to the security reviewer."""
    return Handoff(target="security", reason=reason)  # passport name or agent_id


@security_agent.tool
async def finish(summary: str) -> Finish:
    """Ticket resolved — close the channel."""
    return Finish(summary=summary, reason="resolved")  # close_reason == "resolved"


@reviewer_agent.tool
async def approve(reason: str, channel: ChannelInject) -> str:
    """Approve; pairs with ContextEquals("done", True) → TerminateTarget("approved")."""
    if channel is None:
        return "no channel"
    await set_context(channel, "done", True)
    return f"approved: {reason}"
```

A `Handoff`/`Finish` return wins over graph rules for that turn (first emitted wins). A plain tool named in `ToolCalled("escalate")` routes via the graph. Initial context: `knobs={"graph": ..., "context_vars": {...}}`.

## Gotchas

- Kickoff = initial speaker's turn (Rule #1). Symptom: second agent hallucinates the first agent's artifact.
- `ContextEquals` is sticky — place `TerminateTarget`/`FromSpeaker` rules first or `delete_context` after use, else routing loops until `max_turns`.
- `priority=100` means *later*, not sooner.
- Tools may re-run if a packet is retried after a crash — make external side effects idempotent.
- Always set `max_turns` (or a `Finish`/terminate path). The manifest declares `turn_within(120s, warn)` / `turn_within(600s, auto_close)`, but 1.3.0's `Hub.open` registers no `turn_within` evaluator, so a stuck speaker is **not** auto-closed unless you register one (`ag2-network-governance`). Default view `NamedWindowedSummary(recent_n=max(2N, 4))`.

## Reference

- `references/graphs-and-routing.md` — full graph anatomy, conditional handoff, context read/write loop, feedback loop, stuck-routing trap, custom targets/conditions, `WorkflowState`, packet model and idempotency, cookbook patterns, plugin vs `attach_plugin=False`, imports.
- `references/migration-from-groupchat.md` — concept map and side-by-side translations.
- Upstream: `website/docs/user-guide/network/workflow.mdx`, `context_variables.mdx`, `pattern_cookbook/`.
