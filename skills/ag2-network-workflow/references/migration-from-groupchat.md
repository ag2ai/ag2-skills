# Migrating classic GroupChat to a `workflow` channel

The two systems share vocabulary (speakers, conditions, targets, termination); the network one is data-first — a `TransitionGraph` is JSON-serialisable and survives `Hub.hydrate()`. Upstream: `website/docs/user-guide/network/migration_from_group_chat.mdx`.

## Concept map

| AG2 classic | AG2 network | Notes |
|---|---|---|
| `GroupChat(agents=[...])` | `workflow` channel with participants | The hub plays `GroupChatManager`. |
| `GroupChatManager` | `WorkflowAdapter` + `Hub` | Turn-taking moves into the hub. |
| `Agent.handoffs` | `TransitionGraph` (per channel) | Described once at channel level. |
| `AgentTarget(agent)` | `AgentTarget(agent_id)` | Takes the hub-stamped id. |
| `RevertToInitiator()` | `RevertToInitiatorTarget()` | Same. |
| `Stay()` | `StayTarget()` | Same. |
| `Terminate()` | `TerminateTarget(reason=...)` or a tool returning `Finish(...)` | Carries a reason. |
| `OnContextCondition(...)` | `ContextEquals` or custom `TransitionCondition` | `register_condition`. |
| `OnCondition(...)` (LLM condition) | tool returning `Handoff` / custom condition | |
| `ReplyResult(target=...)` | typed `Handoff(target=..., reason=...)` return | |
| `FunctionTarget(fn)` | custom `TransitionTarget` | `register_target`. |
| `max_round=N` | `max_turns=N` | Closes with reason `"max_turns"`. |
| `initiate_chat(manager, message=...)` | `HumanClient` seeder `open(...)` + `channel.send(...)` | Kickoff is the seeder's turn. |

## Round-robin

```python
# Classic
groupchat = GroupChat(agents=[alice, bob, carol], speaker_selection_method="round_robin", max_round=6)
manager = GroupChatManager(groupchat=groupchat, llm_config=llm_config)
alice.initiate_chat(manager, message="Topic: ...")

# AG2 network — seeder `user` opens; alice → bob → carol cycle, 6 agent turns
graph = TransitionGraph(
    initial_speaker=user.agent_id,
    transitions=[
        Transition(when=FromSpeaker(user.agent_id), then=AgentTarget(alice.agent_id)),
        Transition(when=FromSpeaker(alice.agent_id), then=AgentTarget(bob.agent_id)),
        Transition(when=FromSpeaker(bob.agent_id), then=AgentTarget(carol.agent_id)),
        Transition(when=FromSpeaker(carol.agent_id), then=AgentTarget(alice.agent_id)),
    ],
    default_target=TerminateTarget("done"),
    max_turns=7,  # 1 kickoff + 6 agent turns -> closes with reason "max_turns"
)
channel = await user.open(type="workflow", target=[alice.agent_id, bob.agent_id, carol.agent_id],
                          knobs={"graph": graph.to_dict()})
await channel.send("Topic: ...")
```

Don't use `RoundRobinTarget` / `round_robin(...)` with a seeder among the participants: it cycles over *every* participant, so it hands the turn back to the human and stalls. `TransitionGraph.round_robin(participants=[alice, bob, carol], max_turns=6)` is fine when `alice` opens and her kickoff text *is* her first contribution.

## Conditional handoff (`ReplyResult` → `Handoff`)

```python
# Classic
@triage.register_for_llm(description="Escalate to security review.")
def escalate(reason: str) -> ReplyResult:
    return ReplyResult(target=AgentTarget(security_reviewer), message=...)

# AG2 network — attach to the Agent before registering it with the hub
@triage_agent.tool
async def escalate(reason: str = "") -> Handoff:
    """Escalate to security review."""
    return Handoff(target="security_reviewer", reason=reason)  # passport name or agent_id

# Optional matching rule (documentation / fallback):
Transition(when=ToolCalled("escalate"), then=AgentTarget(security_reviewer.agent_id))
```

## Checklist

1. `hub = await Hub.open(MemoryKnowledgeStore())`; one `HubClient(LocalLink(hub), hub=hub)` per participant.
2. `register(agent, Passport(name=...), Resume())` each `Agent` (tools attached first); `register_human(...)` a seeder.
3. Translate handoffs into a `TransitionGraph` (`sequence` / `round_robin` where they fit).
4. Replace `initiate_chat` with `user.open(type="workflow", target=[...], knobs={"graph": graph.to_dict()})` + `channel.send(text)`.
5. Wait for `EV_CHANNEL_CLOSED` via `wait_for_channel_event(...)`.
6. Read results from `hub.read_wal(channel_id)` (`EV_PACKET` `body`); hub events via `await hub.audit_log.read_all()`.
