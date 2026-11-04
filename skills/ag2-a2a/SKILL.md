---
name: ag2-a2a
description: "Serve an AG2 `Agent` over the Agent-to-Agent (A2A) protocol (a2a-sdk >= 1.3) and call remote A2A agents. Server: `A2AServer(agent)` then `build_jsonrpc(url=)` / `build_rest(url=)` (Starlette ASGI apps for uvicorn) or `build_grpc(bind=, grpc_url=, server_credentials=)`, publishing an `AgentCard` (`build_card`) at `/.well-known/agent-card.json`. Client: `Agent(config=A2AConfig(card_url=...))`, multi-turn `reply.ask`, client-side tools, `as_tool()`. Covers card signing (`card_signer=` / `card_signature_verifier=`), gRPC TLS (`secure_grpc_channel_factory`), security schemes (`bearer_scheme`/`api_key_scheme`/`oauth2_scheme`/`require`), push notifications with `push_url_validator`, multi-replica `VersionedTaskStore`+`event_stream`, extensions, `validate_input_modes`, tasks/push helpers, in-process `ag2.a2a.testing` factories. Use for networked agent interop. For MCP see `ag2-mcp`; IDE agents `ag2-acp`; web frontends `ag2-ag-ui`; in-process multi-agent `ag2-network-quickstart`."
license: Apache-2.0
metadata:
  ag2_version: "1.3.0"
---

# A2A (Agent-to-Agent) integration

Expose an AG2 `Agent` as a standard A2A service (JSON-RPC / REST / gRPC + a discoverable `AgentCard`), or let an AG2 `Agent` call any remote A2A agent by using `A2AConfig` as its model config.

## When to use

- Make an AG2 agent a networked service that **non-AG2** A2A clients can discover (`AgentCard` at `/.well-known/agent-card.json`) and call over JSON-RPC, REST or gRPC.
- Declare auth schemes, push notifications or multi-tenancy on the card, or sign the card.
- Let an AG2 agent **call** a remote A2A agent as if it were a model (`A2AConfig` as its `config`).

Web frontend → `ag2-ag-ui`. AG2 agents talking inside one process/cluster → `ag2-network-quickstart`.

## Install

```bash
pip install "ag2[a2a]"                    # a2a-sdk>=1.3 — enough for the CLIENT
pip install "a2a-sdk[http-server]" uvicorn # + to SERVE build_jsonrpc / build_rest (uvicorn runs the ASGI app)
pip install "a2a-sdk[grpc]"               # + for build_grpc / secure_grpc_channel_factory
pip install "a2a-sdk[signing]"            # + for AgentCard JWS signing / verification
```

## Serve an agent

```python title="serve_a2a.py"
from ag2 import Agent
from ag2.a2a import A2AServer
from ag2.config import OpenAIResponsesConfig

agent = Agent(
    name="weather_bot",
    prompt="You answer questions about the weather.",
    config=OpenAIResponsesConfig(model="gpt-4.1-mini"),
)
# Starlette app; `url` is the PUBLIC URL written into the card (not the bind address)
app = A2AServer(agent).build_jsonrpc(url="http://localhost:8000")
```

`uvicorn serve_a2a:app --port 8000` → card at `/.well-known/agent-card.json`, JSON-RPC at `/`. Mount under a parent Starlette/FastAPI app with `Mount("/a2a", app=...)` (then use `url=".../a2a"`). Attach CORS/auth middleware to the returned app yourself — card `security=` only *declares* schemes.

## Call a remote A2A agent

```python
from ag2 import Agent
from ag2.a2a import A2AConfig

remote = Agent("remote", config=A2AConfig(card_url="http://localhost:8000"))
reply = await remote.ask("What is the weather in Paris?")
print(reply.body)                      # or: await reply.content()
reply = await reply.ask("And tomorrow?")   # multi-turn: full history is re-sent each call
```

- `A2AConfig` options: `prefer` (`"jsonrpc"|"rest"|"grpc"`), `streaming=True`, `headers`, `timeout=60.0`, `polling_interval`, `input_required_timeout`, `httpx_client_factory`, `grpc_channel_factory`, `preset_card` (`A2AConfig.from_card`), `card_signature_verifier`, `tenant`, `history_length`, `extensions`.
- **Client-side tools**: `@tool`-decorated functions on the *client* agent (`Agent(..., tools=[get_local_time])`) are advertised to the server and executed locally when the remote LLM calls them (AG2 servers only; else `A2AClientToolsNotSupportedError`).
- **As a tool**: `remote.as_tool(description=...)` lets a local orchestrator delegate.
- Conversation **variables** sync both ways (keys under `ag:`/`a2a:` stripped) — keep secrets in `dependencies`.

## In-process serve + consume (uvicorn, real socket)

```python
import asyncio, uvicorn

async def main():
    app = A2AServer(agent).build_jsonrpc(url="http://127.0.0.1:8000")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        reply = await Agent("remote", config=A2AConfig(card_url="http://127.0.0.1:8000")).ask("Hi")
        print(reply.body)
    finally:
        server.should_exit = True
        await task
```

Full runnable version (signed card, multi-turn, client tool): `scripts/serve_and_consume.py`.

## Card signing (opt-in, both halves independent)

```python
from a2a.utils.signing import create_agent_card_signer, create_signature_verifier

signer = create_agent_card_signer(private_pem, {"kid": "card-2026", "alg": "ES256", "jku": None, "typ": "JOSE"})
verifier = create_signature_verifier(lambda kid, jku: public_pem, ["ES256"])   # keep alg allow-list tight

app = A2AServer(agent, card_signer=signer).build_jsonrpc(url=url)      # signs on every transport, re-signs card_modifier output
remote = Agent("r", config=A2AConfig(card_url=url, card_signature_verifier=verifier))  # bad sig -> A2ACardSignatureError
```

## gRPC with TLS

```python
import grpc
from ag2.a2a import A2AConfig, build_card, secure_grpc_channel_factory

card = build_card(agent, url="https://agents.example.com", transports=("grpc",), grpc_url="grpcs://agents.example.com:50051")
grpc_server = A2AServer(agent).build_grpc(bind="0.0.0.0:50051", grpc_url="grpcs://agents.example.com:50051", card=card,
                                          server_credentials=grpc.ssl_server_credentials([(key_pem, cert_pem)]))
await grpc_server.start(); await grpc_server.wait_for_termination()

# client: grpcs:// / grpc+tls:// card URLs use system CAs automatically; private CA / mTLS:
creds = grpc.ssl_channel_credentials(root_certificates=ca_pem)
A2AConfig(card_url="https://agents.example.com", prefer="grpc", grpc_channel_factory=secure_grpc_channel_factory(creds))
```

`card_url` is always HTTP(S); only the message exchange uses gRPC. `build_grpc` without `server_credentials` binds insecure.

## Testing in-process (no sockets, no keys)

```python
from ag2.a2a.testing import make_test_client_factory     # REST: make_test_rest_client_factory + prefer="rest"
from ag2.testing import TestConfig

server = A2AServer(Agent(name="bot", config=TestConfig("It is sunny.")))
remote = Agent("remote", config=A2AConfig(card_url="http://test",
               httpx_client_factory=make_test_client_factory(server, url="http://test")))
assert (await remote.ask("Weather?")).body == "It is sunny."
```

## Pitfalls

- Serving without `a2a-sdk[http-server]` fails at `build_jsonrpc`/`build_rest`; `ag2[a2a]` alone is client-only.
- `url=` is the public URL in the card; behind a load balancer use the balancer's URL.
- Auth on the card is declarative; enforce it with middleware on the returned app.
- Several replicas need `task_store=VersionedDatabaseTaskStore(engine)` **and** `event_stream=DatabaseTaskEventStream(engine)` (either alone → `ValueError`); call both `.initialize()` first. Push config stores stay per-replica unless you share a DB store.
- `push_url_validator` without `push_config_store` → `ValueError`. Without a validator any webhook URL is accepted (SSRF risk) — pass `a2a.utils.push_url_validator.validate_push_notification_url`.
- `build_card(transports=("grpc",))` without `grpc_url=` → `ValueError`.
- Pass `@tool`-decorated functions (not bare functions) when adding tools per call via `ask(tools=[...])`.

## Reference

- `references/a2a_reference.md` — `build_card` options, security schemes, push/tasks helpers, multi-replica, extensions, `validate_input_modes`, card caching, errors.
- `scripts/test_snippets.py` — in-process tests (JSON-RPC, REST, card, security, push validator, signing) — no keys.
- `scripts/serve_and_consume.py` — uvicorn in one process, signed card, multi-turn, client tool (needs `OPENAI_API_KEY`).
- Upstream: `website/docs/user-guide/a2a/{overview,server,client,card_signing,tasks_and_push,advanced}.mdx`; source `ag2/a2a/`.
