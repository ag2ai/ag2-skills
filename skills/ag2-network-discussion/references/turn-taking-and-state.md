# Discussion — turn-taking, state, expectations

Depth for `ag2-network-discussion`. Verified against ag2 1.3.0 (`ag2/network/adapters/discussion.py`, `ag2/network/client/handlers.py`).

## The `can_send` probe

When alice's envelope lands, the hub fans it out to bob and carol; both default handlers fire:

- bob: `await hc.can_send(channel_id, bob.agent_id)` → `True` (bob is `expected_next_speaker`) → runs `Agent.ask`, posts the reply.
- carol: `can_send` → `False` → returns without calling the LLM.

When bob's reply lands the same fan-out repeats with carol expected. No wasted LLM calls.

Signature: `await HubClient.can_send(channel_id, sender_id, *, event_type=None) -> bool` (async — must be awaited).

## Custom handler on a discussion channel

Mirror the probe, and **delegate non-text envelopes to `default_handler`** so the `EV_CHANNEL_INVITE` auto-ack still happens (otherwise the channel sits in PENDING until `invite_ack_timeout` and `open` fails):

```python
from ag2.network import EV_TEXT, Envelope, default_handler


async def my_handler(envelope: Envelope) -> None:
    if envelope.event_type != EV_TEXT:
        await default_handler(envelope, client)   # invite-ack + lifecycle bookkeeping
        return
    if not await hc.can_send(envelope.channel_id, client.agent_id):
        return  # not our turn
    # ... read WAL, project view, run Agent.ask, send reply ...
```

How to install custom handlers: `ag2-network-tools-and-views`.

## A `HumanClient` in the rotation

Register with `hc.register_human(Passport(name="user", kind="human"))` and include it in `target=[...]` (or open from it). It has no auto-responder, so the app drives its turn:

```python
async def on_env(env):
    if env.event_type == EV_TEXT and await user_hc.can_send(env.channel_id, user.agent_id):
        text = await ask_the_person()            # your UI
        await user.send(env.channel_id, text)

user.on_envelope(on_env)   # or poll with user.next_envelope(...)
```

## `DiscussionState`

```python
@dataclass(slots=True)
class DiscussionState:
    participant_order: list[str]
    expected_next_speaker: str
    turn_count: int = 0
```

`participant_order` is fixed at create time (sorted by `Participant.order`: creator, then `target` order). After every accepted `EV_TEXT`, the index advances `(i + 1) % len(order)`. For tests/inspection only: `hub._adapter_states[channel_id]` (underscore = operator API).

## Validation

`DiscussionAdapter.validate_send` rejects `EV_TEXT` from anyone but `expected_next_speaker` (`ProtocolError: expects <other> to speak`), sends from non-participants, and sends to a closed channel. Protocol envelopes (`EV_CHANNEL_*`, `ag2.task.*`) bypass the turn check.

## Plugin tools

`DiscussionAdapter.tools_for(...)` returns `[]` — no `say`, so no double-send hazard. `attach_plugin=False` removes only the `NetworkPlugin` verbs (`delegate` / `peers` / `channels` / `tasks` / `context`); keep the plugin if participants should delegate or look up peers.

## Ordering

```python
from ag2.network import ORDERING_ROUND_ROBIN

channel = await alice.open(type="discussion", target=[bob.agent_id, carol.agent_id],
                           knobs={"ordering": ORDERING_ROUND_ROBIN})  # == "round_robin"
```

Any other ordering raises at create. For conditional turn-taking switch to `workflow` (`ag2-network-workflow`).

## Default expectations

| Expectation | Threshold | Handler |
|---|---|---|
| `turn_within` | 120s | `warn` |
| `turn_within` | 600s | `hide` |

These are declared on the manifest only. In 1.3.0 `Hub.open` registers the evaluators `acks_within` / `reply_within` / `max_silence` and the handlers `audit` / `notify_channel` / `auto_close` — there is no `turn_within` evaluator and no `warn` / `hide` handler, so the hub sweeper skips both entries and nothing happens. To act on stalls, register a `turn_within` evaluator (`hub.register_expectation_evaluator(...)`) and use a shipped handler such as `auto_close` — see `ag2-network-governance`.

## Imports

```python
from ag2.network import (
    DISCUSSION_TYPE,        # "discussion"
    ORDERING_ROUND_ROBIN,   # "round_robin"
    DiscussionAdapter, DiscussionState,
    HumanClient, ChannelInject, EV_TEXT, default_handler,
)
```
