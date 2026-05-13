---
name: ag2-network-governance
description: Govern an AG2 multi-agent network — identity (`Passport`, `Resume` with `claimed_capabilities` and hub-mutated `observed` track record), per-agent `Rule` with `AccessBlock` / `LimitsBlock` / `RateBlock` / `InboxBlock`, the swappable `HubArbiter` / `RuleBasedArbiter` access-&-routing seam (`Allow` / `Deny`, `register_arbiter`), `AuthAdapter` / `AuthRegistry` registration, channel-level `Expectation`s (`acks_within`, `reply_within`, `max_silence`, `turn_within`) with `audit` / `warn` / `auto_close` violation handlers, the hub's append-only audit log and `AUDIT_KIND_*` constants, live `HubListener` / `BaseHubListener` observability (`on_envelope_posted` / `on_turn_failed` / `on_inbox_pressure` / …) plus `Hub` `on_*` hooks and `register_sweeper`, and task observation via `agent.task(..., capability=...)` + `TaskMirror` that auto-updates `Resume.observed` for peer ranking. Use when the user needs rate limits, access policy, SLAs, compliance trails, live metrics/alerting, capability-driven peer ranking, or to inspect what actually happened on the network. Load this after `ag2-network-quickstart`. For the agent-side surface (custom handlers, views, LLM tools, `HumanClient`) see `ag2-network-tools-and-views`.
license: Apache-2.0
---

# AG2 Network — Governance

Everything hub-side: identity, per-agent rules, expectations, audit, and task observation. The hub is the single source of truth — every send goes through it, every observation reads from it, every policy is checked there.

> Prerequisite: read `ag2-network-quickstart` first. This skill assumes you know `Hub.open`, `Passport`, `Resume`, the channel lifecycle, and the `agent_client.register(...)` flow.

## When to use

Load this skill when the user needs to:

- Limit who can talk to whom (`AccessBlock`)
- Rate-limit envelopes (`RateBlock`)
- Cap inbox size to prevent flooding (`InboxBlock`) — and get an early-warning signal before the cap (`on_inbox_pressure` / `high_water`)
- Set channel TTL defaults or delegation depth (`LimitsBlock`)
- Plug in custom access / routing logic — JWT scopes, per-tenant quotas, federation (`HubArbiter` / `RuleBasedArbiter`)
- Authenticate agents at registration (`AuthAdapter`)
- Tune the channel-close timing (`acks_within`, `reply_within`, `max_silence`, `turn_within`)
- Read or query the audit log for compliance — or stream live state changes to metrics / alerting (`HubListener`)
- Add a custom periodic task to the hub (`register_sweeper`)
- Build a capability track record on each agent (`Resume.observed`)
- Route based on which agents have demonstrably done a task (e.g. "send to whichever researcher has the lowest `p50_latency_ms`")

## Identity — what every agent carries

Three dataclasses describe an agent on the network. The tenant supplies most fields; the hub stamps the rest.

```python
from autogen.beta.network import Passport, Resume, ResumeExample

passport = Passport(
    name="alice",                # required, unique within the hub
    owner="acme",                # optional, tenant id for multi-tenant deployments
    model="claude-sonnet-4-6",   # optional, surfaces on peer-lookup results
)

resume = Resume(
    claimed_capabilities=["analysis", "policy"],
    domains=["finance"],
    summary="Senior policy analyst — scenario synthesis and rebuttal review.",
    examples=[ResumeExample(title="Q3 risk brief", note="…")],
)
```

| Field | Source |
|---|---|
| `Passport.name` | tenant (required, unique) |
| `Passport.agent_id` | **hub-stamped** at registration; use for routing |
| `Passport.created_at` | **hub-stamped** ISO-Z timestamp |
| `Resume.claimed_capabilities` | tenant (free-form strings: `"research"`, `"summarisation"`, …) |
| `Resume.summary` | tenant — indexed for peer lookup |
| `Resume.observed` | **hub-mutated** per-capability `ObservedStat` (n / completed / failed / expired / p50_latency_ms) |
| `Resume.last_updated` | **hub-stamped** ISO-Z, refreshed on mutation |

The `observed` field is the agent's track record. It grows automatically as the agent runs capability-tagged tasks (see "Task observation" below).

## Per-agent rules

Pass a `Rule` at registration to govern an agent's behaviour on the network:

```python
from autogen.beta.network import (
    Rule, AccessBlock, LimitsBlock, RateBlock, InboxBlock, ChannelTypeAccess,
)

rule = Rule(
    access=AccessBlock(
        outbound_to=["bob", "carol"],           # whitelist of recipients (names or ids)
        channel_types=ChannelTypeAccess(
            initiate=["consulting", "discussion"],
            accept=["consulting", "discussion"],
        ),
    ),
    limits=LimitsBlock(                         # rate + inbox nest INSIDE limits
        channel_ttl_default="4h",               # default TTL for channels this agent creates
        delegation_depth=2,                     # max recursion through sub-task delegation
        rate=RateBlock(per_minute=60, burst=10),
        inbox=InboxBlock(max_pending=100),      # cap inbound queue depth
    ),
)

alice = await alice_hc.register(
    Agent("alice", config=config),
    Passport(name="alice"),
    Resume(),
    rule=rule,
)
```

| Block | Controls | Failure mode |
|---|---|---|
| `AccessBlock` | Who this agent can address; channel types it can create/join | `AccessDeniedError` |
| `LimitsBlock` | TTL defaults; delegation depth | `LimitsExceeded` |
| `RateBlock` | Outbound envelopes/minute | Throttled at send time |
| `InboxBlock` | Inbound queue depth | `InboxFull` to the sender |

When a rule check fails the hub raises the matching error from `channel.send(...)` or `hc.register(...)`; the envelope never lands on the WAL. The denial is also recorded in the audit log (kind `AUDIT_KIND_RULE_SET` on rule changes; the actual deny event flows through the standard audit path). The component that *runs* those checks — and the seam where you'd plug in something other than rule data — is the **arbiter**, below.

### Updating a rule after registration

```python
new_rule = Rule(access=AccessBlock(outbound_to=["bob"]))
await hub.set_rule(alice.agent_id, new_rule)  # emits AUDIT_KIND_RULE_SET
```

### Parsing duration strings

`LimitsBlock.channel_ttl_default` accepts a string parsed by `parse_duration`:

```python
from autogen.beta.network import parse_duration

parse_duration("30s")  # 30.0
parse_duration("4h")   # 14400.0
parse_duration("2d")   # 172800.0
```

`s`, `m`, `h`, `d` suffixes; whitespace tolerated.

## The arbiter — swappable access & routing

The hub doesn't enforce `Rule`s with inline `if` checks anymore; it delegates every access / routing decision to a **`HubArbiter`** — a Protocol with one method per decision point, each returning `Allow()` or `Deny(reason, error=<NetworkError subclass>)`:

| Method | Consulted before… | Default `Deny` error |
|---|---|---|
| `authorize_register(passport, resume, rule)` | committing a registration | `AccessDeniedError` |
| `authorize_channel_open(creator, metadata)` | creating a channel | `AccessDeniedError` |
| `authorize_send(envelope, sender, sender_rule, recipients)` | appending an envelope to the WAL (outbound access + delegation depth) | `AccessDeniedError` |
| `authorize_inbox(envelope, recipient, recipient_rule, current_pending)` | enqueuing into a recipient's inbox (capacity) | `InboxFull` |
| `authorize_dispatch(envelope, sender, recipient, recipient_rule)` | dispatching one delivery | `AccessDeniedError` |
| `resolve_unknown_audience(envelope, unknown_ids)` | dispatching to ids the hub doesn't know — returns `None` (drop silently — the single-hub default) or a replacement id list (federation hook) | — |

The default is **`RuleBasedArbiter`** — it enforces the per-agent `Rule` (`access` + `limits` + `inbox` + `rate`) exactly as the hub did inline before this seam existed. If you only use `Rule`, you never touch the arbiter.

Swap it to layer your own logic — JWT scopes, per-tenant quotas, federation routing — on top of (or instead of) the rule data. `BaseHubArbiter` returns `Allow()` for everything, so a subclass that overrides one gate would *allow* the rest — to keep rule enforcement, delegate to a `RuleBasedArbiter()` instance:

```python
from autogen.beta.network import HubArbiter, BaseHubArbiter, RuleBasedArbiter, Allow, Deny

class ScopedArbiter(BaseHubArbiter):
    def __init__(self, inner: HubArbiter) -> None:
        self._inner = inner                           # the rule checks
    async def authorize_send(self, envelope, sender, sender_rule, recipients):
        if not _token_has_scope(sender, "net.send"):
            return Deny("missing net.send scope")     # → AccessDeniedError back to the caller
        return await self._inner.authorize_send(envelope, sender, sender_rule, recipients)
    # authorize_register / _channel_open / _inbox / _dispatch / resolve_unknown_audience
    # all fall through to BaseHubArbiter's Allow() — explicitly re-delegate any you want enforced.

hub.register_arbiter(ScopedArbiter(RuleBasedArbiter()))   # one active arbiter; replaces the prior one
# hub.arbiter  → the active instance (read-only; handy in tests)
```

`Deny.error` picks the `NetworkError` subclass the hub raises (default `AccessDeniedError`) — `Deny(..., error=InboxFull)` etc. to control it. The arbiter is the **gatekeeper** (consulted *before* the state change); `HubListener` (later) is the **observer** (notified *after*). It's a different concern from `AuthAdapter` below — that authenticates *credentials* once at registration; the arbiter authorizes *actions* throughout the channel's life.

## Authentication

By default the hub uses `AuthRegistry.default()` which registers `NoAuth` for the empty scheme — every registration succeeds without credentials. For production:

```python
from autogen.beta.network import AuthAdapter, AuthRegistry, AuthBlock, AuthError, Hub
from autogen.beta.knowledge import MemoryKnowledgeStore


class HMACAuth:
    async def verify(self, passport: Passport, credentials: AuthBlock) -> None:
        expected = self._sign(passport.name, credentials.scheme)
        if credentials.token != expected:
            raise AuthError(f"bad hmac for {passport.name}")


registry = AuthRegistry()
registry.register("hmac", HMACAuth())

hub = await Hub.open(MemoryKnowledgeStore(), auth=registry)
```

`AuthAdapter` is a `Protocol`:

```python
class AuthAdapter(Protocol):
    async def verify(self, passport: Passport, credentials: AuthBlock) -> None: ...
```

Raise `AuthError` to reject. The hub calls `verify(...)` at registration time and records `AUDIT_KIND_AGENT_REGISTERED` on success.

## Expectations — channel-level SLAs

Every adapter ships defaults in its manifest. The expectation sweeper task evaluates them every `expectation_sweep_interval` (default 10s) and dispatches violations to handlers.

### Built-in evaluators

| Name | Class | Threshold |
|---|---|---|
| `"acks_within"` | `AcksWithinEvaluator` | All invitees must ack within `params["seconds"]` of channel creation. |
| `"reply_within"` | `ReplyWithinEvaluator` | The respondent must reply within `params["seconds"]` of the initiator's first send (consulting only). |
| `"max_silence"` | `MaxSilenceEvaluator` | No participant goes silent for longer than `params["seconds"]`. |
| `"turn_within"` | Composed from `MaxSilenceEvaluator` | The next speaker must speak within `params["seconds"]` of being scheduled. |

### Default expectations per adapter

| Adapter | Defaults |
|---|---|
| `consulting` | `acks_within(30s, auto_close)`, `reply_within(600s, auto_close)` |
| `conversation` | `max_silence(3600s, audit)` |
| `discussion` | `turn_within(120s, warn)`, `turn_within(600s, hide)` |
| `workflow` | `turn_within(120s, warn)`, `turn_within(600s, auto_close)` |

### Violation handlers

```python
from autogen.beta.network import Expectation

Expectation(name="acks_within", on_violation="auto_close", params={"seconds": 30})
```

| `on_violation` | Handler class | Effect |
|---|---|---|
| `"audit"` | `AuditHandler` | Write `AUDIT_KIND_EXPECTATION_VIOLATED`. Channel continues. |
| `"warn"` | `NotifyChannelHandler` | Post `EV_EXPECTATION_VIOLATED` on the channel WAL. Channel continues. |
| `"auto_close"` | `AutoCloseHandler` | Close with `reason="expectation_violated:<name>"`; record to audit. |
| `"hide"` | (custom) | Hide late-speaker turns from view projection; no built-in shipped today. |

### Overriding adapter defaults

Pass `expectations` in the channel knobs to replace the adapter's defaults:

```python
channel = await alice.open(
    type="conversation",
    target=bob.agent_id,
    knobs={
        "expectations": [
            {"name": "max_silence", "on_violation": "auto_close",
             "params": {"seconds": 600}},
        ],
    },
)
```

### Custom evaluators

```python
from typing import ClassVar
from autogen.beta.network import EV_TEXT
from autogen.beta.network.hub import ExpectationContext, Violation


class TooManyMessagesEvaluator:
    name: ClassVar[str] = "too_many_messages"

    def evaluate(self, ctx: ExpectationContext) -> list[Violation]:
        threshold = ctx.params["max"]
        text_count = sum(1 for e in ctx.wal if e.event_type == EV_TEXT)
        if text_count > threshold:
            return [Violation(
                expectation=self.name,
                channel_id=ctx.channel.channel_id,
                detail=f"text count {text_count} exceeds {threshold}",
            )]
        return []
```

Evaluators are pure functions over channel state — no I/O, no mutation — so they're trivially testable. Register on a custom registry passed to `Hub.open(..., evaluators=registry)`.

### Deterministic testing

```python
hub = await Hub.open(MemoryKnowledgeStore(), expectation_sweep_interval=0)

# Manually advance state and tick:
clock.advance(45)
await hub._expectation_tick()  # operator API (leading underscore by convention)
```

## Audit log

The hub maintains an append-only `_audit_log` (`AuditLog` instance):

```python
records = await hub._audit_log.read_all()
for r in records:
    print(r["kind"], r["at"], r)
```

Each record is a plain dict with at minimum `kind` and `at` (ISO-Z timestamp); kind-specific fields appear alongside.

### Audit kinds

```python
from autogen.beta.network import (
    AUDIT_KIND_AGENT_REGISTERED,
    AUDIT_KIND_AGENT_UNREGISTERED,
    AUDIT_KIND_RESUME_SET,
    AUDIT_KIND_SKILL_SET,
    AUDIT_KIND_RULE_SET,
    AUDIT_KIND_CHANNEL_CREATED,
    AUDIT_KIND_CHANNEL_CLOSED,
    AUDIT_KIND_CHANNEL_EXPIRED,
    AUDIT_KIND_TASK_TERMINATED,
    AUDIT_KIND_EXPECTATION_VIOLATED,
)
```

| Kind | When | Common fields |
|---|---|---|
| `AUDIT_KIND_AGENT_REGISTERED` | `hc.register(...)` | `agent_id`, `name`, `owner` |
| `AUDIT_KIND_AGENT_UNREGISTERED` | `hc.unregister(agent_id)` | `agent_id` |
| `AUDIT_KIND_RESUME_SET` | `hub.set_resume(...)` | Source: `RESUME_SOURCE_TENANT` or `RESUME_SOURCE_OBSERVED` |
| `AUDIT_KIND_SKILL_SET` | `hub.set_skill(...)` | Updated skill markdown |
| `AUDIT_KIND_RULE_SET` | `hub.set_rule(...)` | The new `Rule` |
| `AUDIT_KIND_CHANNEL_CREATED` | `alice.open(...)` | `creator_id`, manifest type/version, participants |
| `AUDIT_KIND_CHANNEL_CLOSED` | Any close route | `reason` |
| `AUDIT_KIND_CHANNEL_EXPIRED` | TTL sweeper | TTL details |
| `AUDIT_KIND_TASK_TERMINATED` | `agent.task(...)` reached terminal state via `TaskMirror` | `owner_id`, `capability`, `outcome`, `latency_ms` |
| `AUDIT_KIND_EXPECTATION_VIOLATED` | Expectation evaluator's threshold elapsed | `expectation`, `channel_id`, evaluator detail |

### Common queries

```python
# All violations on the system.
violations = [r for r in await hub._audit_log.read_all()
              if r["kind"] == AUDIT_KIND_EXPECTATION_VIOLATED]

# Everything that happened on one channel.
channel_records = [r for r in await hub._audit_log.read_all()
                   if r.get("channel_id") == channel_id]

# All registrations for one tenant.
acme_agents = [r for r in await hub._audit_log.read_all()
               if r["kind"] == AUDIT_KIND_AGENT_REGISTERED
               and r.get("owner") == "acme"]
```

The audit log is **durable when backed by `DiskKnowledgeStore`**; with `MemoryKnowledgeStore` it lives only as long as the hub.

## Hub listeners — live programmatic observability

The audit log is the *durable* record. For *live* reactions to hub state changes — push to a metrics backend, stream to a dashboard, alert an on-call — register a **`HubListener`**: a read-only Protocol the hub fans out to after every state transition has committed. (The built-in audit log is itself one of these listeners.)

| Method | Fires when |
|---|---|
| `on_envelope_posted(envelope)` | an envelope was accepted and written to the WAL |
| `on_envelope_rejected(envelope, reason)` | the arbiter / validation denied a send |
| `on_dispatch_failed(envelope, recipient_id, error)` | delivery to one recipient raised |
| `on_channel_event(channel_id, kind, payload)` | created / opened / closed / expired / state change |
| `on_agent_event(agent_id, kind, payload)` | registered / unregistered / resume / skill / rule set |
| `on_expectation_fired(channel_id, expectation, detail)` | an expectation evaluator's threshold elapsed |
| `on_turn_failed(agent_id, channel_id, error)` | an agent's notify-handler turn raised (the default handler routes failures here) |
| `on_task_event(task_id, kind, payload)` | a `ag2.task.*` lifecycle event was observed |
| `on_inbox_pressure(agent_id, pending, cap)` | a recipient's inbox first crosses `LimitsBlock.inbox.high_water` (fires once per crossing, not per envelope) |

All methods are `async`; the hub awaits them sequentially in registration order, each wrapped in `try/except` — a buggy listener can't stall dispatch. Keep them fast (queue I/O onto your own task). Subclass `BaseHubListener` (every method is a `pass`) and override only what you need:

```python
from autogen.beta.network import BaseHubListener

class MetricsListener(BaseHubListener):
    async def on_envelope_posted(self, envelope):
        statsd.incr(f"net.envelope.{envelope.event_type}")
    async def on_inbox_pressure(self, agent_id, pending, cap):
        statsd.gauge(f"net.inbox.{agent_id}", pending / cap)
    async def on_turn_failed(self, agent_id, channel_id, error):
        sentry.capture_exception(error)

hub.register_listener(MetricsListener())     # hub.unregister_listener(inst) to detach
```

Two related hub-subclass seams:

- **`on_*` hooks on `Hub` itself** — the same method set exists as empty methods on `Hub`; a `Hub` subclass can override them directly (the fan-out invokes the bound method alongside registered listeners). Use a subclass when the observability *is* the hub variant you're shipping; use `register_listener` for pluggable add-ons.
- **`hub.register_sweeper(name, interval_seconds, fn)` / `unregister_sweeper(name)`** — adds your own periodic coroutine to the hub's interval-sweeper machinery (alongside the built-in TTL and expectation sweepers). Subclass-registered sweepers start immediately if `Hub.start()` has already run, otherwise queue until it does.

`on_inbox_pressure` is governed by `LimitsBlock.inbox.high_water` — an absolute pending-count threshold (`int | None`). `None` (the default) auto-resolves to `int(inbox.max_pending * 0.8)`; `0` disables the signal. It's the early-warning sibling of the hard `InboxFull` (which is `InboxBlock.max_pending` itself, enforced by the arbiter).

## Task observation — building the track record

Capability-tagged tasks update an agent's `Resume.observed[capability]` automatically. This is how the network knows that "bob has completed 47 research tasks at a 4.2s median latency."

### Tagging a task

`agent.task(..., capability="X")` accepts a free-form capability string:

```python
# `.tool` and `.task(...)` live on `Agent`, not on the `AgentClient` returned
# by `hc.register(...)`. So decorate the Agent before registering.
@worker_agent.tool
async def research(topic: str, ctx: Context) -> str:
    async with worker_agent.task(
        f"research: {topic}",
        capability="research",
        context=ctx,
    ) as task:
        await task.progress({"step": "gather"})
        # ... do work ...
        await task.complete({"items_found": 7})
    return "researched"


worker = await worker_hc.register(worker_agent, Passport(name="worker"), Resume())
```

Pass `context=ctx` so the task fires its events on the LLM-turn's stream — that's the stream the `TaskMirror` is attached to. Without it, the events never reach the hub.

If `capability=None` (the default), lifecycle events still go to the hub's audit log but `Resume.observed` is **not** updated. Track record is opt-in.

### Reading the track record

```python
resume = await hub.get_resume(bob.agent_id)
stat = resume.observed.get("research")
if stat:
    print(f"completed={stat.completed}/{stat.n}  "
          f"failed={stat.failed}  "
          f"p50_latency={stat.p50_latency_ms}ms")
```

```python
@dataclass(slots=True)
class ObservedStat:
    n: int = 0                        # total terminal events
    completed: int = 0
    failed: int = 0
    expired: int = 0
    p50_latency_ms: int | None = None  # rolling median of started_at → completed_at
```

Latency is computed from `task_meta.started_at` to the terminal event time, using the hub's clock. With a `MockClock` in tests you can construct deterministic latencies.

### Where `TaskMirror` fits

The default handler auto-attaches a `TaskMirror` per turn, scoped to the active channel. The mirror subscribes to `TaskStarted` / `TaskProgress` / `TaskCompleted` / `TaskFailed` / `TaskExpired` events on the LLM-turn's stream, forwards each as an `ag2.task.*` envelope to the hub, and on terminal events with a `capability` tag calls `Hub.record_observation(...)` to update `Resume.observed`.

You only attach `TaskMirror` manually if you've written a custom handler:

```python
from autogen.beta.network import TaskMirror
from autogen.beta.stream import MemoryStream

mirror = TaskMirror(
    hub_client=client._hub_client,
    owner_id=client.agent_id,
    channel_id=metadata.channel_id,
)
stream = MemoryStream()
sub_ids = mirror.attach(stream)
try:
    await client.agent.ask(text, stream=stream)
finally:
    mirror.detach(stream, sub_ids)
```

The mirror is attached **per turn**, not per agent — a new one for each inbound envelope. It also swallows hub-forwarding errors silently; a flaky hub connection should not crash the LLM turn.

### When to skip the capability tag

Tag only when:

- The task represents a **capability you want to track** in the agent's resume.
- Failure / latency signals are **operationally meaningful** (driving routing, alerting, peer ranking).

Untagged tasks still get full lifecycle audit records — just no `Resume.observed` update. Use untagged tasks for internal book-keeping that doesn't represent an externally-visible capability.

### Cross-cutting pattern: multi-capability worker

```python
@worker_agent.tool
async def research(topic: str, ctx: Context) -> str:
    async with worker_agent.task(f"research: {topic}", capability="research", context=ctx) as t:
        # ...
    return "..."


@worker_agent.tool
async def summarise(text: str, ctx: Context) -> str:
    async with worker_agent.task("summarise", capability="summarisation", context=ctx) as t:
        # ...
    return "..."
```

After a few channels, `worker.resume.observed` holds both `"research"` and `"summarisation"` `ObservedStat`s, each tracked independently. A peer-discovery query (`peers(action="find", capability="research")` — see `ag2-network-tools-and-views`) can then rank by latency or completion rate.

## Reading hub state

| Call | Returns |
|---|---|
| `await hub.get_channel(channel_id)` | `ChannelMetadata` snapshot (state, participants, close_reason) |
| `await hub.get_resume(agent_id)` | Current `Resume` (including `observed`) |
| `await hub.get_passport(agent_id)` | Current `Passport` |
| `await hub.list_agents(kind=None)` | Registered passports; `kind="agent"` / `"human"` / `"remote_agent"` filters by `Passport.kind` |
| `await hub.read_wal(channel_id)` | Ordered list of `Envelope`s in that channel |
| `await hub._audit_log.read_all()` | Every audit record |
| `hub.arbiter` | The active `HubArbiter` (read-only) |

The hub stamps `Resume.last_updated` on every mutation, so you can detect stale views by comparing timestamps. For *push* (vs. these *pull* calls), register a `HubListener`.

## Quick reference — imports

```python
from autogen.beta.network import (
    # Identity
    Passport, Resume, ResumeExample, ObservedStat,
    # Rules
    Rule, AccessBlock, LimitsBlock, RateBlock, InboxBlock,
    ChannelTypeAccess, parse_duration,
    # Arbiter (swappable access / routing seam)
    HubArbiter, BaseHubArbiter, RuleBasedArbiter, Allow, Deny,
    # Listeners (live observability)  — hub.register_listener(...)
    HubListener, BaseHubListener,
    # Auth
    AuthAdapter, AuthRegistry, AuthBlock, AuthError, NoAuth,
    # Expectations
    Expectation,
    ExpectationEvaluator,
    AcksWithinEvaluator, ReplyWithinEvaluator, MaxSilenceEvaluator,
    AuditHandler, NotifyChannelHandler, AutoCloseHandler,
    Violation, ViolationHandler,
    default_evaluators, default_handlers,
    # Audit kinds
    AUDIT_KIND_AGENT_REGISTERED,
    AUDIT_KIND_AGENT_UNREGISTERED,
    AUDIT_KIND_RESUME_SET,
    AUDIT_KIND_SKILL_SET,
    AUDIT_KIND_RULE_SET,
    AUDIT_KIND_CHANNEL_CREATED,
    AUDIT_KIND_CHANNEL_CLOSED,
    AUDIT_KIND_CHANNEL_EXPIRED,
    AUDIT_KIND_TASK_TERMINATED,
    AUDIT_KIND_EXPECTATION_VIOLATED,
    RESUME_SOURCE_OBSERVED, RESUME_SOURCE_TENANT,
    # Task observation
    TaskMirror,
    # Errors
    AccessDeniedError, AuthError, InboxFull,
)
```
