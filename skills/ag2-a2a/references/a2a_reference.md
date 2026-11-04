# ag2.a2a reference (AG2 1.3.0, a2a-sdk >= 1.3)

## `A2AServer(agent, **kw)`

| kw | purpose |
|---|---|
| `extended_card` / `card_modifier` / `extended_card_modifier` | authenticated extended card; async per-request card rewrites |
| `card_signer` | JWS-sign every served card (all transports; modifier output re-signed) |
| `task_store` | `TaskStore` (default one `InMemoryTaskStore` shared by all `build_*`) or `VersionedTaskStore` for replicas |
| `event_stream` | `TaskEventStream`; required with a `VersionedTaskStore`, rejected without one |
| `push_config_store` / `push_sender` / `push_url_validator` | push notifications; validator = `async (url) -> bool`, needs a store |
| `executor` | custom a2a `AgentExecutor` |
| `validate_input_modes` | reject parts whose media type the card does not declare (`ContentTypeNotSupportedError`) |

Builders:
- `build_jsonrpc(*, url, card=None, rpc_url="/", card_url="/.well-known/agent-card.json", legacy_card_url="/.well-known/agent.json", card_cache_control=None)` → Starlette.
- `build_rest(*, url, card=None, path_prefix="", ..., card_cache_control=None)` → Starlette.
- `build_grpc(*, bind, grpc_url, card=None, options=(), server_credentials=None)` → `grpc.aio.Server` (you `start()` / `wait_for_termination()`).

One server shares its task store across transports. For one multi-binding card: `build_card(agent, url=..., transports=("jsonrpc","rest","grpc"), rest_url=..., grpc_url=...)` and pass `card=` to each builder.

`card_cache_control="public, max-age=3600"` sets `Cache-Control` on the card route (ETag/304 always on). Do not use `public` with a per-caller `card_modifier`.

## `build_card(agent, *, url, ...)`

`transports=("jsonrpc",)`, `rest_url`, `rest_path_prefix`, `grpc_url`, `version="1.0.0"`, `description` (defaults to system prompt, else agent name), `push_notifications`, `skills` (else auto from `SkillsToolkit`, else one agent-derived skill), `security=[Requirement...]`, `provider=AgentProvider(...)`, `documentation_url`, `icon_url`, `tenants={transport: tenant}`, `extensions=[AgentExtension(uri=, description=, required=)]`, `input_modes=[...]` (replaces derived list — keep the `application/vnd.ag2.*+json` types if validating).

Always declares `urn:ag2:client-tools:v1` (`required=False`); redeclaring it or duplicate URIs → `ValueError`. `a2a.types` objects are protobuf messages (use `CopyFrom`, attribute assignment).

## Security schemes (`ag2.a2a.security`)

```python
from a2a.types import AuthorizationCodeOAuthFlow, OAuthFlows
from ag2.a2a.security import api_key_scheme, bearer_scheme, oauth2_scheme, require

bearer = bearer_scheme(name="bearer")
apikey = api_key_scheme(name="apikey", key_name="X-API-Key", location="header")
oauth = oauth2_scheme(name="oauth", flows=OAuthFlows(authorization_code=AuthorizationCodeOAuthFlow(
    authorization_url="https://auth.example/authorize", token_url="https://auth.example/token",
    scopes={"read": "Read", "write": "Write"})))

card = build_card(agent, url=URL, security=[require(bearer), require(apikey)])          # OR
card = build_card(agent, url=URL, security=[require(bearer, oauth.with_scopes("read"))])  # AND, scoped
```

Also `http_auth_scheme`, `open_id_connect_scheme`, `mtls_scheme`. Declaration only — enforce on the app. Clients send credentials via `A2AConfig(headers={...})` or `interceptors=`.

## Push notifications and tasks (client helpers)

```python
from a2a.server.tasks import InMemoryPushNotificationConfigStore
from a2a.utils.push_url_validator import validate_push_notification_url
from ag2.a2a.push import A2APushConfig, create_push_notification_config
from ag2.a2a.tasks import cancel_task, get_task, list_tasks

server = A2AServer(agent, push_config_store=InMemoryPushNotificationConfigStore(),
                   push_url_validator=validate_push_notification_url)   # public http(s) only

cfg = A2AConfig(card_url="http://127.0.0.1:8000")
await create_push_notification_config(cfg, task_id, A2APushConfig(url="https://my-app.example/hook", token="secret"))
task = await get_task(cfg, task_id, history_length=10)
page = await list_tasks(cfg, page_size=20, include_artifacts=False)   # ListedTasks
await cancel_task(cfg, task_id)
```

Also `get_/list_/delete_push_notification_config`. These need a reachable server (or an in-process `httpx_client_factory`).

## Several replicas

```python
from a2a.server.cluster import DatabaseTaskEventStream, VersionedDatabaseTaskStore
from sqlalchemy.ext.asyncio import create_async_engine

engine = create_async_engine("postgresql+asyncpg://user:pw@db/a2a")   # or sqlite+aiosqlite (a2a-sdk[sqlite]) locally
task_store, event_stream = VersionedDatabaseTaskStore(engine), DatabaseTaskEventStream(engine)
await task_store.initialize(); await event_stream.initialize()
app = A2AServer(agent, task_store=task_store, event_stream=event_stream).build_jsonrpc(url="https://lb.example.com")
```

No sticky routing needed (executor is stateless). One cluster = one agent. Push stores are per-replica unless you use `DatabasePushNotificationConfigStore` + `BasePushNotificationSender`.

## Extensions

Server: `build_card(..., extensions=[AgentExtension(uri="urn:acme:billing:v1", required=True)])`. Client: `A2AConfig(extensions=["urn:acme:billing:v1"])`. Unadvertised URI or unactivated `required=True` extension → `A2AExtensionNotSupportedError` (`err.uris`) at connect time.

## Reply semantics

`reply.body` is the final model turn's text (artifacts in first-seen order, then a completion message). Server cancel → `A2ATaskCancelledError` with `err.task.artifacts`. HITL: a server task in `input_required` calls the client agent's `hitl_hook` (cap with `input_required_timeout`).

## Errors (`ag2.a2a.errors`)

`A2AError`, `A2ATaskTerminalError`, `A2ATaskCancelledError`, `A2AReconnectError`, `A2AInvalidCardError`, `A2AIncompatibleProtocolVersionError`, `A2AClientToolsNotSupportedError`, `A2AExtensionNotSupportedError`, `A2ACardSignatureError`, `A2AStaleCardSignatureError` (you passed a pre-signed `card=` and the server had to flip capability flags — use `card_signer=` instead).

## Events

`ag2.a2a.events`: `A2ATaskSnapshot`, `A2ATaskStatusUpdate`, `A2ATaskArtifactUpdate`, `A2ATextArtifact`, `A2AToolCallArtifact`, `A2AMessage` appear on the client agent's stream.

## Testing helpers (`ag2.a2a.testing`)

`make_test_client_factory(server, url=)`, `make_test_rest_client_factory(server, url=)`, `pick_free_port()` (for a real gRPC server), plus `ToolRoundsConfig`, `ObservedAgentExecutor`, `TaskStatusRecorder`, `TurnGate`.
