# ag2.mcp serving reference (AG2 1.3.0, mcp SDK 2.x)

Everything here is verified by `test_server.py` / `serve_and_consume.py` in this folder unless marked otherwise.

## `MCPServer(agent, **options)`

| option | default | purpose |
|---|---|---|
| `name` / `version` / `title` / `description` / `instructions` / `website_url` / `icons` | derived / `None` | `initialize` handshake metadata; never derived from the agent |
| `cache_hints` | `None` | `ttlMs`/`cacheScope` hints (only 2026-07-28 clients see them) |
| `tool_name` / `tool_description` | `"ask"` / auto | the conversational tool |
| `stream_progress` | `True` | forward agent stream events as progress/log notifications |
| `context_provider` | `None` | async `(AccessToken \| None) -> AskContext` per call |
| `lifespan` | `None` | `mcp` server lifespan; state reachable via `request_context.lifespan_context` |
| `sessions` | `True` | `True`, `SessionConfig(...)`, or `False` (stateless) |
| `elicitation_policy` | `"ask"` | `"ask"` or `"decline"` for the served agent's own `ctx.input()` |
| `client_model` | `False` | legacy: reason on the client's LLM via MCP sampling (deprecated by MCP) |
| `resources` / `resource_templates` / `prompts` | `()` | capability advertised only when non-empty |
| `tools` | `()` | `@mcp_tool` / `MCPFunctionTool` served next to `ask` |
| `apps` | `()` | `MCPApp`s (document + bound tools) |
| `extensions` | `None` | advertised protocol extensions (`ExtensionMap`); inspect per request with `client_extension()` |
| `path` / `stateless` / `json_response` | `"/mcp"` / `False` / `False` | HTTP only |
| `transport` | `None` (= `TransportConfig()`) | streamable-HTTP settings |
| `security` | `None` | OAuth 2.1 Resource Server requirement |
| `request_state_security` | `None` | replace the ephemeral `RequestStateSecurity` (paused-run state) |

Escape hatch: `server.server` is the low-level `mcp` `Server`.

## `SessionConfig` and `TransportConfig`

`SessionConfig(max_sessions=1024, ttl=None, storage=None)` — LRU cap on remembered **conversations** (a conversation with a turn in flight is never evicted), idle expiry seconds, `ag2` `Storage` for history (e.g. `ag2.streams.RedisStorage("redis://...")`). The name→history registry is per process even with shared storage: pin callers to one replica.

`TransportConfig` (all keyword):

| field | default | purpose |
|---|---|---|
| `mcp_session_idle_timeout` | `1800.0` | idle MCP session reaped (next request 404); `None` never reaps |
| `max_mcp_sessions` | `10_000` | concurrent MCP sessions; beyond → 503 |
| `max_request_body_size` | 4 MiB | larger body → 413 |
| `security_settings` | `None` | `TransportSecuritySettings(allowed_hosts=[...], allowed_origins=[...])`. `None` = off unless `security=` is set (then derived from `resource_url`). A bare `TransportSecuritySettings()` refuses everything |
| `sse_retry_interval` | `None` | ms reconnect hint (needs `event_store`) |
| `event_store` | `None` | `EventStore` for resumable SSE (`Last-Event-ID`) |

`stateless=True` with a non-default `mcp_session_idle_timeout` raises `ValueError`. `stateless=True` + `sessions=True` is valid: conversations are named by handle only.

## Deterministic tools: `@mcp_tool`

```python
from typing import Annotated
from pydantic import BaseModel
from ag2.mcp import Elicit, ListRoots, MCPRequestContext, Resolve, mcp_tool

class Colour(BaseModel):
    answer: str

def pick_colour() -> Elicit[Colour]:
    return Elicit("What colour?", Colour)

@mcp_tool                                     # or @mcp_tool(name=, description=, title=, annotations=, output_schema=, meta=)
def paint(room: str, colour: Annotated[Colour, Resolve(pick_colour)]) -> str:
    """Paint a room."""
    return f"painted {room} {colour.answer}"

@mcp_tool
async def whoami(ctx: MCPRequestContext) -> str:   # live mcp ServerRequestContext; hidden from inputSchema
    params = ctx.session.client_params
    return params.client_info.name if params else "unknown"
```

- Return types: `str`, `ContentBlock`(s), mapping (→ `structuredContent`, no schema), pydantic model / dataclass (→ `outputSchema` + `structuredContent`, text from `__str__`), or `mcp.types.CallToolResult`. Anything else (e.g. `int`) → `TypeError` at call time.
- A resolver may return `Elicit(message, Model)`, `Sample(messages, max_tokens=...)` or `ListRoots()`; the body runs once all are satisfied; the resolver re-runs each round (make it idempotent). Resolvers ask regardless of `elicitation_policy`.
- `Variable`, `Depends`/`Inject` and `AskContext.dependencies` resolve inside `@mcp_tool`, resource readers and app content providers.

## Prompts and resources

```python
from pathlib import Path
from ag2.mcp import Prompt, PromptArgument, PromptMessage, Resource, ResourceTemplate

Prompt(name="code_review", description="...", render=lambda args: [PromptMessage(role="user", text=f"Review {args['language']}")],
       arguments=(PromptArgument(name="language", required=True), PromptArgument(name="code")))
Prompt(name="greet", render=lambda args: f"Say hello to {args['who']}")      # str -> one user message
Resource(uri="config://app", name="app-config", mime_type="application/json", read=lambda: '{"env": "prod"}')
ResourceTemplate(uri_template="file:///{+path}", name="file", read=lambda v: Path(v["path"]).read_text())
```

Renderers/readers may be sync or async; `read` returns `str` or `bytes`. Missing required prompt argument / unknown prompt / unknown resource URI → JSON-RPC `-32602`. Template variables are percent-decoded once; `{var}` is one segment, `{+var}` may span `/` (and may contain `..` — contain it yourself).

## MCP Apps (`MCPApp`)

```python
from dataclasses import dataclass
from ag2.mcp import MCPApp, MCPServer

CARD = """<div id="name">Loading…</div>
<script>ag2ui.onToolResult(r => { document.getElementById("name").textContent = r.structuredContent.name; });</script>"""
shop = MCPApp("ui://shop/card", CARD)

@dataclass
class Item:
    name: str
    price: int
    def __str__(self) -> str: return f"{self.name} costs ${self.price}."

@shop.tool
async def show_item(item_id: str) -> Item:
    """Show a product card."""
    return Item(name="Espresso cup", price=12)

server = MCPServer(agent, apps=[shop])
```

The document is static (hosts read it in parallel with the call); per-call data arrives as `structuredContent`. The `ag2ui` JS global is injected (`onToolResult`, `callTool`, `sendMessage`, ...). `_meta.ui` is only advertised to clients that advertised the MCP Apps extension — check with `client_supports_apps(ctx)`. Also: `AppSandbox`, `ResourceCsp`, `ResourcePermissions`, `Visibility`, `AppContent`, `AppText`. MCP-UI (`ag2.mcp_ui`, `ag2[mcp-ui]`) is the alternative, opposite pattern. Details: `website/docs/user-guide/tools/mcp_apps.mdx`.

## Per-request context: `AskContext` / `ContextProvider`

```python
from ag2.mcp import AskContext, MCPServer

async def provide(token) -> AskContext:     # token: mcp AccessToken or None
    return AskContext(variables={"tenant": "acme"}, dependencies=None, tools=None, prompt="Be concise.")

server = MCPServer(agent, context_provider=provide)
```

Non-`None` fields go into `Agent.ask(...)`.

## OAuth2 security (HTTP) — needs your own authorization server

```python
from ag2.mcp.security import oauth2_scheme, require

security = require(
    oauth2_scheme(url="https://auth.example.com"),
    resource_url="https://agent.example.com/mcp",   # path must equal MCPServer(path=...)
    verifier=my_token_verifier,                      # mcp TokenVerifier: async verify_token(token) -> AccessToken | None
    required_scopes=["mcp.read"],
    validate_token_resource=False,                   # True = RFC 8707 audience check on AccessToken.resource
)
app = MCPServer(agent, path="/mcp", security=security)
```

No/invalid token → 401 with `WWW-Authenticate`; missing scope → 403; metadata at `/.well-known/oauth-protected-resource/mcp`. With auth, a conversation is bound to the token's subject/client id; another principal presenting the handle gets "unknown conversation".

## Pausing (modern era) and ops

On 2026-07-28 clients a question comes back as the call **result** (`InputRequiredResult`) and the run is held in-process until the client retries. Consequences: sticky routing required; a pause does not survive restart; multiple replicas need `RequestStateSecurity(keys=[os.environ["MCP_STATE_KEY"]])` (≥32-byte keys); a conversation with a paused run refuses other calls with a tool error. Pause lifetime = request-state TTL (600 s default; `RequestStateSecurity.ephemeral(ttl=120.0)`).

## Errors (`ag2.mcp.errors`)

`MCPServerError` ⊃ `MCPAgentConfigError`, `MCPToolNameConflictError`, `UnknownConversationError` (returned as tool error), `MCPResourceNotFoundError` / `MCPPromptNotFoundError` (-32602), `MCPSamplingUnavailableError` (-32021) / `MCPSamplingRefusedError`. `MCPElicitationDeclinedError` subclasses `HumanInputNotProvidedError`.

## Client side quick facts (`ag2.tools`)

`MCPToolkit(server, *, middleware=(), answering=None)`; `server` = URL | `MCPServerConfig(server_url, authorization_token, headers, connection_timeout=30.0, max_sse_event_size=1 MiB, proxy, verify, protocol_mode="legacy"|"auto", server_label, description, allowed_tools, blocked_tools, tool_name_prefix)` | `MCPStdioServerConfig(command, args, env, cwd, encoding, protocol_mode, ...)` (pass `env=dict(os.environ)` to forward API keys — unset `env` gives the subprocess only the mcp SDK minimal environment). Most fields accept a `Variable`. `MCPAnswerPolicy(elicitation="decline", sampling=False, roots=(), max_rounds=10)`; elicitation answers go through the agent's `hitl_hook` and only single-field forms are answerable. A remote tool never replaces a same-named local tool (dropped with a warning) — use `tool_name_prefix`.
