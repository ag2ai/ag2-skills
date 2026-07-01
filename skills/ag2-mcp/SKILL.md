---
name: ag2-mcp
description: Host an MCP server that exposes an AG2 `Agent` (plus prompts and resources) to MCP clients like Claude Desktop, Cursor, or the MCP Inspector. Wrap the agent with `MCPServer(agent)` — it surfaces `Agent.ask()` as a single conversational tool and serves over stdio (`run_stdio()`) or streamable HTTP (it is itself an ASGI app for uvicorn). Covers `MCPServer`, `SessionConfig` (multi-turn history), `Prompt`/`PromptArgument`/`PromptMessage`, `Resource`/`ResourceTemplate`, `AskContext`/`ContextProvider` (per-request injection), `build_ask_tool`, OAuth2 `security=`, and in-process `testing.connect`/`testing.serve` helpers. Use when you want OTHER MCP clients to call YOUR agent. This is the SERVER side — for CONSUMING external MCP servers from an agent (client side) see `ag2-use-builtin-tools` (`MCPServerTool`).
license: Apache-2.0
---

# Serving an AG2 agent as an MCP server

`ag2.mcp.MCPServer` turns an AG2 `Agent` into a Model Context
Protocol **server**: MCP clients (Claude Desktop, Cursor, the MCP Inspector, or
any MCP-speaking app) connect and call your agent as a tool. It can also expose
**prompts** and **resources** alongside the agent.

## Server side vs. client side — read this first

There are two opposite directions, and this skill is only one of them.

| Direction | You want… | Use |
|---|---|---|
| **Server (this skill)** | other MCP clients to call **your** AG2 agent | `ag2.mcp.MCPServer` |
| **Client** | your AG2 agent to call an **external** MCP server's tools | `MCPServerTool` / MCP toolkits — see **`ag2-use-builtin-tools`** |

If the user says "let Claude Desktop talk to my agent", "publish my agent over
MCP", or "host an MCP endpoint" → this skill. If they say "give my agent the
GitHub MCP tools" or "connect to an MCP server" → `ag2-use-builtin-tools`.

## When to use

- Expose an AG2 agent so external MCP clients (Claude Desktop, Cursor, IDEs) can call it.
- Publish a single conversational `ask`-style tool that runs `Agent.ask()` and returns the reply.
- Serve reusable **prompts** (templates) and **resources** (files/config/dynamic data) over MCP.
- Need multi-turn history per client session, OAuth2-protected HTTP, or per-request context injection.

## Installation

```bash
pip install "ag2[mcp]"
```

> Required. Run this install before delivering the code. Without the `mcp`
> extra, `from ag2.mcp import MCPServer` resolves to a stub that raises
> a "missing optional dependency" error on use.

## 60-second recipe — serve an agent over stdio

This is the form local MCP clients (Claude Desktop, Cursor, MCP Inspector)
expect. The server reads/writes MCP frames over stdin/stdout.

```python title="serve_stdio.py"
import asyncio

from ag2 import Agent
from ag2.config import OpenAIConfig
from ag2.mcp import MCPServer

agent = Agent(
    name="assistant",
    prompt="You are a helpful assistant.",
    config=OpenAIConfig(model="gpt-4o-mini"),
)

# The agent is exposed as ONE conversational tool, named "ask" by default,
# taking a required `message` and an optional `context` string.
server = MCPServer(
    agent,
    name="assistant-mcp",                 # serverInfo.name in the handshake
    instructions="Ask me anything.",       # client-facing usage hint (NOT the agent prompt)
)

if __name__ == "__main__":
    asyncio.run(server.run_stdio())
```

Register it with a client (Claude Desktop `claude_desktop_config.json` shown;
Cursor / other clients use the same `command` + `args` shape):

```json
{
  "mcpServers": {
    "assistant": {
      "command": "python",
      "args": ["/absolute/path/to/serve_stdio.py"],
      "env": { "OPENAI_API_KEY": "sk-..." }
    }
  }
}
```

> The agent **must** have a model `config=` set. Serving an agent with no
> config raises `MCPAgentConfigError` on the first tool call.

## Serve over HTTP (streamable HTTP transport)

`MCPServer` is itself an ASGI3 application — hand it straight to `uvicorn`. It
manages its own lifespan (it runs the streamable-HTTP session manager), so a
standalone run just works.

```python title="serve_http.py"
import uvicorn

from ag2 import Agent
from ag2.config import OpenAIConfig
from ag2.mcp import MCPServer

agent = Agent(name="assistant", prompt="You help users.", config=OpenAIConfig(model="gpt-4o-mini"))

app = MCPServer(agent, path="/mcp")  # MCP endpoint mounted at /mcp

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

HTTP transport knobs (ignored over stdio):

| Param | Default | Effect |
|---|---|---|
| `path` | `"/mcp"` | URL path the MCP endpoint is served at. |
| `stateless` | `False` | When `True` the transport issues **no** `mcp-session-id`, so every call is stateless regardless of `sessions=`. |
| `json_response` | `False` | Return plain JSON instead of SSE for responses. |
| `security` | `None` | OAuth2 bearer enforcement (see below). |

## Customising the tool

By default the tool is named `ask` with an auto-generated description. Override:

```python
server = MCPServer(
    agent,
    tool_name="consult_expert",
    tool_description="Consult the expert agent about a question.",
    stream_progress=True,   # forward agent stream events as MCP progress/log notifications (default True)
)
```

The tool always takes a required `message` and an optional `context` string
(prepended to the message). Mirrors `Agent.as_tool()`'s shape.

## Prompts

Expose reusable prompt templates (MCP `prompts/list` + `prompts/get`). A
renderer receives the call arguments as a `{name: value}` dict and returns
either a plain `str` (becomes one `user` message) or a list of `PromptMessage`.
Renderers may be sync or async.

```python
from ag2.mcp import MCPServer, Prompt, PromptArgument, PromptMessage

def render_review(args: dict[str, str]) -> list[PromptMessage]:
    return [
        PromptMessage(role="user", text=f"Review this {args['language']} code:"),
        PromptMessage(role="user", text=args.get("code", "")),
    ]

server = MCPServer(
    agent,
    prompts=[
        Prompt(
            name="code_review",
            description="Generate a code-review prompt.",
            render=render_review,
            arguments=(
                PromptArgument(name="language", description="Programming language", required=True),
                PromptArgument(name="code", description="The code to review", required=False),
            ),
        ),
        # A bare-string renderer becomes a single user message.
        Prompt(name="greet", render=lambda args: f"Say hello to {args['who']}"),
    ],
)
```

The `prompts` MCP capability is advertised only when a non-empty list is passed.

## Resources

Expose static and templated resources (MCP `resources/list` + `resources/read`,
plus `resources/templates/list` for templates). `read` returns `str` (text) or
`bytes` (binary); sync or async.

```python
from pathlib import Path
from ag2.mcp import MCPServer, Resource, ResourceTemplate

server = MCPServer(
    agent,
    resources=[
        Resource(
            uri="config://app",
            name="app-config",
            description="Static app config.",
            mime_type="application/json",
            read=lambda: '{"env": "prod"}',
        ),
    ],
    resource_templates=[
        # RFC 6570 templates: {var} matches one path segment, {+var} spans '/'.
        ResourceTemplate(
            uri_template="file:///{+path}",
            name="file",
            description="Read a file by path.",
            read=lambda vars: Path(vars["path"]).read_text(),
        ),
    ],
)
```

`mime_type` defaults per the MCP SDK (`text/plain` for `str`,
`application/octet-stream` for `bytes`) when left `None`. The `resources`
capability is advertised only when at least one resource or template is given.

## Sessions — multi-turn history

By default (`sessions=True`) each MCP session (keyed by the transport's
`mcp-session-id` over HTTP, or a single per-process key over stdio) keeps its
own conversation history that **accumulates across `tools/call` invocations**.
Tune it with `SessionConfig`, or disable with `sessions=False` for fully
stateless calls.

```python
from ag2.mcp import MCPServer, SessionConfig

server = MCPServer(
    agent,
    sessions=SessionConfig(
        max_sessions=1024,   # LRU cap; least-recently-used session's history is dropped past the cap
        ttl=3600,            # optional idle-expiry in seconds (None = never expire)
        storage=None,        # pluggable history backend; defaults to in-memory MemoryStorage
    ),
)

# Or stateless — every call independent:
stateless = MCPServer(agent, sessions=False)
```

`storage` accepts any `ag2.history.Storage` (e.g. a Redis-backed store)
for cross-replica continuity. Note: a `stateless=True` **HTTP transport** issues
no session id, so it stays stateless regardless of `sessions=`.

## Structured output → `structuredContent`

If the agent has a `response_schema` that is an **object** schema (Pydantic
model / dataclass / dict), `MCPServer` advertises it as the tool's `outputSchema`
and returns validated `structuredContent` to clients. Scalar/union schemas
aren't advertised — those replies flow back as plain text.

```python
from pydantic import BaseModel
from ag2 import Agent
from ag2.config import OpenAIConfig
from ag2.mcp import MCPServer

class Weather(BaseModel):
    city: str
    temp_c: float

agent = Agent(name="weather", prompt="Report weather.", response_schema=Weather,
              config=OpenAIConfig(model="gpt-4o-mini"))
server = MCPServer(agent)
# tool.outputSchema is set; call results carry result.structuredContent == {"city": ..., "temp_c": ...}
```

## Per-request context — `AskContext` / `ContextProvider`

A `context_provider` is an **async** hook that runs per request. It receives the
authenticated bearer token (an `mcp.server.auth.provider.AccessToken`, or `None`
when unauthenticated) and returns an `AskContext` whose non-`None` fields are
passed straight into `Agent.ask()`. Use it to inject per-principal variables,
tools, or prompt — context the stateless executor otherwise omits.

```python
from typing import Any
from ag2.mcp import AskContext, ContextProvider, MCPServer

async def provide(token: Any) -> AskContext:
    # Resolve the caller from `token`, then scope the turn to them.
    tenant = "acme"  # e.g. token.scopes / a claims lookup
    return AskContext(
        variables={"tenant": tenant},   # -> Agent.ask(variables=...)
        tools=None,                      # -> Agent.ask(tools=...)  (None = leave default)
        prompt="Be concise.",           # -> Agent.ask(prompt=...)
    )

server = MCPServer(agent, context_provider=provide)
```

`AskContext` fields: `variables: dict | None`, `tools: list | None`,
`prompt: list[str] | str | None`. Any field left `None` is omitted, so the
default (stateless) behavior is preserved.

## Security — OAuth2 bearer (HTTP only) — needs external setup

For HTTP, protect the endpoint with OAuth 2.1 bearer auth. The MCP server acts
purely as a **Resource Server**: it advertises trusted authorization server(s)
via RFC 9728 Protected Resource Metadata at
`/.well-known/oauth-protected-resource` and verifies presented tokens. Issuing
tokens stays with your external authorization server.

```python
from ag2.mcp import MCPServer
from ag2.mcp.security import oauth2_scheme, require

security = require(
    oauth2_scheme(url="https://auth.example.com"),  # absolute http(s) issuer URL
    resource_url="https://api.example.com/mcp",     # this server's public endpoint
    verifier=my_token_verifier,                     # your mcp TokenVerifier implementation
    required_scopes=["mcp.read"],                   # a token must carry every scope
)

app = MCPServer(agent, path="/mcp", security=security)
```

- `security.resource_url`'s path component **must equal** `path` (here `/mcp`),
  or `MCPServer` raises `ValueError`.
- Missing/invalid token → `401` (with a `WWW-Authenticate` header pointing at
  the metadata); insufficient scopes → `403`.
- `verifier` is a bring-your-own `mcp.server.auth.provider.TokenVerifier`.
  `oauth2_scheme(url=...)` rejects non-`http(s)` URLs (an OIDC issuer *string*
  is not a usable authorization-server URL — pass the full URL).

> **Requires external setup**: a real authorization server to mint tokens and a
> concrete `TokenVerifier`. Exercise the unauthenticated path in-process (see
> testing below); the token round-trip needs your OAuth provider.

## Testing in-process — no sockets, no subprocess

`ag2.mcp.testing` stands the server up entirely in memory. Use
`connect()` for a low-level `ClientSession` (list/call tools, prompts,
resources) and `serve()` for an `httpx.AsyncClient` over the ASGI transport
(exercise the HTTP path, status codes, metadata). Pair with `TestConfig` from
`ag2.testing` to mock the LLM — no API keys needed.

```python title="test_server.py"
import asyncio

from ag2 import Agent
from ag2.testing import TestConfig
from ag2.mcp import MCPServer, Resource
from ag2.mcp import testing

async def main() -> None:
    agent = Agent(name="assistant", prompt="p", config=TestConfig("Hello from the agent!"))
    server = MCPServer(
        agent,
        resources=[Resource(uri="config://app", name="cfg", read=lambda: '{"env": "prod"}')],
    )

    # In-memory MCP client/server pair (the MCP analog of an ASGI test client).
    async with testing.connect(server) as session:
        await session.initialize()

        tools = await session.list_tools()
        assert [t.name for t in tools.tools] == ["ask"]

        result = await session.call_tool("ask", {"message": "Hi"})
        assert "Hello from the agent" in result.content[0].text

        res = await session.read_resource("config://app")
        assert res.contents[0].text == '{"env": "prod"}'

    # Exercise the HTTP transport (initialize handshake, session id) in-memory:
    async with testing.serve(server) as client:
        resp = await client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
            json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "test", "version": "1.0"}},
            },
        )
        assert resp.status_code == 200
        assert "mcp-session-id" in resp.headers

    print("ok")

if __name__ == "__main__":
    asyncio.run(main())
```

`testing.connect(server, raise_exceptions=..., **session_kwargs)` forwards extra
kwargs (e.g. `logging_callback` / `message_handler`) to the client session — how
you observe streamed progress / log notifications.

> **`TestConfig` caveat for multi-turn:** `TestConfig.create()` builds a fresh
> response iterator per turn, so giving it `TestConfig("a", "b")` will **not**
> show `"a"` then `"b"` across two separate MCP `call_tool`s — each call replays
> from the first scripted response. That's a property of the mock, not the
> server: session history really does accumulate (verify it by inspecting the
> growing message list a custom test client receives, or use a real model).

## Public API reference

All importable from `ag2.mcp`:

| Symbol | Kind | Purpose |
|---|---|---|
| `MCPServer` | class | Wrap an `Agent` as an MCP server (ASGI app + `run_stdio()`). |
| `SessionConfig` | dataclass | `max_sessions`, `ttl`, `storage` for multi-turn history. |
| `Prompt` | dataclass | `name`, `render`, `description`, `arguments` — a prompt template. |
| `PromptArgument` | dataclass | `name`, `description`, `required` — a declared prompt arg. |
| `PromptMessage` | dataclass | `role` (`"user"`/`"assistant"`), `text` — one rendered message. |
| `Resource` | dataclass | `uri`, `name`, `read`, `description`, `mime_type` — static resource. |
| `ResourceTemplate` | dataclass | `uri_template`, `name`, `read`, ... — RFC 6570 dynamic resource. |
| `AskContext` | dataclass | `variables`, `tools`, `prompt` — per-request injection into `ask()`. |
| `ContextProvider` | type alias | `async (AccessToken | None) -> AskContext`. |
| `build_ask_tool` | function | Build the single conversational `MCPTool` standalone (advanced/tests). |

From `ag2.mcp.security`: `oauth2_scheme`, `require`, `Scheme`,
`Requirement`. From `ag2.mcp.testing`: `connect`, `serve`.

## Common pitfalls

- **Missing `mcp` extra** — `pip install "ag2[mcp]"`; otherwise the imports are dependency stubs that raise on use.
- **Agent has no model config** — `MCPServer` accepts it, but the first tool call raises `MCPAgentConfigError`. Set `Agent(config=...)`.
- **Confusing server with client** — `MCPServer` SERVES your agent. To CONSUME an external MCP server's tools from your agent, use `MCPServerTool` (see `ag2-use-builtin-tools`).
- **`instructions=` ≠ system prompt** — `instructions` is client-facing "how to use this server" text in the handshake; it is not derived from the agent's prompt. Pass it explicitly.
- **`stateless=True` HTTP discards sessions** — a stateless HTTP transport issues no `mcp-session-id`, so multi-turn history can't key. Use `stateless=False` (default) when you want sessions.
- **`security.resource_url` path mismatch** — its path must equal `path`; otherwise `MCPServer.__init__` raises `ValueError`.
- **Non-object `response_schema`** — only object schemas get `outputSchema`/`structuredContent`; scalars/unions come back as text.

## Going deeper

- Source: `ag2/mcp/{server,sessions,prompts,resources,executor,info,security,testing}.py`
- Runnable reference covering every sample above: `references/test_server.py` (run with `python references/test_server.py`, no API keys needed)
- MCP spec: https://modelcontextprotocol.io
- Client side (consuming MCP servers from an agent): skill `ag2-use-builtin-tools`
