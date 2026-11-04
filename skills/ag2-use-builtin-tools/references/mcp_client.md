# Consuming MCP servers from an AG2 agent (client side)

Install: `pip install "ag2[mcp]"` (MCP SDK 2.x). Serving *your* agent over MCP is a different skill — `ag2-mcp`.

## Two connection styles

| | `MCPToolkit` (client-side) | `MCPServerTool` (provider-side) |
|---|---|---|
| Who connects / executes | AG2, in your process | the LLM provider |
| Providers | every provider | Anthropic, OpenAI Responses, xAI |
| Local stdio servers | yes (`MCPStdioServerConfig`) | no — URL only |
| Credentials leave your infra | no | yes — sent to the provider on every request |
| Tool middleware / `blocked_tools` enforced | yes | `blocked_tools` raises `BlockedToolsUnsupportedError` on OpenAI/xAI |

Default to `MCPToolkit`.

## `MCPToolkit(server, *, middleware=(), answering=None)`

`server` is a URL string, an `MCPServerConfig`, or an `MCPStdioServerConfig`. Each MCP tool becomes an ordinary function tool. Discovery is lazy and cached while the resolved config is unchanged; every operation (discovery, each call) opens a short-lived session, so there is no process or pool to manage. A stdio subprocess is launched on first use.

```python
import sys
from ag2 import Agent
from ag2.config import OpenAIResponsesConfig
from ag2.tools import MCPServerConfig, MCPStdioServerConfig, MCPToolkit

agent = Agent(
    "assistant",
    config=OpenAIResponsesConfig(model="gpt-4.1-mini"),
    tools=[
        MCPToolkit(MCPServerConfig(
            server_url="https://mcp.example.com/mcp",   # include the endpoint path
            authorization_token="XXXX",                 # sent as Bearer unless headers has Authorization
            tool_name_prefix="docs_",                   # model sees docs_search; server still gets "search"
        )),
        MCPToolkit(MCPStdioServerConfig(
            command=sys.executable, args=["my_server.py"],
            allowed_tools=["add"],                      # filters match ORIGINAL remote names
            server_label="calc",
        )),
    ],
)
```

### `MCPServerConfig` (keyword-only)

`server_url` (required), `authorization_token=None`, `headers=None`, `connection_timeout=30.0`, `max_sse_event_size=1_048_576` (`None` = no cap; raise for big tool results), `proxy=None`, `verify=True`, `protocol_mode="legacy"`, `server_label=""`, `description=None`, `allowed_tools=None`, `blocked_tools=None` (applied after `allowed_tools`), `tool_name_prefix=""`.

### `MCPStdioServerConfig` (keyword-only)

`command` (required), `args=[]`, `env=None` (inherits when unset), `cwd=None`, `encoding="utf-8"`, `protocol_mode="legacy"`, plus `server_label` / `description` / `allowed_tools` / `blocked_tools` / `tool_name_prefix`.

Every field except `connection_timeout`, `max_sse_event_size`, `proxy`, `verify`, `encoding`, `protocol_mode` accepts `Variable("name")` — per-conversation tokens or tenant prefixes resolved from `context.variables` at discovery time:

```python
from ag2 import Variable
MCPToolkit(MCPServerConfig(server_url="https://mcp.example.com/mcp",
                           authorization_token=Variable("mcp_token")))
reply = await agent.ask("...", variables={"mcp_token": user_token})
```

### Name collisions

A server tool never replaces a tool declared in code — it is dropped with a warning. Two servers exposing the same name without distinct prefixes: the first wins. Fix with `tool_name_prefix`.

## When the server asks you something — `MCPAnswerPolicy`

A server may elicit input mid-call. Everything is **off by default** (the capability is not even advertised), so a tool that elicits fails with "Elicitation not supported" until you opt in:

```python
from ag2.tools import MCPAnswerPolicy, MCPStdioServerConfig, MCPToolkit

agent = Agent(
    "assistant",
    config=config,
    hitl_hook=lambda event: input(event.content),     # where the server's question goes
    tools=[MCPToolkit(
        MCPStdioServerConfig(command="python", args=["server.py"]),
        answering=MCPAnswerPolicy(elicitation="ask", max_rounds=10),
    )],
)
```

| field | default | meaning |
|---|---|---|
| `elicitation` | `"decline"` | `"ask"` routes the question to `context.input()` → the agent's `hitl_hook` |
| `sampling` | `False` | lend your model to the server (deprecated in MCP; prefer not) |
| `roots` | `()` | report directories to the server (deprecated in MCP) |
| `max_rounds` | `10` | how often a server may come back before the call fails |

Only single-property form elicitations are answerable (the human's text is sent verbatim for that property); URL-mode and multi-field forms are declined without asking. With `elicitation="ask"` but no `hitl_hook`, the call fails with "human input was requested but not provided".

### `protocol_mode`

`"legacy"` (default) does the `initialize` handshake only. `"auto"` probes `server/discover` first and falls back — required for servers on the 2026-07-28 MCP revision, the only era that can return a question as a call *result*.

## `MCPServerTool` (provider-side)

```python
from ag2.config import AnthropicConfig
from ag2.tools import MCPServerTool

Agent("assistant", config=AnthropicConfig(model="claude-haiku-4-5"),
      tools=[MCPServerTool(server_url="https://mcp.deepwiki.com/mcp", server_label="deepwiki",
                           allowed_tools=["read_wiki_structure"])])
```

Params: `server_url`, `server_label`, `authorization_token`, `description`, `allowed_tools`, `blocked_tools` (Anthropic only), `headers` (OpenAI/xAI; Anthropic ignores). All accept `Variable`. Gemini / Z.AI raise `UnsupportedToolError`.

Upstream: `website/docs/user-guide/tools/mcp_servers.mdx`.
