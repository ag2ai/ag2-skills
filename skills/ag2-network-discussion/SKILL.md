---
name: ag2-network-discussion
description: "Open an AG2 network `discussion` channel — N-party (2+) round-robin where participants speak in a fixed order, cycling until explicit close or TTL. Use for a brainstorm or panel with a fixed cast, debate in turns, or round-robin reviewers. Covers `agent_client.open(type=\"discussion\", target=[...], knobs={\"ordering\": ORDERING_ROUND_ROBIN})`, rotation order (creator first, then `target` order), `expected_next_speaker`, the `hc.can_send(...)` probe that keeps out-of-turn handlers from calling the LLM, `DiscussionState`, app-side caps and `ChannelInject` close tools, a `HumanClient` seat in the rotation, reading the transcript from `hub.read_wal` (`EV_TEXT`), and the declared-but-inert `turn_within` expectation defaults. Load after `ag2-network-quickstart`. For conditional handoffs, pipelines, or graph-driven termination use `ag2-network-workflow`."
license: Apache-2.0
---

# AG2 Network — Discussion Adapter

`discussion` is an N-party round-robin channel. Participants speak in fixed order — creator first, then `target` order — cycling until you close it. `validate_send` rejects out-of-turn `EV_TEXT` with `ProtocolError`; each default handler calls `hc.can_send(...)` first, so only the expected speaker runs its LLM.

> Prerequisite: `ag2-network-quickstart` (`Hub.open`, `HubClient.register`, lifecycle, envelopes).

## When to use

- "Three agents debating in turn", "panel / brainstorm with a fixed cast", "round-robin reviewers on a draft".

Not for: conditional handoffs, once-through pipelines, or graph-driven termination → `ag2-network-workflow`; two agents without turn order → `conversation`; strict 1Q1R → `consulting` (both in `ag2-network-quickstart`).

| | |
|---|---|
| Participants | 2+ |
| Turn order | creator, then `target=[...]` order, repeating |
| Auto-close | Never — `channel.close()`, agent close tool, or TTL |
| Default view | `NamedWindowedSummary(recent_n=max(2N, 4))` (≈ last two cycles, each line labelled `[name]:`) |
| Default expectations | declared `turn_within(120s, warn)`, `turn_within(600s, hide)` — inert by default (no `turn_within` evaluator ships) |
| Knob | `{"ordering": ORDERING_ROUND_ROBIN}` (the only ordering shipped) |
| Adapter tools | none (`tools_for` → `[]`, no `say`) — a turn is just the agent's reply |

## Smallest example (executed against ag2 1.3.0)

```python
import asyncio

from ag2 import Agent
from ag2.config import AnthropicConfig
from ag2.knowledge import MemoryKnowledgeStore
from ag2.network import EV_TEXT, ORDERING_ROUND_ROBIN, Hub, HubClient, LocalLink, Passport, Resume


async def wait_for_text_count(hub, channel_id, expected, *, timeout=180.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        wal = await hub.read_wal(channel_id)
        if sum(1 for e in wal if e.event_type == EV_TEXT) >= expected:
            return
        await asyncio.sleep(0.05)
    raise TimeoutError("did not reach expected count")


async def main() -> None:
    config = AnthropicConfig(model="claude-sonnet-5")
    hub = await Hub.open(MemoryKnowledgeStore(), ttl_sweep_interval=0)
    link = LocalLink(hub)
    alice_hc, bob_hc, carol_hc = (HubClient(link, hub=hub) for _ in range(3))

    alice = await alice_hc.register(
        Agent("alice", prompt="The optimist. Speak only as yourself, one short sentence.", config=config),
        Passport(name="alice"), Resume(), attach_plugin=False,
    )
    bob = await bob_hc.register(
        Agent("bob", prompt="The realist. Speak only as yourself, one short sentence.", config=config),
        Passport(name="bob"), Resume(), attach_plugin=False,
    )
    carol = await carol_hc.register(
        Agent("carol", prompt="The skeptic. Speak only as yourself, one short sentence.", config=config),
        Passport(name="carol"), Resume(), attach_plugin=False,
    )

    channel = await alice.open(
        type="discussion",
        target=[bob.agent_id, carol.agent_id],  # rotation: alice → bob → carol → alice …
        knobs={"ordering": ORDERING_ROUND_ROBIN},
    )
    await channel.send("Topic: should every developer learn Rust?")  # alice's first turn

    await wait_for_text_count(hub, channel.channel_id, expected=6)  # 2 full cycles
    await channel.close(reason="cap_reached")

    for env in await hub.read_wal(channel.channel_id):
        if env.event_type == EV_TEXT:
            print(f"{hub.name_for(env.sender_id)}: {env.event_data['text']}")

    for hc in (alice_hc, bob_hc, carol_hc):
        await hc.close()
    await hub.close()


asyncio.run(main())
```

Like `workflow`, the creator's kickoff send **is** the creator's turn — alice's `Agent.ask` does not run for it; bob answers first and alice's model first speaks on turn 4. Write the kickoff as the brief. If a `HumanClient` moderator opens instead, it is in the rotation and must post on each of its turns (see the reference), or the cycle stalls.

## Closing

| Pattern | Who decides |
|---|---|
| App-side cap → `channel.close(reason=...)` (canonical, above) | your code |
| `ChannelInject` close tool on each `Agent` | any participant's LLM |
| Adapter subclass sentinel | framework (rare here) |
| TTL / expectations set to `auto_close` | hub sweepers |

```python
from ag2.network import ChannelInject


async def end_discussion(reason: str, channel: ChannelInject) -> str:
    """Close the discussion when consensus is reached."""
    if channel is None:
        return "no active channel"
    await channel.close(reason=f"agent_close:{reason}")
    return f"closed: {reason}"


for agent in (alice_agent, bob_agent, carol_agent):  # attach before register()
    agent.tool(end_discussion)
```

## Gotchas

- Rotation is fixed at create time; `ordering` values other than `"round_robin"` raise at create.
- Named views label history `[bob]: ...`; small models sometimes imitate the format and write other participants' lines — tell each agent to speak only as itself.
- Nothing stops a discussion except you: no auto-close, and the declared `turn_within` expectations don't fire by default. Always cap it.
- Need "if bob mentions X, go to Y"? Switch to `workflow` — see `ag2-network-workflow`.

## Reference

- `references/turn-taking-and-state.md` — the `can_send` probe, custom handlers that keep invite auto-ack, a `HumanClient` in the rotation, `DiscussionState`, validation rules, expectations, imports.
- Upstream: `website/docs/user-guide/network/discussion.mdx`, `termination.mdx`.
