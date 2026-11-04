# Custom notify handlers (verified against ag2 1.3.0)

`agent_client.on_envelope(callback)` **replaces** the default handler. The callback receives only `(envelope)`, so capture the client in a closure. `default_handler(envelope, client)` is the exported default; call it to get standard behaviour for anything you don't handle. Restore the default with `client.on_envelope(client._run_default_handler)`.

## What the default handler does

| Event | Default behaviour | If you drop it |
|---|---|---|
| `EV_CHANNEL_INVITE` | posts `EV_CHANNEL_INVITE_ACK` | channel stays `PENDING`; hub closes it after `invite_ack_timeout` (30s), reason `invite_timeout` |
| `EV_TEXT` / `EV_PACKET` | substantive turn (below) | no LLM, no reply; channel stalls until an expectation fires |
| `ag2.channel.*`, `ag2.task.*` | no-op | — |

The substantive turn runs in this order:

1. `get_channel` and return unless the channel is ACTIVE.
2. Return if this agent already replied to this envelope (redelivery guard via `find_envelope_by_causation`).
3. `can_send` probe.
4. Project the history with `read_wal_until` and the view from `resolve_view_policy`, rendering through the adapter.
5. Get the current input as `Input` parts.
6. Seed a fresh `MemoryStream` with the history.
7. Call `stamp_dependencies`.
8. Collect the adapter tools from `tools_for`.
9. Attach a `TaskMirror`.
10. Run `agent.ask(*parts, stream=..., dependencies=..., tools=adapter_tools)`.
11. Build the reply with `adapter.build_round_envelope(...)`, set `causation_id` to the inbound envelope's id, and call `send_envelope`.

Exceptions are routed to `report_turn_failure`, which writes `AUDIT_KIND_TURN_FAILED` and fires `HubListener.on_turn_failed`. The channel stays open.

## Gateway / selective / logging wrappers

```python
from ag2.network import EV_CHANNEL_INVITE, EV_TEXT, Envelope, default_handler


async def gateway_handler(envelope: Envelope) -> None:
    """Forward inbound text to an external queue instead of running the LLM."""
    if envelope.event_type != EV_TEXT:
        await default_handler(envelope, client)  # invite acks etc.
        return
    await outbox.put({"from": envelope.sender_id, "text": envelope.event_data.get("text", ""),
                      "channel": envelope.channel_id})


async def selective_handler(envelope: Envelope) -> None:
    if envelope.event_type == EV_CHANNEL_INVITE and envelope.sender_id not in TRUSTED_IDS:
        return  # drop: the hub times the invite out
    await default_handler(envelope, client)


client.on_envelope(gateway_handler)
```

For a participant that never runs an LLM (person at a UI, bridge, scripted seeder), use `HumanClient` instead (`hc.register_human(...)`).

## Full custom turn handler (executed)

This is the default substantive path minus adapter tools (so the model never sees `say`), with every turn tagged with a capability for `Resume.observed`. Change `tools=`, the view, the dependencies or the output envelope as you need.

```python
from ag2.network import (
    EV_PACKET, EV_TEXT, Envelope, TaskMirror, default_handler, ensure_parts,
    read_wal_until, resolve_view_policy, stamp_dependencies,
)
from ag2.network.client.channel import Channel
from ag2.stream import MemoryStream


def make_turn_handler(client, *, capability: str | None = None):
    async def handler(envelope: Envelope) -> None:
        if envelope.event_type not in (EV_TEXT, EV_PACKET):
            await default_handler(envelope, client)
            return
        hub = client._hub_client
        meta = await hub.get_channel(envelope.channel_id)
        if meta.is_terminal() or not await hub.can_send(envelope.channel_id, client.agent_id):
            return
        adapter = hub.adapter_for_metadata(meta)

        def render(env):  # adapters may return str / Input / sequences; views want Input parts
            return ensure_parts(adapter.render_envelope(env)) or None

        view = resolve_view_policy(client, meta)
        history = await view.project(await read_wal_until(client, envelope), participant_id=client.agent_id,
                                     channel=meta, render_envelope=render, name_for=hub.name_for)
        parts = ensure_parts(adapter.extract_turn_input(envelope))
        if not parts:
            return
        stream = MemoryStream()
        if history:
            await stream.history.storage.set_history(stream.id, history)
        deps = stamp_dependencies(client, Channel(metadata=meta, client=client),
                                  await hub.adapter_state(meta.channel_id))

        mirror = TaskMirror(hub_client=hub, owner_id=client.agent_id, channel_id=meta.channel_id)
        subs = mirror.attach(stream)
        try:
            reply = await client.agent.ask(*parts, stream=stream, dependencies=deps, capability=capability)
            out = adapter.build_round_envelope(
                metadata=meta, sender_id=client.agent_id, reply=reply,
                events=list(await stream.history.get_events()),
                state=await hub.adapter_state(meta.channel_id), hub=hub,
            )
        finally:
            mirror.detach(stream, subs)
        if out is not None:
            out.causation_id = envelope.envelope_id
            await client.send_envelope(out)

    return handler


analyst.on_envelope(make_turn_handler(analyst, capability="analysis"))
```

Hooks used above (all exported from `ag2.network`):

| Hook | Purpose |
|---|---|
| `read_wal_until(client, envelope)` | WAL slice before `envelope` |
| `resolve_view_policy(client, metadata)` | the adapter's `ViewPolicy` for this participant |
| `stamp_dependencies(client, channel, state)` | `{CHANNEL_DEP, AGENT_CLIENT_DEP, HUB_DEP, CHANNEL_STATE_DEP}` for `ask(dependencies=)` |
| `ensure_parts(rendered)` | normalise an adapter's `Rendered` (str / `Input` / sequence) into `list[Input]` |
