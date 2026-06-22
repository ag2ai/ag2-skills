---
name: ag2-a2a
description: Expose an AG2 beta `Agent` over the Agent-to-Agent (A2A) protocol so any A2A-compliant client (or another AG2 agent) can call it across process/host boundaries. Wrap the agent with `A2AServer(agent)` and serve it via `build_jsonrpc(...)`, `build_rest(...)`, or `build_grpc(...)` — each returns a ready-to-serve ASGI/gRPC object that publishes an A2A `AgentCard` (built by `build_card`) at `/.well-known/agent-card.json`. Use when you want an AG2 agent reachable as a standard networked A2A service — interop with non-AG2 A2A clients, multi-transport (JSON-RPC / REST / gRPC) endpoints, declared auth schemes, push notifications, or multi-tenancy. To consume a remote A2A agent from AG2 instead, point an `Agent` at `A2AConfig(card_url=...)`. Covers `A2AConfig`, `A2AServer`, `build_card`, security schemes (`bearer_scheme`/`api_key_scheme`/`oauth2_scheme`/`require`), and the in-process `testing.py` helpers (`make_test_client_factory`, `make_test_rest_client_factory`).
license: Apache-2.0
---

# A2A (Agent-to-Agent) integration

Expose an AG2 beta `Agent` over the [A2A protocol](https://a2a-protocol.org/) — a standard JSON-RPC / REST / gRPC contract plus a discoverable `AgentCard` — so other systems can call your agent over the network. The same module also lets an AG2 `Agent` *consume* a remote A2A agent by using `A2AConfig` as the agent's model config.

## When to use

- You want an AG2 agent reachable as a standard networked service that **non-AG2** A2A clients can call (interop), not just from Python.
- You need a **discoverable** agent: a published `AgentCard` at `/.well-known/agent-card.json` describing the agent's skills, transports, auth, and capabilities.
- You want a choice of transport (**JSON-RPC**, **REST**, or **gRPC**) — possibly several at once from one server.
- You need declared **auth schemes** (bearer / API key / OAuth2 / OIDC / mTLS), **push notifications**, or **multi-tenancy** surfaced on the card.
- Conversely, you want one AG2 agent to **call** a remote A2A agent as if it were an LLM provider — use `A2AConfig` as the calling agent's `config`.

If you only need a web frontend (React/CopilotKit) in front of an agent, use the AG-UI skill instead. If two AG2 agents simply need to talk inside one process/cluster, the AG2 network is the standard multi-agent pattern.

## Installation

```bash
pip install "ag2[a2a]"
```

> Required. The `a2a` extra pulls in `a2a-sdk` (with the HTTP server). gRPC needs the `a2a-sdk[grpc]` extra in addition. If you cannot run commands, state the exact `pip install` command.

Public API (`from autogen.beta.a2a import ...`): `A2AConfig`, `A2AServer`, `build_card`.

## 60-second recipe — serve an agent over A2A

`A2AServer.build_jsonrpc(...)` returns a ready-to-serve **Starlette** ASGI app. Run it directly with uvicorn:

```python title="serve_a2a.py"
from autogen.beta import Agent
from autogen.beta.a2a import A2AServer
from autogen.beta.config import OpenAIConfig

agent = Agent(
    name="weather_bot",
    prompt="You answer questions about the weather.",
    config=OpenAIConfig(model="gpt-4o-mini"),
)

server = A2AServer(agent)
# returns a Starlette ASGI app; `url` is the public URL written into the card
app = server.build_jsonrpc(url="http://localhost:8000")
```

```bash
uvicorn serve_a2a:app --host 0.0.0.0 --port 8000
```

The agent card is then served at `http://localhost:8000/.well-known/agent-card.json` and JSON-RPC requests at `/`.

To combine with other routes, mount the returned app under a parent Starlette (or FastAPI) app:

```python
from starlette.applications import Starlette
from starlette.routing import Mount

a2a_app = server.build_jsonrpc(url="http://localhost:8000/a2a")
parent = Starlette(routes=[Mount("/a2a", app=a2a_app)])
```

> A2A has no middleware spec — attach CORS / auth / tracing to the returned Starlette app yourself.

## Calling a served agent from another AG2 agent

`A2AConfig` is a `ModelConfig`: point an `Agent` at a remote card URL and `ask()` it like any agent. The client fetches the card from `{card_url}/.well-known/agent-card.json`, picks a transport, and runs the task.

```python
from autogen.beta import Agent
from autogen.beta.a2a import A2AConfig

remote = Agent(
    "remote",
    config=A2AConfig(card_url="http://localhost:8000"),
)
reply = await remote.ask("What is the weather in Paris?")
print(await reply.content())
```

Key `A2AConfig` options (all keyword, with defaults):

| Option | Default | Purpose |
|---|---|---|
| `card_url` | (required) | Base URL; card fetched from `{card_url}/.well-known/agent-card.json`. |
| `prefer` | `None` | Force a transport when the card lists several: `"jsonrpc"`, `"rest"`, or `"grpc"`. `None` auto-picks. |
| `streaming` | `True` | Stream the task; set `False` to poll `get_task` every `polling_interval`s instead. |
| `headers` | `None` | Extra HTTP headers sent on every request (e.g. an auth token). |
| `timeout` | `60.0` | Per-request timeout (seconds). |
| `polling_interval` | `0.5` | Poll interval (s) when streaming is off / unsupported. |
| `input_required_timeout` | `None` | Cap on waiting for the HITL hook on `TASK_STATE_INPUT_REQUIRED`; `None` = wait forever. |
| `tenant` | `None` | Scope every request to a server-side tenant (override per-call via `context.variables["a2a:tenant"]`). |
| `history_length` | `None` | Server-side hint: truncate echoed `Task.history` to the most recent N messages. |
| `httpx_client_factory` | `None` | Supply a custom `httpx.AsyncClient` factory (used by the in-process test helpers below). |
| `preset_card` | `None` | Use an already-fetched `AgentCard` to skip the discovery round-trip (see `A2AConfig.from_card(...)`). |

## The agent card — `build_card`

`A2AServer` builds a card automatically, but call `build_card` directly to customise it and pass the result via `card=` to a `build_*` method.

```python
from a2a.types import AgentProvider, AgentSkill

from autogen.beta.a2a import build_card

card = build_card(
    agent,
    url="http://localhost:8000",
    transports=("jsonrpc",),          # one AgentInterface per binding
    version="2.1.0",
    description="Customer support agent",
    push_notifications=True,
    skills=[
        AgentSkill(
            id="refunds",
            name="Process refunds",
            description="Issue refunds for orders",
            tags=["billing"],
        ),
    ],
    provider=AgentProvider(organization="Acme", url="https://acme.example"),
    documentation_url="https://acme.example/docs",
)
```

Card behaviour worth knowing:

- **Skills**: if you omit `skills`, `build_card` auto-detects any `SkillsToolkit` on `agent.tools` and publishes its local skills; if there are none it falls back to a single skill derived from `agent.name` / the system prompt, so the card stays spec-compliant.
- **Default description**: when `description` is omitted it uses the agent's system prompt.
- **Always declares** the `urn:ag2:client-tools:v1` extension as `required=False` (lets AG2 clients run client-side tools, falling back to plain text for non-AG2 clients).
- `transports=` accepts any of `("jsonrpc", "rest", "grpc")`. For `"grpc"` you must also pass `grpc_url=`.

## Auth — declaring security schemes

Build schemes with the factory helpers in `autogen.beta.a2a.security`, group them into `require(...)` requirements, and pass `security=[...]` to `build_card`. The card's `security_schemes` are auto-derived from the schemes you reference — no duplicate declarations.

```python
from autogen.beta.a2a import build_card
from autogen.beta.a2a.security import api_key_scheme, bearer_scheme, require

bearer = bearer_scheme(name="bearer")                               # Authorization: Bearer <JWT>
apikey = api_key_scheme(name="apikey", key_name="X-API-Key", location="header")

card = build_card(
    agent,
    url="http://localhost:8000",
    # list is OR-ed: present a bearer token OR an API key
    security=[require(bearer), require(apikey)],
)
```

Schemes in a single `require(...)` are **AND**-ed (all must be presented); multiple `require(...)` entries are **OR**-ed (any one suffices). OAuth2 / OIDC scopes attach via `.with_scopes(...)`:

```python
from a2a.types import AuthorizationCodeOAuthFlow, OAuthFlows

from autogen.beta.a2a.security import bearer_scheme, oauth2_scheme, require

flows = OAuthFlows(
    authorization_code=AuthorizationCodeOAuthFlow(
        authorization_url="https://auth.example/authorize",
        token_url="https://auth.example/token",
        scopes={"read": "Read access", "write": "Write access"},
    ),
)
oauth = oauth2_scheme(name="oauth", flows=flows)
bearer = bearer_scheme(name="bearer")

card = build_card(
    agent,
    url="http://localhost:8000",
    security=[require(bearer, oauth.with_scopes("read", "write"))],  # AND, scoped
)
```

Available scheme factories: `bearer_scheme`, `http_auth_scheme` (basic/digest/...), `api_key_scheme`, `oauth2_scheme`, `open_id_connect_scheme`, `mtls_scheme`. The card only **declares** the scheme — A2A defines no middleware, so you must **enforce** auth on the returned Starlette/gRPC object (e.g. a Starlette middleware that validates the header).

## Multiple transports from one server

One `A2AServer` shares a single task store across transports, so the same agent can be exposed several ways simultaneously:

```python
server = A2AServer(agent)
jsonrpc_app = server.build_jsonrpc(url="http://localhost:8000")
rest_app    = server.build_rest(url="http://localhost:8001", path_prefix="/v1")
grpc_srv    = server.build_grpc(bind="0.0.0.0:50051", grpc_url="localhost:50051")  # needs ag2[a2a] grpc extra
```

- `build_jsonrpc(url=...)` / `build_rest(url=..., path_prefix=...)` → **Starlette** ASGI apps (serve with uvicorn).
- `build_grpc(bind=..., grpc_url=...)` → a `grpc.aio.Server` you `start()`/`await` yourself (`bind` = listener address, `grpc_url` = public URL in the card; insecure binding only).

To consolidate the card into a single multi-binding card, call `build_card(agent, url=..., transports=("jsonrpc","rest","grpc"), grpc_url=...)` and pass it via `card=` to each builder.

## Push notifications

Pass a `PushNotificationConfigStore` to the server; the card's `capabilities.push_notifications` flips on automatically.

```python
from a2a.server.tasks import InMemoryPushNotificationConfigStore

from autogen.beta.a2a import A2AServer

server = A2AServer(agent, push_config_store=InMemoryPushNotificationConfigStore())
app = server.build_jsonrpc(url="http://localhost:8000")
```

**Client side** (consuming AG2 agent), register a webhook for a task via the helpers in `autogen.beta.a2a.push`:

```python
from autogen.beta.a2a import A2AConfig
from autogen.beta.a2a.push import A2APushConfig, create_push_notification_config

config = A2AConfig(card_url="http://localhost:8000")
await create_push_notification_config(
    config,
    task_id="task-123",
    push_config=A2APushConfig(url="https://my-app.example/webhook", token="secret"),
)
```

Companion helpers: `get_push_notification_config`, `list_push_notification_configs`, `delete_push_notification_config`. Task inspection helpers live in `autogen.beta.a2a.tasks`: `get_task`, `list_tasks` (returns a `ListedTasks` with pagination metadata), `cancel_task`.

> These client helpers require a **running, reachable** A2A server (real network round-trips), so they aren't exercised by the in-process tests below.

## Testing — in-process, no network, no API keys

`autogen.beta.a2a.testing` ships ASGI helpers that dispatch directly into the server app via `httpx.ASGITransport` — no socket, no port, no SSE proxy. Plug the factory into `A2AConfig.httpx_client_factory` and do a full round trip:

```python
import asyncio

from autogen.beta import Agent
from autogen.beta.a2a import A2AConfig, A2AServer
from autogen.beta.a2a.testing import make_test_client_factory
from autogen.beta.testing import TestConfig  # mock the served agent's LLM

async def main():
    served = Agent(
        name="weather_bot",
        prompt="You answer questions about the weather.",
        config=TestConfig("It is sunny in Paris."),  # no real LLM call
    )
    server = A2AServer(served)

    factory = make_test_client_factory(server, url="http://test")
    remote = Agent("remote", config=A2AConfig(card_url="http://test", httpx_client_factory=factory))

    reply = await remote.ask("What is the weather in Paris?")
    print(await reply.content())  # -> "It is sunny in Paris."

asyncio.run(main())
```

For REST use `make_test_rest_client_factory` and `prefer="rest"`:

```python
from autogen.beta.a2a.testing import make_test_rest_client_factory

factory = make_test_rest_client_factory(server, url="http://test")
remote = Agent("remote", config=A2AConfig(card_url="http://test", prefer="rest", httpx_client_factory=factory))
```

For gRPC (which has no in-process ASGI equivalent), `testing.pick_free_port()` finds a free TCP port to bind a real gRPC server in a test.

A runnable test that exercises every snippet in this skill lives at `assets/test_snippets.py`:

```bash
.venv/bin/python skills/ag2-a2a/assets/test_snippets.py
```

## Common pitfalls

- **Missing `a2a` extra** — `pip install "ag2[a2a]"`; without it `from autogen.beta.a2a import A2AServer` raises a "missing additional dependency" error. gRPC additionally needs the `a2a-sdk[grpc]` extra.
- **No FastAPI required** — `build_jsonrpc`/`build_rest` already return a Starlette ASGI app; serve it with uvicorn directly or mount it. Don't wrap an agent in a hand-rolled FastAPI route.
- **`url=` is the public URL, not the bind address** — it is written into the AgentCard interface entries, so clients must be able to reach it. For gRPC, `bind` is the listener and `grpc_url` is what clients connect to.
- **Auth is declared, not enforced** — `security=[...]` only advertises schemes on the card. A2A has no middleware spec; enforce auth on the returned Starlette/gRPC object yourself.
- **Stateless executor** — each request is a self-contained turn; the AG2 client sends its full history on every call. No sticky sessions are needed, but don't assume server-side per-task memory between turns.
- **`build_grpc` needs `grpc_url`** — and `build_card(..., transports=("grpc",))` raises `ValueError` if you forget `grpc_url=`.

## Going deeper (source of truth)

- `autogen/beta/a2a/server.py` — `A2AServer`, the `build_jsonrpc` / `build_rest` / `build_grpc` builders.
- `autogen/beta/a2a/card.py` — `build_card` and skill/security/interface resolution.
- `autogen/beta/a2a/config.py` — `A2AConfig`, `from_card`, all client options.
- `autogen/beta/a2a/security.py` — scheme factories and `require`.
- `autogen/beta/a2a/push.py` / `tasks.py` — client-side push + task helpers.
- `autogen/beta/a2a/testing.py` — in-process test factories.
- `autogen/beta/a2a/events.py` — `A2AEvent` family for observing wire events on the AG2 stream.
- A2A protocol: https://a2a-protocol.org/
