---
name: ag2-mcp
description: "Serve an AG2 `Agent` as an MCP server (mcp SDK 2.x) for Claude Desktop, Cursor, the MCP Inspector or any MCP client, and consume an external MCP server from an agent. Server side: `MCPServer(agent)` exposes `Agent.ask()` as one `ask` tool with conversation handles, over stdio (`run_stdio()`) or streamable HTTP (it is an ASGI app for uvicorn); deterministic `@mcp_tool` tools with `MCPRequestContext` and `Resolve`/`Elicit`/`Sample`/`ListRoots` resolved params; `Prompt`/`PromptArgument`/`PromptMessage`, `Resource`/`ResourceTemplate`, `MCPApp` (MCP Apps UI); `SessionConfig`, `TransportConfig`/`TransportSecuritySettings`, `elicitation_policy`, `AskContext`/`ContextProvider`, OAuth2 `security=`, `RequestStateSecurity`, in-process `ag2.mcp.testing.connect`/`serve`. Client side: `MCPToolkit` with `MCPServerConfig`/`MCPStdioServerConfig`/`MCPAnswerPolicy`. For provider-side `MCPServerTool` and other shipped tools see `ag2-use-builtin-tools`; for A2A/ACP/AG-UI see `ag2-a2a`, `ag2-acp`, `ag2-ag-ui`."
license: Apache-2.0
metadata:
  ag2_version: "1.3.0"
---

# AG2 + MCP: serve an agent, consume a server

Two opposite directions — pick the right one:

| You want… | Use |
|---|---|
| other MCP clients to call **your** agent (server) | `ag2.mcp.MCPServer` |
| your agent to call an **external** MCP server's tools, AG2 executes them | `ag2.tools.MCPToolkit` (below) |
| the LLM provider to connect to a remote MCP URL itself | `MCPServerTool` — see `ag2-use-builtin-tools` |

```bash
pip install "ag2[mcp]"   # mcp>=2.3,<3; without it ag2.mcp names are stubs that raise on use
```

## When to use

- Expose an AG2 agent to MCP clients (Claude Desktop, Cursor, IDEs, the MCP Inspector) as an `ask` tool, plus deterministic tools, prompts and resources.
- Multi-turn history per client session, OAuth2-protected HTTP, or per-request context injection.
- Let your agent call an external MCP server's tools that AG2 executes (`MCPToolkit`).

## Serve over stdio (Claude Desktop, Cursor, Inspector)

```python title="serve_stdio.py"
import asyncio

from ag2 import Agent
from ag2.config import OpenAIResponsesConfig
from ag2.mcp import MCPServer, mcp_tool

@mcp_tool
def stock_level(sku: str) -> str:          # must return str / dict / BaseModel / dataclass / CallToolResult
    """Return how many units of a SKU are in stock."""
    return f"{sku}: 42 units"

agent = Agent(
    name="assistant",
    prompt="You are a helpful assistant.",
    config=OpenAIResponsesConfig(model="gpt-4.1-mini"),   # served agent needs its own model
)
server = MCPServer(
    agent,
    name="assistant-mcp",            # serverInfo.name
    instructions="Ask me anything.", # client-facing hint, NOT the agent prompt
    tools=[stock_level],             # optional deterministic tools next to `ask`
)

if __name__ == "__main__":
    asyncio.run(server.run_stdio())
```

Client config (`claude_desktop_config.json`; Cursor uses the same shape):
`{"mcpServers": {"assistant": {"command": "python", "args": ["/abs/path/serve_stdio.py"], "env": {"OPENAI_API_KEY": "sk-..."}}}}`

## Serve over streamable HTTP

`MCPServer` **is** an ASGI3 app with its own lifespan — run it with uvicorn or mount it in Starlette/FastAPI:

```python
import uvicorn
app = MCPServer(agent, path="/mcp")          # endpoint at http://host:8000/mcp
uvicorn.run(app, host="127.0.0.1", port=8000)
```

HTTP-only knobs: `path="/mcp"`, `stateless=False`, `json_response=False`, `security=None`, `transport=TransportConfig(...)` (body-size cap, DNS-rebinding `TransportSecuritySettings`, MCP-session caps; see reference).

## The `ask` tool and conversations

The agent becomes one tool (`tool_name="ask"`) with `message` (required), `context` (optional, prepended) and `conversation` (optional handle). Every reply carries a **conversation handle** (a server-minted UUID) in `result.meta["ai.ag2/conversation"]` and in an extra text block — the agent's answer is `result.content[0].text`; don't join all blocks or the handle text leaks into your output. Pass it back to continue:

```python
from mcp.client import Client

async with Client("http://127.0.0.1:8000/mcp") as client:
    first = await client.call_tool("ask", {"message": "My name is Ada."})
    handle = first.meta["ai.ag2/conversation"]
    second = await client.call_tool("ask", {"message": "What is my name?", "conversation": handle})
    print(second.content[0].text)
```

- Unknown/expired handle → tool result with `is_error=True` (not a protocol error).
- Handshake-era clients without a handle still get per-MCP-session history; modern-era (2026-07-28) clients get a fresh conversation per call unless they pass a handle.
- `sessions=False` → stateless; the `conversation` argument disappears. `SessionConfig(max_sessions=1024, ttl=None, storage=None)` bounds the in-process registry.
- An object `response_schema` on the agent → `outputSchema` + `structuredContent`.

## Questions back to the caller (elicitation)

A tool inside the served agent calling `await ctx.input("...")` (`ctx: Context`) is forwarded to the calling client's human when `elicitation_policy="ask"` (default) and the client advertised elicitation; otherwise it falls to the agent's `hitl_hook`, else `HumanInputNotProvidedError`. A `@mcp_tool` can declare **resolved parameters** — `Annotated[T, Resolve(fn)]` where `fn` returns `Elicit(...)`, `Sample(...)` or `ListRoots()` — which are hidden from `inputSchema` and asked of the client before the body runs (runs regardless of `elicitation_policy`; write resolvers idempotently). `Sample` and `ListRoots` rely on MCP sampling/roots, deprecated by MCP revision 2026-07-28 — prefer `Elicit` or plain tool arguments. `client_model=True` (borrowing the client's LLM via MCP sampling) is legacy — configure the agent's own model.

## Consume an external MCP server (client side)

```python
import os
import sys

from ag2 import Agent
from ag2.config import OpenAIResponsesConfig
from ag2.tools import MCPAnswerPolicy, MCPServerConfig, MCPStdioServerConfig, MCPToolkit

agent = Agent(
    name="clerk",
    config=OpenAIResponsesConfig(model="gpt-4.1-mini"),
    tools=[
        MCPToolkit("https://mcp.example.com/mcp"),                       # bare URL
        MCPToolkit(MCPServerConfig(server_url="https://gh.example.com/mcp",
                                   authorization_token="XXX", tool_name_prefix="gh_")),
        MCPToolkit(MCPStdioServerConfig(command=sys.executable, args=["serve_stdio.py"], env=dict(os.environ)),
                   answering=MCPAnswerPolicy(elicitation="ask")),          # server questions -> hitl_hook
    ],
)
reply = await agent.ask("How many units of SKU-7 are in stock?")
print(reply.body)
```

`MCPToolkit` connects lazily, opens a short-lived session per operation, filters with `allowed_tools`/`blocked_tools`, and answers nothing unless `answering=` enables it. Use `protocol_mode="auto"` for servers on the modern (2026-07-28) era. **Pass `env=dict(os.environ)` to `MCPStdioServerConfig`** when the subprocess needs your API keys — the mcp SDK otherwise gives it only a minimal safe environment (verified: the served agent fails with missing `OPENAI_API_KEY`).

## Testing in-process (no sockets, no keys)

`ag2.mcp.testing.connect(server, **session_kwargs)` yields an **already-initialized** `ClientSession` (pass e.g. `elicitation_callback=`, `logging_callback=`); `testing.connect_modern` pins revision 2026-07-28; `testing.serve(server)` yields an `httpx.AsyncClient` over ASGI. Pair with `ag2.testing.TestConfig`. Note the mcp 2.x client types are snake_case: `tool.input_schema`, `result.is_error`, `result.structured_content`.

## Pitfalls

- `@mcp_tool` returning `int`/`float`/`list[int]` → `TypeError: A tool handler cannot return int` at call time. Return `str`, a mapping, a pydantic model/dataclass (gives `outputSchema`), or `CallToolResult`.
- Served agent with no `config=` → `MCPAgentConfigError` on first call.
- `security.resource_url` path must equal `path` → else `ValueError`. Without `security=`, DNS-rebinding protection is off and the handle is the only credential.
- `stateless=True` + `TransportConfig(mcp_session_idle_timeout=<non-default>)` → `ValueError`.
- Conversations and paused runs live in one process: behind a load balancer use sticky routing; share `RequestStateSecurity(keys=[...])` across replicas.
- `mcp.MCPDeprecationWarning` ("logging capability is deprecated") on the server comes from the mcp SDK when `stream_progress=True` forwards tool events as log messages — harmless; `stream_progress=False` silences it.
- `MCPApp` (MCP Apps) vs `ag2.mcp_ui` (MCP-UI, `ag2[mcp-ui]`) are alternatives — pick by host.

## Reference

- `references/serving_reference.md` — constructor tables, prompts/resources, `@mcp_tool` + `MCPRequestContext` + resolvers, `MCPApp`, OAuth2 security, `TransportConfig`, `AskContext`, pausing/ops, errors.
- `scripts/test_snippets.py` — in-process tests of every server feature above (no keys).
- `scripts/serve_and_consume.py` — uvicorn in-process + raw `mcp.client.Client` + `MCPToolkit` agent (needs `OPENAI_API_KEY`).
- Upstream: `website/docs/user-guide/tools/{serving_mcp,mcp_servers,mcp_apps,mcp_ui}.mdx`; source `ag2/mcp/`, `ag2/tools/mcp*`.
