# Expectations, audit log and hub listeners (verified against ag2 1.3.0)

## Expectations

The hub's expectation sweeper runs every `expectation_sweep_interval` seconds (default 10; `0` disables the loop). Each tick evaluates `metadata.manifest.expectations` on every active channel, dedupes per `(channel, expectation index, violator)`, fans `on_expectation_fired` out to listeners (the `AuditLog` writes `AUDIT_KIND_EXPECTATION_VIOLATED` here), then calls the handler named by `on_violation`. Unknown evaluator or handler names are silently skipped.

Built-in evaluators (`default_evaluators()`):

| Name | Class | `params["seconds"]` default | Fires when |
|---|---|---|---|
| `acks_within` | `AcksWithinEvaluator` | 30 | channel still `PENDING` and invitees haven't acked |
| `reply_within` | `ReplyWithinEvaluator` | 600 | `ACTIVE`; a participant addressed by `EV_TEXT` hasn't replied |
| `max_silence` | `MaxSilenceEvaluator` | 3600 | `ACTIVE`; no content envelope from anyone |

Built-in handlers (`default_handlers()`):

| `on_violation` | Class | Effect |
|---|---|---|
| `audit` | `AuditHandler` | no-op (the audit record is already written by the listener fan-out) |
| `notify_channel` | `NotifyChannelHandler` | posts `EV_EXPECTATION_VIOLATED` to the channel |
| `auto_close` | `AutoCloseHandler` | closes the channel, `reason="expectation_violated:<name>"` |

Adapter defaults: `consulting` = `acks_within(30, auto_close)` + `reply_within(600, auto_close)`; `conversation` = `max_silence(3600, audit)`; `discussion` = `turn_within(120, warn)` + `turn_within(600, hide)`; `workflow` = `turn_within(120, warn)` + `turn_within(600, auto_close)`. **No `turn_within` evaluator and no `warn` / `hide` handler ship**, so the discussion/workflow defaults are inert until you register them.

### Custom expectations (executed)

Expectations come **only** from the adapter manifest snapshotted at channel creation — `knobs={"expectations": ...}` is ignored. To change them, register an adapter instance whose manifest carries your list (it replaces the built-in adapter for that `(type, version)` for channels created afterwards):

```python
from ag2.network import ConversationAdapter, EV_TEXT, Expectation, ExpectationContext, Violation


class TooManyMessages:
    name = "too_many_messages"

    def evaluate(self, expectation: Expectation, context: ExpectationContext) -> Violation | None:
        # context: metadata, state, wal, now_iso, now_seconds — keep evaluators pure
        n = sum(1 for e in context.wal if e.event_type == EV_TEXT)
        if n > int(expectation.params["max"]):
            return Violation(expectation=expectation, violator_ids=[], detail={"text_count": n})
        return None


conv = ConversationAdapter()
conv.manifest.expectations = [
    Expectation(name="too_many_messages", on_violation="notify_channel", params={"max": 1}),
]
hub.register_adapter(conv)
hub.register_expectation_evaluator(TooManyMessages())
# hub.register_violation_handler(obj)  # obj.name + async handle(self, hub, channel_id, violation)
```

`Violation(expectation, violator_ids=[], detail={})` — `expectation` is the `Expectation` object; there is no `channel_id` field. Empty `violator_ids` = channel-wide.

Deterministic tests: `Hub.open(store, expectation_sweep_interval=0, clock=fake_clock)` then `await hub._expectation_tick()` (test entry point, leading underscore by convention).

## Audit log

`hub.audit_log` (an `AuditLog`, itself the first `HubListener`) appends JSON records to `audit.jsonl` in the hub's `KnowledgeStore` — durable with `DiskKnowledgeStore`, process-lifetime with `MemoryKnowledgeStore`.

| Member | Purpose |
|---|---|
| `await hub.audit_log.read_all()` | every record (`list[dict]`, each has `kind` and `at`) |
| `await hub.audit_log.append(record)` | add your own kinds |
| `hub.audit_log.subscribe(async_cb)` / `unsubscribe(cb)` | live tail, `async def cb(record: dict)` |
| `hub.replace_audit_log(custom)` | swap in an `AuditLog` subclass |
| `hub.health()` | in-memory ops snapshot (includes `audit_log_bytes`) |

| Constant (`kind` string) | Written on | Fields besides `kind`, `at` |
|---|---|---|
| `AUDIT_KIND_AGENT_REGISTERED` (`agent_registered`) | register | `agent_id`, `name` |
| `AUDIT_KIND_AGENT_UNREGISTERED` | unregister | `agent_id`, `name` |
| `AUDIT_KIND_RESUME_SET` | `set_resume` / observation | `agent_id`, `version`, `source` (`RESUME_SOURCE_TENANT` / `RESUME_SOURCE_OBSERVED`; observed also has `capability`, `outcome`) |
| `AUDIT_KIND_SKILL_SET` | `set_skill` | `agent_id`, `removed` |
| `AUDIT_KIND_RULE_SET` | `set_rule` | `agent_id`, `version` |
| `AUDIT_KIND_CHANNEL_CREATED` | channel open | `channel_id`, `manifest_type`, `manifest_version`, `creator_id`, `participants` |
| `AUDIT_KIND_CHANNEL_CLOSED` / `_EXPIRED` | close / TTL | `channel_id`, `reason` |
| `AUDIT_KIND_EXPECTATION_VIOLATED` | violation | `channel_id`, `expectation`, `on_violation`, `params`, `violators`, `detail` |
| `AUDIT_KIND_TASK_TERMINATED` (`task_terminated`) | task reaches completed / failed / expired / cancelled | `task_id`, `owner_id`, `channel_id`, `outcome`, `capability`, `reason` (latency lives in `Resume.observed`) |
| `AUDIT_KIND_TURN_FAILED` | default handler's turn raised | `channel_id`, `agent_id`, `envelope_id`, `exc_type`, `exc_message` |

Records carry ids, not tenants: to filter by `Passport.owner`, join on `await hub.get_agent(agent_id)`.

```python
from ag2.network import AUDIT_KIND_EXPECTATION_VIOLATED

records = await hub.audit_log.read_all()
violations = [r for r in records if r["kind"] == AUDIT_KIND_EXPECTATION_VIOLATED]
on_channel = [r for r in records if r.get("channel_id") == channel_id]
acme_ids = {p.agent_id for p in await hub.list_agents() if p.owner == "acme"}
acme_events = [r for r in records if r.get("agent_id") in acme_ids]
```

## Hub listeners

`hub.register_listener(obj)` / `unregister_listener(obj)`. All methods `async`, awaited sequentially after the state change commits, each wrapped in try/except. Subclass `BaseHubListener` (all no-ops):

| Method | Fires when |
|---|---|
| `on_envelope_posted(envelope, metadata)` | envelope accepted, WAL-appended, dispatched |
| `on_envelope_rejected(envelope, reason)` | arbiter/validation denied a send (`reason` is the `NetworkError`) |
| `on_dispatch_failed(envelope, recipient_id, reason)` | delivery to one recipient raised |
| `on_channel_event(channel_id, kind, payload)` | `opened` / `closed` / `expired` / `participant_removed` / `participant_hidden` |
| `on_agent_event(agent_id, kind, payload)` | `registered` / `unregistered` / `resume_set` / `skill_set` / `rule_set` / `observation_recorded` |
| `on_expectation_fired(channel_id, expectation, violation)` | evaluator emitted a violation |
| `on_turn_failed(channel_id, agent_id, envelope_id, exc)` | a notify-handler turn raised |
| `on_task_event(task_id, kind, payload)` | task reached a terminal state (`completed` / `failed` / `expired` / `cancelled`), or `mirror_failed`; `started` / `progress` are not fanned out by the hub |
| `on_inbox_pressure(agent_id, pending, cap)` | recipient inbox first crosses `limits.inbox.high_water` |

```python
from ag2.network import BaseHubListener


class Metrics(BaseHubListener):
    async def on_envelope_rejected(self, envelope, reason):
        print("denied", envelope.event_type, reason)

    async def on_task_event(self, task_id, kind, payload):
        print("task", kind, payload.get("capability"))


hub.register_listener(Metrics())
```

A `Hub` subclass can override the same `on_*` methods directly instead of registering a listener.

## Custom sweepers

```python
async def refresh_tokens() -> None: ...

hub.register_sweeper("refresh_tokens", 60.0, refresh_tokens)  # sync call; starts now if hub started
await hub.unregister_sweeper("refresh_tokens")                 # async!
```

Re-registering an existing name raises `ValueError`.
