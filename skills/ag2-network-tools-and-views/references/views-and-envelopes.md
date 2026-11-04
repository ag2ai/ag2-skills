# Views, skill markdown and the envelope wire format (verified against ag2 1.3.0)

## ViewPolicy

```python
class ViewPolicy(Protocol):
    name: str
    async def project(self, wal: list[Envelope], *, participant_id: str, channel: ChannelMetadata,
                      render_envelope: EnvelopeRenderer,           # Envelope -> Sequence[Input] | None
                      name_for: NameResolver = default_name_resolver,  # agent_id -> display name
                      ) -> list[BaseEvent]: ...
```

**1.3.0 change:** `EnvelopeRenderer` returns **`Input` parts** (`Sequence[Input] | None`), no longer `str | None`. The handler wraps the adapter's `render_envelope` (which may return any `Rendered`: str, `Input`, or a sequence) with `ensure_parts`. When you call `view.project` yourself, wrap it the same way. Inbound envelopes project to one `ModelRequest(parts)`; your own envelopes project to one `ModelMessage` per text part.

| View (`name`) | Behaviour | Default for |
|---|---|---|
| `FullTranscript()` (`full_transcript`) | every visible rendered envelope | `consulting` |
| `WindowedSummary(recent_n=10)` (`windowed_summary`) | last N, plus a leading `CompactionSummary` for the elided ones | `conversation` |
| `NamedWindowedSummary(recent_n=N)` | windowed, with non-self lines prefixed `[<name>]:` | `discussion`, `workflow` |
| `NamedTranscript()` | full, with name prefixes | opt-in |

All honour `audience` (via `visible_to(envelope, participant_id)`).

```python
from ag2.network import NamedWindowedSummary, ensure_parts

adapter = hub.adapter_for(channel_id)
projected = await NamedWindowedSummary(recent_n=12).project(
    await hub.read_wal(channel_id), participant_id=carol.agent_id, channel=await hub.get_channel(channel_id),
    render_envelope=lambda env: ensure_parts(adapter.render_envelope(env)) or None,
    name_for=hub.name_for,
)
```

### Custom view (executed)

```python
from ag2.events import BaseEvent, ModelMessage, ModelRequest, TextInput
from ag2.network import EV_TEXT, visible_to


class FromOneOnly:
    """Show only EV_TEXT envelopes from one sender."""
    name = "from_one_only"

    def __init__(self, sender_id: str) -> None:
        self.sender_id = sender_id

    async def project(self, wal, *, participant_id, channel, render_envelope, name_for=lambda a: a):
        out: list[BaseEvent] = []
        for env in wal:
            if env.event_type != EV_TEXT or env.sender_id != self.sender_id or not visible_to(env, participant_id):
                continue
            parts = render_envelope(env)
            if not parts:
                continue
            if env.sender_id == participant_id:
                out.extend(ModelMessage(p.content) for p in parts if isinstance(p, TextInput))
            else:
                out.append(ModelRequest(list(parts)))
        return out
```

The default handler always uses the adapter's view, which it gets from `adapter.default_view_policy(metadata, participant_id)`. To make it use a custom view, either subclass the adapter and override `default_view_policy`, or project with your view inside a custom handler (see `custom-handlers.md`). Views shape only what the LLM sees; the WAL is unchanged.

## Skill markdown (peer discovery)

```python
from ag2.network import parse_skill_frontmatter, render_fallback_skill

client = await hc.register(agent, Passport(name="researcher"), Resume(claimed_capabilities=["research"]),
                           skill_md="---\ntitle: Research Assistant\nexpertise: [policy, finance]\n---\n\n# Researcher\n...")
parsed = parse_skill_frontmatter(skill_md)   # ParsedSkill(frontmatter: dict, body: str)
# NOTE: flat key: value parser, not YAML — "[policy, finance]" comes back as the string "[policy, finance]"
await hub.get_skill(client.agent_id)          # stored verbatim
await hub.set_skill(client.agent_id, new_md)  # audited AUDIT_KIND_SKILL_SET
render_fallback_skill(passport, resume)       # what peers(describe) shows when no skill_md was given
```

## Envelope

```python
@dataclass(slots=True)
class Envelope:
    channel_id: str
    sender_id: str
    audience: list[str] | None          # agent ids; None = all participants
    event_type: str
    event_data: dict[str, Any]
    envelope_id: str = ""               # hub-stamped
    task_id: str | None = None
    causation_id: str | None = None     # "in reply to" envelope_id
    trace_id: str | None = None         # W3C traceparent when tracing is on
    priority: Priority = "normal"       # Literal["background", "normal", "urgent"]
    depth: int = 0                      # delegation hops (checked against Rule.limits.delegation_depth)
    idempotency_key: str | None = None
    created_at: str = ""                # hub-stamped
    ttl_seconds: int | None = None
```

There is no sequence field. WAL order is admission order, and `hub.read_wal(channel_id)` returns it. `Hub.hydrate()` replays the WAL on restart.

| Constant | String | `event_data` |
|---|---|---|
| `EV_TEXT` | `ag2.msg.text` | `{"text": str}` |
| `EV_PACKET` | `ag2.packet` | `{"routing": {...}, "context_updates": {"set", "delete"}, "body": str}`. This is the workflow round-end; the text is in **`body`**. |
| `EV_CONTEXT_SET` | `ag2.context.set` | `{"set": {...}, "delete": [...]}` |
| `EV_CHANNEL_INVITE` / `_INVITE_ACK` / `_INVITE_REJECT` / `_OPENED` / `_CLOSED` / `_EXPIRED` | `ag2.channel.*` | `_CLOSED` carries `reason` |
| `EV_EXPECTATION_VIOLATED` | `ag2.expectation.violated` | posted by the `notify_channel` handler |
| `EV_TASK_CANCEL_REQUEST` | `ag2.task.cancel_request` | `{"task_id", "reason"}`, sent by `tasks(action="cancel")` to the owner only |
| `EV_TASK_CANCELLED` | `ag2.task.cancelled` | `{"task_id", "reason"}` |

Task lifecycle (started, progress, completed and so on) does not travel as WAL envelopes. `TaskMirror` mirrors it into the hub's task table.

### Custom event types (executed)

```python
from ag2.network import Envelope

eid = await alice.send_envelope(Envelope(
    channel_id=channel.channel_id, sender_id=alice.agent_id, audience=[bob.agent_id],
    event_type="myapp.review_request", event_data={"document_id": "doc-123"}, priority="urgent",
))
```

The hub does not allowlist `event_type`. Adapters ignore unknown types, so custom envelopes don't advance turn state. The recipient's handler sees them, and the default handler doesn't act on them. Use a dotted namespace, keep `event_data` JSON-serialisable, and set `causation_id` on logical replies. Over the wire, clients cannot post hub-only `ag2.channel.*` events (only invite ack/reject), and `audience` must list channel participants.
