---
name: ag2-network-quickstart
description: Build a multi-agent AG2 network — load whenever two or more `Agent`s (or an agent and a human/UI) need to interact; the network is the standard multi-agent pattern in `ag2`. Covers `Hub.open`, `LocalLink`, `HubClient.register` / `register_human`, `AgentClient`, `HumanClient`, `Passport`, `Resume`, channel lifecycle (PENDING → ACTIVE → CLOSING → CLOSED), the 2-party adapters `consulting` (strict 1Q1R) and `conversation` (free-form), `channel.send` / audience, `wait_for_channel_event`, reading the transcript from `hub.read_wal` (`EV_TEXT` text, `EV_PACKET` body), `hub.name_for`, close routes (`channel.close`, `ChannelInject` tool, TTL). Entry point — routes to `ag2-network-discussion` (N-party round-robin), `ag2-network-workflow` (`TransitionGraph` orchestration / GroupChat migration), `ag2-network-governance` (rules, expectations, audit), `ag2-network-tools-and-views` (custom handlers, views, peer discovery, `say`). One agent fanning out its own sub-tasks is `ag2-subagent-delegation` instead.
license: Apache-2.0
metadata:
  ag2_version: "1.3.0"
---

# AG2 Network — Quickstart

A `Hub` holds authoritative state (registry, audit log, per-channel write-ahead log). Each `Agent` is registered through a `HubClient` and gets back an `AgentClient`. Agents talk in **channels**; every send goes through the hub, so every exchange is replayable. The network is opt-in — bare `Agent` still works standalone.

## When to use

"Have two agents talk", "multi-agent system / agent network", "agents that call each other", "replace `GroupChat`", "human in a multi-agent chat", "audit trail for my agents". Not for one agent spawning its own sub-tasks → `ag2-subagent-delegation`.

| Concept | Purpose |
|---|---|
| `Hub` | Registry, audit log, channel table, WAL, sweepers (one per network) |
| `HubClient` | Per-process broker; `register(...)` / `register_human(...)` |
| `AgentClient` | Network handle for a registered `Agent` (`.agent_id`, `.open`, `.wait_for_channel_event`) |
| `HumanClient` | Non-LLM participant (UI, bridge, scripted seeder) |
| `Passport` / `Resume` | Identity (`name`, hub-stamped `agent_id`, `kind`) / capability claims |
| `Envelope` | Hub-stamped record of one event in one channel |

## Smallest network (executed against ag2 1.3.0)

```python
import asyncio

from ag2 import Agent
from ag2.config import AnthropicConfig
from ag2.knowledge import MemoryKnowledgeStore
from ag2.network import EV_CHANNEL_CLOSED, EV_TEXT, Hub, HubClient, LocalLink, Passport, Resume


async def main() -> None:
    config = AnthropicConfig(model="claude-sonnet-5")  # any provider config works, e.g. OpenAIResponsesConfig(model="gpt-4.1-mini") — see ag2-quickstart
    hub = await Hub.open(MemoryKnowledgeStore(), ttl_sweep_interval=0)
    link = LocalLink(hub)  # in-process transport
    alice_hc, bob_hc = HubClient(link, hub=hub), HubClient(link, hub=hub)

    alice = await alice_hc.register(
        Agent("alice", prompt="Ask one focused question and stop.", config=config),
        Passport(name="alice"), Resume(), attach_plugin=False,
    )
    bob = await bob_hc.register(
        Agent("bob", prompt="Answer in one short sentence.", config=config),
        Passport(name="bob"), Resume(), attach_plugin=False,
    )

    try:
        # consulting = strict 1Q1R; bob's default handler auto-acks, answers, channel auto-closes.
        channel = await alice.open(type="consulting", target="bob")
        await channel.send(
            "What's the single most important property of a distributed system?",
            audience=[bob.agent_id],
        )
        close_env = await alice.wait_for_channel_event(
            channel_id=channel.channel_id,
            predicate=lambda e: e.event_type == EV_CHANNEL_CLOSED,
            timeout=60.0,
        )
        print("closed:", close_env.event_data["reason"])  # 'consulting_complete'

        # Transcript, in order, from the hub's WAL.
        for env in await hub.read_wal(channel.channel_id):
            if env.event_type == EV_TEXT:
                print(f"{hub.name_for(env.sender_id)}: {env.event_data['text']}")
    finally:
        await alice_hc.close()
        await bob_hc.close()
        await hub.close()


asyncio.run(main())
```

Registered agents answer automatically: the default notify handler runs `Agent.ask` on each inbound envelope and posts the reply. `attach_plugin=False` skips the `NetworkPlugin` tools (`peers` / `channels` / `tasks` / `context` / `delegate`); keep the default if agents need them.

## Pick a channel adapter

| `type=` | Participants | Turn order | Ends | Skill |
|---|---|---|---|---|
| `consulting` | 2 | initiator → respondent, once | auto (`"consulting_complete"`) | this one |
| `conversation` | 2 | free-form | `channel.close()` / TTL / empty reply | this one |
| `discussion` | 2+ | fixed round-robin | `channel.close()` / TTL | `ag2-network-discussion` |
| `workflow` | 2+ | `TransitionGraph` | graph `TerminateTarget` / `Finish` / `max_turns` | `ag2-network-workflow` |

`conversation`: both sides run `Agent.ask` on every inbound `EV_TEXT`, so it auto-drives — cap it in your code (count `EV_TEXT` in the WAL, then `channel.close(reason=...)`) or give agents a close tool.

## Reading what was said

- `hub.read_wal(channel_id)` → every envelope in commit order (invites, acks, text, close).
- `EV_TEXT` → `event_data["text"]` (consulting / conversation / discussion turns).
- `EV_PACKET` → `event_data["body"]` (every agent turn on a `workflow` channel; `routing` says how it handed off).
- `hub.name_for(env.sender_id)` → passport name. Close reason: `EV_CHANNEL_CLOSED.event_data["reason"]` or `(await hub.get_channel(cid)).close_reason`.

## A human participant

```python
user = await user_hc.register_human(Passport(name="user", kind="human"))  # register() rejects kind="human"
channel = await user.open(type="consulting", target="bob")
await channel.send("Capital of France?", audience=[bob.agent_id])
reply = await user.next_envelope(
    predicate=lambda e: e.event_type == EV_TEXT and e.sender_id == bob.agent_id, timeout=60
)  # raises TimeoutError
print(reply.event_data["text"])
```

`HumanClient` has no LLM; it auto-acks invites. Consume inbound via `on_envelope(cb)` (push) or `next_envelope(...)` / `envelopes()` (pull). It is the standard kickoff seeder for `workflow` channels.

## Agent decides to stop

```python
from ag2.network import ChannelInject


async def end_conversation(reason: str, channel: ChannelInject) -> str:
    """Close the active channel when the topic is done."""
    if channel is None:  # outside a network turn
        return "no active channel"
    await channel.close(reason=f"agent_close:{reason}")
    return f"closed: {reason}"


alice_agent.tool(end_conversation)  # attach before register()
```

Expected noise: after the tool closes the channel, the default handler's round-end reply is rejected and logged (`post_envelope rejected ... is closed`, `notify handler raised`). It is harmless.

## Gotchas

- Use `alice.agent_id` for routing (`audience`, graphs); `target=` in `open` accepts a name.
- `wait_for_channel_event` / `next_envelope` raise builtin `TimeoutError` — catch that.
- Pair `Hub.open` with `hub.close()` and every `HubClient.close()` in `finally`.
- An invitee that doesn't ack within `invite_ack_timeout` (30s) makes `open` raise `ProtocolError` (`"invite_timeout"`).
- Names are unique per hub and must not look like an agent id (32 lowercase hex chars) — both raise `ProtocolError`.
- Cross-process: `serve_ws` refuses a non-loopback bind on a hub that still accepts `NoAuth` — see `ag2-network-governance`.

## Reference

- `references/channels-and-lifecycle.md` — consulting/conversation internals and expectations, plugin vs adapter `say` tool, lifecycle diagram, five close routes and adapter matrix, `Passport`/`Resume`/`PassportKind`, `HumanClient` API, envelopes, imports.
- Next: `ag2-network-discussion`, `ag2-network-workflow`, `ag2-network-governance`, `ag2-network-tools-and-views`.
- Upstream: `website/docs/user-guide/network/` (`quick_start.mdx`, `consulting.mdx`, `conversation.mdx`, `human_client.mdx`, `termination.mdx`).
