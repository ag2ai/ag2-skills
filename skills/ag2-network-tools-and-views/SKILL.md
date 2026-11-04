---
name: ag2-network-tools-and-views
description: "Agent-side AG2 network surface: the `NetworkPlugin` LLM tools (`delegate`, `peers`, `channels`, `tasks`, `context`) and the adapter-owned `say`, tool injects (`ChannelInject`, `AgentClientInject`, `HubInject`, `TaskInject`), replacing the notify handler via `agent_client.on_envelope(...)` + `default_handler` and hooks (`read_wal_until`, `resolve_view_policy`, `stamp_dependencies`, `ensure_parts`), non-LLM participants (`HumanClient`, `register_human`), `ViewPolicy` (`FullTranscript`, `WindowedSummary`, `NamedTranscript`, `NamedWindowedSummary`, custom views over `Input` parts), skill markdown (`skill_md=`, `parse_skill_frontmatter`, `hub.set_skill`), and the `Envelope` wire format (`EV_*`, `audience`, `visible_to`, `Priority`, `causation_id`, custom events via `send_envelope`). Use for gateways, headless workers, filtered history, peer discovery. Load after `ag2-network-quickstart`; hub-side rules/audit/track records live in `ag2-network-governance`."
license: Apache-2.0
---

# AG2 Network — Tools, Views & Custom Handlers

The agent-side counterpart of `ag2-network-governance`. It covers which network actions the LLM gets, what history it sees, how to replace the notify handler, how agents describe themselves to peers, and the envelope format.

> Prerequisite: `ag2-network-quickstart` (`Hub.open`, `HubClient.register`, channels).

## When to use

- Trim or extend the LLM's network tools, or stop a model from double-posting via `say`
- Build a gateway or headless worker, or add a person, bridge or script to a channel (`HumanClient`)
- Change, filter or redact the channel history the LLM sees (`ViewPolicy`)
- Wire peer discovery (`skill_md`, `peers`)
- Send custom event types, or look up `EV_*`, `Envelope` fields and `audience` semantics

## Tool surface

| Source | Tools | Controlled by |
|---|---|---|
| `NetworkPlugin` | `delegate`, `peers`, `channels`, `tasks`, `context` | `hc.register(..., attach_plugin=True)` (default) |
| Channel adapter (`tools_for`) | `say`, offered on consulting/conversation/discussion when it's the agent's turn; never on workflow | the default handler passes it as `ask(tools=...)` |

`attach_plugin=False` does **not** remove `say`. The default handler already posts the agent's reply body, so an agent that calls `say` and also replies posts twice. On `consulting` the second post fails with `ProtocolError: channel ... is closed`. Don't prompt agents to call `say`. If a model calls it anyway, use a custom handler without `tools=`. Every action and argument is listed in [references/network-tools.md](references/network-tools.md). There is no `tasks(action="start")`. Code starts tasks with `Task(...)` from `ag2.task`, because `agent.task` is deprecated.

## Non-LLM participants — `HumanClient`

```python
from ag2.network import EV_TEXT, Passport

user = await hc.register_human(Passport(name="user", kind="human"))  # resume=, rule=, auto_ack_invites=True
ch = await user.open(type="consulting", target="analyst")
await user.send(ch.channel_id, "What's our Q3 exposure?")
reply = await user.next_envelope(predicate=lambda e: e.event_type == EV_TEXT, timeout=60)  # TimeoutError
```

To push envelopes instead, use `user.on_envelope(async_cb)` and remove it with `remove_envelope_callback(cb)`. To stream them, use `async for env in user.envelopes()`. `user.send_envelope(env)` (alias `post_envelope`) posts adapter-shaped envelopes. Find humans with `hub.list_agents(kind="human")`.

## Replacing the default handler

```python
from ag2.network import EV_TEXT, Envelope, default_handler


async def gateway(envelope: Envelope) -> None:  # called with (envelope,) only; capture client
    if envelope.event_type != EV_TEXT:
        await default_handler(envelope, client)  # keeps invite auto-ack etc.
        return
    await outbox.put(envelope.event_data["text"])  # no LLM turn


client.on_envelope(gateway)  # restore: client.on_envelope(client._run_default_handler)
```

If you drop the default handling of `EV_CHANNEL_INVITE`, the channel sits in PENDING until the hub closes it with `invite_timeout`. If you drop the handling of `EV_TEXT` / `EV_PACKET`, no reply is ever sent. [references/custom-handlers.md](references/custom-handlers.md) walks through the default turn step by step and has a complete, executed custom turn handler. That handler drops `say` and tags each turn with `capability=`, built from `read_wal_until`, `resolve_view_policy`, `stamp_dependencies`, `ensure_parts` and `TaskMirror`.

## Views

The adapter picks the default view: `FullTranscript` for consulting, `WindowedSummary(recent_n=10)` for conversation, and `NamedWindowedSummary` for discussion and workflow. `NamedTranscript` is opt-in. Every view respects `audience`.

```python
from ag2.events import ModelMessage, ModelRequest, TextInput


class FromOneOnly:
    name = "from_one_only"

    def __init__(self, sender_id):
        self.sender_id = sender_id

    async def project(self, wal, *, participant_id, channel, render_envelope, name_for=lambda a: a):
        out = []
        for env in wal:
            parts = render_envelope(env) if env.sender_id == self.sender_id else None  # Sequence[Input] | None
            if parts:
                out += ([ModelMessage(p.content) for p in parts if isinstance(p, TextInput)]
                        if env.sender_id == participant_id else [ModelRequest(list(parts))])
        return out
```

New in 1.3.0: `render_envelope` returns `Input` parts, not `str`. When you call `view.project` yourself, wrap the adapter's renderer: `lambda e: ensure_parts(adapter.render_envelope(e)) or None`. For how to plug a view into the handler, see [references/views-and-envelopes.md](references/views-and-envelopes.md).

## Skill markdown

Register with `hc.register(agent, passport, resume, skill_md="---\ntitle: ...\n---\n# body")`. Peers read it with `peers(action="describe", name=...)`. Update it with `await hub.set_skill(agent_id, md)`, which is audited. `parse_skill_frontmatter(md)` returns a `ParsedSkill(frontmatter, body)` and parses flat `key: value` lines, not YAML. `render_fallback_skill(passport, resume)` builds the text used when an agent has no skill markdown.

## Envelopes

`EV_TEXT` carries `{"text"}`. `EV_PACKET` (the workflow round-end) carries `{"routing", "context_updates", "body"}`. `audience=None` broadcasts; a list of agent ids restricts delivery, and `visible_to(env, agent_id)` checks it. `priority` is a `Literal` (`"background"`, `"normal"` or `"urgent"`). Set `causation_id` to the envelope you're replying to. Custom types go through `await client.send_envelope(Envelope(..., event_type="myapp.x", event_data={...}))`. The hub passes them through without acting on them. The full field and constant tables are in [references/views-and-envelopes.md](references/views-and-envelopes.md).

## Reference

- [references/network-tools.md](references/network-tools.md): `say` gating, every plugin tool action and its arguments, injects and dependency keys
- [references/custom-handlers.md](references/custom-handlers.md): what the default handler does, wrapper patterns, a full custom turn handler
- [references/views-and-envelopes.md](references/views-and-envelopes.md): `ViewPolicy`, built-in and custom views, skill markdown, `Envelope` fields, `EV_*` table, custom events
- Upstream docs in `website/docs/user-guide/network/`: `network_assigned_tools.mdx`, `views_and_skills.mdx`, `envelopes_and_events.mdx`, `agent_clients.mdx`, `human_client.mdx`
