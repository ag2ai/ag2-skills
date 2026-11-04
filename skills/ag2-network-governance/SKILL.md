---
name: ag2-network-governance
description: "Govern an AG2 multi-agent network hub-side: identity (`Passport`, `Resume`, `ObservedStat`), per-agent `Rule` (`AccessBlock`, `LimitsBlock` with nested `RateBlock` / `InboxBlock`), the swappable `HubArbiter` / `RuleBasedArbiter` / `BaseHubArbiter` (`Allow` / `Deny`) gate, `AuthRegistry` / `AuthAdapter` / `ApiKeyAuth` / `NoAuth` registration auth, channel `Expectation`s with `ExpectationEvaluator` / `Violation` and `audit` / `notify_channel` / `auto_close` handlers, the append-only `hub.audit_log` with `AUDIT_KIND_*` constants, live `HubListener` / `BaseHubListener` fan-out, `register_sweeper`, and capability track records via `TaskMirror` + `Task(owner_id=..., spec=TaskSpec(capability=...))` or `agent.ask(..., capability=...)` (replaces deprecated `agent.task`). Use for access policy, inbox caps, SLAs/timeouts, compliance trails, metrics/alerting, peer ranking by track record. Load after `ag2-network-quickstart`; for handlers, views, LLM network tools and envelopes see `ag2-network-tools-and-views`."
license: Apache-2.0
---

# AG2 Network — Governance

Everything hub-side: identity, rules, the arbiter, auth, expectations, audit, listeners, and capability track records. Every send passes through the hub, so every policy is checked and every observation is read there.

> Prerequisite: `ag2-network-quickstart` (`Hub.open`, `HubClient.register`, channel lifecycle).

## When to use

- Restrict who talks to whom, cap inboxes / concurrent channels / delegation depth (`Rule`)
- Custom access logic — JWT scopes, tenant quotas, federation (`HubArbiter`)
- Authenticate agents at registration (`AuthRegistry`, `AuthAdapter`)
- Channel SLAs and auto-close timing (`Expectation`, evaluators, handlers)
- Compliance queries (`hub.audit_log`), live metrics/alerts (`HubListener`), periodic jobs (`register_sweeper`)
- Track which agent demonstrably does a capability well (`Resume.observed`)

## Rules

```python
from ag2.network import AccessBlock, InboxBlock, LimitsBlock, Rule

rule = Rule(
    access=AccessBlock(outbound_to=["bob", "research-*"], inbound_from=["*"]),  # globs over Passport.name
    limits=LimitsBlock(
        max_concurrent_channels=5, delegation_depth=2, channel_ttl_default="4h",
        inbox=InboxBlock(max_pending=100),  # high_water=None -> 80% early warning
    ),
)
alice = await hc.register(agent, Passport(name="alice", owner="acme"), Resume(), rule=rule)
await hub.set_rule(alice.agent_id, rule)  # later change; audited as AUDIT_KIND_RULE_SET
```

Violations raise from `open(...)` / `send(...)`: `AccessDeniedError` (access, depth, concurrency) or `InboxFull`. Denied sends are **not** audited — they reach `HubListener.on_envelope_rejected`.

**Stored but not enforced** by the default `RuleBasedArbiter`: `access.channel_types` and `limits.rate`. Enforce them in a custom arbiter. Field reference: [references/rules-arbiter-auth.md](references/rules-arbiter-auth.md).

## Arbiter and auth

```python
from ag2.network import BaseHubArbiter, Deny, RuleBasedArbiter


class ScopedArbiter(BaseHubArbiter):  # allows every gate you don't override -> re-delegate the others
    def __init__(self, inner):        # (authorize_register/_channel_open/_inbox/_dispatch) to keep rule checks
        self._inner = inner

    async def authorize_send(self, envelope, sender, sender_rule, recipients):
        if sender.name in BLOCKED:
            return Deny("missing net.send scope")  # -> AccessDeniedError to the caller
        return await self._inner.authorize_send(envelope, sender, sender_rule, recipients)


hub.register_arbiter(ScopedArbiter(RuleBasedArbiter()))
```

Auth: `Hub.open(store, auth=AuthRegistry([NoAuth(), ApiKeyAuth({"carol": "k1"}), MyAdapter()]))`; the passport selects one with `Passport(auth=AuthBlock(scheme="api_key", claim={"token": "k1"}))`; adapters raise `AuthError`. The hub stores passports **without** claims, so validate against your own secrets. Details, a full HMAC adapter and wire-security notes: [references/rules-arbiter-auth.md](references/rules-arbiter-auth.md).

## Expectations

Adapter manifests declare SLAs evaluated by the sweeper (`expectation_sweep_interval`, default 10s): `consulting` auto-closes on `acks_within(30s)` / `reply_within(600s)`; `conversation` audits `max_silence(3600s)`. Built-in handlers: `audit`, `notify_channel` (posts `EV_EXPECTATION_VIOLATED`), `auto_close`. The `turn_within` / `warn` / `hide` names on discussion/workflow manifests have **no** built-in implementation, and `knobs={"expectations": ...}` is ignored. Customise by registering an adapter with your own `manifest.expectations` plus `hub.register_expectation_evaluator(...)`. See [references/expectations-audit-listeners.md](references/expectations-audit-listeners.md).

## Audit and listeners

```python
from ag2.network import AUDIT_KIND_TASK_TERMINATED, BaseHubListener

records = await hub.audit_log.read_all()  # dicts: kind, at, + kind-specific ids
ended = [r for r in records if r["kind"] == AUDIT_KIND_TASK_TERMINATED]


class Alerts(BaseHubListener):  # override only what you need; all async
    async def on_turn_failed(self, channel_id, agent_id, envelope_id, exc): ...
    async def on_inbox_pressure(self, agent_id, pending, cap): ...


hub.register_listener(Alerts())
```

Audit kinds: `AGENT_REGISTERED` / `AGENT_UNREGISTERED` / `RESUME_SET` / `SKILL_SET` / `RULE_SET` / `CHANNEL_CREATED` / `CHANNEL_CLOSED` / `CHANNEL_EXPIRED` / `EXPECTATION_VIOLATED` / `TASK_TERMINATED` / `TURN_FAILED` (all `AUDIT_KIND_*`). Records carry ids (`agent_id`, `channel_id`, `task_id`), not tenant names. Field tables, `audit_log.subscribe`, and `register_sweeper` / `await unregister_sweeper` are in the reference.

## Capability track records

Capability-tagged task terminals update `Resume.observed[cap]` (`ObservedStat`: `n`, `completed`, `failed`, `expired`, `p50_latency_ms`) and write `AUDIT_KIND_TASK_TERMINATED`. `Agent.task(...)` is deprecated (removed in 1.4.0). Use `Task` from `ag2.task`:

```python
from ag2 import Context
from ag2.task import Task, TaskSpec


@worker_agent.tool
async def research(topic: str, ctx: Context) -> str:
    """Research a topic."""
    async with Task(
        owner_id=worker_agent.name,
        spec=TaskSpec(title=f"research: {topic}", capability="research"),
        context=ctx,  # required: the default handler's TaskMirror listens on this stream
    ) as task:
        await task.progress({"step": "gather"})
        result = f"notes on {topic}"
    return result


stat = (await hub.get_resume(worker.agent_id)).observed.get("research")
```

For a whole model run, call `agent.ask(..., capability="x", ttl_seconds=60)` on a stream with a `TaskMirror` attached, either in a custom handler or a script. The default handler does not pass `capability`. `delegate(capability=...)` only records a channel knob. More: [references/task-observation.md](references/task-observation.md).

## Reading hub state

`await hub.get_agent(id_or_name)` (Passport), `get_resume`, `get_rule`, `get_skill`, `list_agents(kind="agent"|"human"|"remote_agent")`, `get_channel`, `list_channels`, `read_wal`, `get_task`, `list_tasks`, plus `hub.arbiter`, `hub.auth_schemes`, `hub.health()`.

## Reference

- [references/rules-arbiter-auth.md](references/rules-arbiter-auth.md): `Rule` fields and their enforcement, the arbiter protocol, auth adapters, 1.3.0 wire security
- [references/expectations-audit-listeners.md](references/expectations-audit-listeners.md): evaluators, handlers, custom expectations, audit record fields, listener methods, sweepers
- [references/task-observation.md](references/task-observation.md): `Task` / `ask(capability=)` / `TaskMirror`, migration from `agent.task`
- Upstream: `website/docs/user-guide/network/expectations_and_audit.mdx`, `task_observation.mdx`, `hub_and_identity.mdx`; `website/docs/user-guide/tasks.mdx`
