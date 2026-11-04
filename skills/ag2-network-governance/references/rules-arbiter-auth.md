# Rules, arbiter and authentication (verified against ag2 1.3.0)

## `Rule` — field reference

```python
from ag2.network import (
    AccessBlock, ChannelTypeAccess, InboxBlock, LimitsBlock, RateBlock, Rule,
)

rule = Rule(
    access=AccessBlock(
        outbound_to=["bob", "research-*"],   # fnmatch globs over Passport.name; default ["*"]
        inbound_from=["*"],                  # who may address / invite this agent
        channel_types=ChannelTypeAccess(initiate=["*"], accept=["*"]),  # stored, NOT enforced (see below)
    ),
    limits=LimitsBlock(
        max_concurrent_channels=0,   # 0 disables; checked at channel open (creator)
        max_concurrent_tasks=0,      # 0 disables; checked when the hub first observes a task
        channel_ttl_default="2h",    # default TTL for channels this agent creates
        task_ttl_default="15m",
        delegation_depth=5,          # envelope.depth cap on sends; 0 disables
        rate=RateBlock(per_minute=0, burst=0),                       # stored, NOT enforced
        inbox=InboxBlock(max_pending=1000, overflow="reject", high_water=None),
    ),
)
```

| Check | Where (`RuleBasedArbiter`) | Error |
|---|---|---|
| `access.outbound_to` | `authorize_send` (every recipient, including the invite sent at channel open) | `AccessDeniedError` |
| `access.inbound_from` | `authorize_channel_open` (invitee pre-flight) + `authorize_dispatch` | `AccessDeniedError` |
| `limits.delegation_depth` | `authorize_send` (`envelope.depth > cap`) | `AccessDeniedError` |
| `limits.max_concurrent_channels` | `authorize_channel_open` | `AccessDeniedError` |
| `limits.max_concurrent_tasks` | `Hub.observe_task` (not the arbiter) | `AccessDeniedError` |
| `limits.inbox.max_pending` | `authorize_inbox` | `InboxFull` |
| `access.channel_types`, `limits.rate` | **nowhere** — data only. Enforce them in a custom arbiter. | — |

`InboxBlock.overflow` accepts `"drop_oldest"` / `"drop_newest"` but only `"reject"` is implemented.
`high_water=None` resolves to `int(max_pending * 0.8)`; `0` disables `on_inbox_pressure`.

`parse_duration("30s"|"15m"|"4h"|"2d")` → int seconds; a bare integer string or `int` is seconds; `""` → 0; unknown unit → `ValueError`.

`await hub.set_rule(agent_id, rule)` replaces the rule (audit `AUDIT_KIND_RULE_SET`, listener `on_agent_event(..., "rule_set", ...)`); `await hub.get_rule(agent_id)` reads it.

A denied send never reaches the WAL and is **not** written to the audit log; it fans out to `HubListener.on_envelope_rejected(envelope, reason)` (also for the invite of a denied channel open).

## `HubArbiter`

Protocol methods (all `async`), each returning `Allow()` or `Deny(reason, error=<NetworkError subclass>)` (default error `AccessDeniedError`):

| Method | Consulted before |
|---|---|
| `authorize_register(passport, resume, rule)` | committing a registration |
| `authorize_channel_open(manifest, creator, creator_rule, invitees, invitee_rules, active_creator_channels)` | creating a channel |
| `authorize_send(envelope, sender, sender_rule, recipients)` | WAL append |
| `authorize_inbox(envelope, recipient, recipient_rule, current_pending)` | enqueue into a recipient inbox |
| `authorize_dispatch(envelope, sender, recipient, recipient_rule)` | each delivery |
| `resolve_unknown_audience(envelope, unknown_ids)` | dispatch to unknown ids; `None` = drop (default) or a replacement id list (federation hook) |

`BaseHubArbiter` returns `Allow()` everywhere — a subclass that overrides one gate *allows* all others, so wrap a `RuleBasedArbiter()` to keep rule enforcement:

```python
from ag2.network import AccessDeniedError, Allow, BaseHubArbiter, Deny, HubArbiter, RuleBasedArbiter


class ChannelTypeArbiter(BaseHubArbiter):
    """Enforce AccessBlock.channel_types (the default arbiter doesn't) on top of the rule checks."""

    def __init__(self, inner: HubArbiter) -> None:
        self._inner = inner

    async def authorize_channel_open(self, manifest, creator, creator_rule, invitees, invitee_rules,
                                     active_creator_channels):
        allowed = creator_rule.access.channel_types.initiate
        if "*" not in allowed and manifest.type not in allowed:
            return Deny(f"{creator.name} may not initiate {manifest.type}")
        return await self._inner.authorize_channel_open(
            manifest, creator, creator_rule, invitees, invitee_rules, active_creator_channels)

    async def authorize_send(self, envelope, sender, sender_rule, recipients):
        return await self._inner.authorize_send(envelope, sender, sender_rule, recipients)

    async def authorize_inbox(self, envelope, recipient, recipient_rule, current_pending):
        return await self._inner.authorize_inbox(envelope, recipient, recipient_rule, current_pending)

    async def authorize_dispatch(self, envelope, sender, recipient, recipient_rule):
        return await self._inner.authorize_dispatch(envelope, sender, recipient, recipient_rule)


hub.register_arbiter(ChannelTypeArbiter(RuleBasedArbiter()))  # one active arbiter; replaces the previous
assert isinstance(hub.arbiter, ChannelTypeArbiter)
```

The arbiter is the gatekeeper (before the state change); `HubListener` is the observer (after). `AuthAdapter` authenticates credentials once at registration / re-attach; the arbiter authorizes actions throughout.

## Authentication

`AuthRegistry([...adapters])` keyed by each adapter's `scheme`. Default: `AuthRegistry.default()` = `NoAuth` only (scheme `"none"`, accept all). The passport selects the scheme with `Passport(auth=AuthBlock(scheme=..., claim={...}))`.

```python
import hmac

from ag2.knowledge import MemoryKnowledgeStore
from ag2.network import ApiKeyAuth, AuthBlock, AuthError, AuthRegistry, Hub, NoAuth, Passport


class HMACAuth:
    scheme = "hmac"

    def __init__(self, secret: bytes) -> None:
        self._secret = secret

    def sign(self, name: str) -> str:
        return hmac.new(self._secret, name.encode(), "sha256").hexdigest()

    async def validate(self, passport: Passport, claim: dict) -> None:
        # Validate against YOUR secrets — the hub never stores claims (see below).
        if not hmac.compare_digest(self.sign(passport.name), claim.get("token", "")):
            raise AuthError(f"bad hmac for {passport.name}")


hm = HMACAuth(b"s3cret")
hub = await Hub.open(
    MemoryKnowledgeStore(),
    auth=AuthRegistry([NoAuth(), hm, ApiKeyAuth({"carol": "k1"})]),  # ApiKeyAuth(keys=..., resolver=async fn)
)
hub.auth_schemes  # ['none', 'hmac', 'api_key']

alice = await hc.register(agent, Passport(name="alice",
                          auth=AuthBlock(scheme="hmac", claim={"token": hm.sign("alice")})), Resume())
# bad claim -> AuthError from register()
```

1.3.0 behaviour worth knowing:

- The hub stores the passport **without** `auth.claim` (`(await hub.get_agent(id)).auth.claim == {}`), and wire responses never carry claims. A cross-process client re-attaching an identity registered under a credentialed scheme must pass its passport (with the credential) to `HubClient.attach(...)`.
- A `HelloFrame` re-attach must present the identity's registered scheme.
- Names shaped like an agent id (32 hex chars) or equal to an existing agent id are rejected (`ProtocolError`).
- `Hub.open(..., allow_remote_agent_registration=False)` (default): wire clients can't register `kind="remote_agent"` identities unless you opt in; when allowed, the registering connection's agents become its owners.
- Over the wire, every control-plane op is scoped to the identities bound to that connection (agent-, channel- and task-scoped checks); `fire_task_event` over the wire accepts only the mirror's `mirror_failed` report.
