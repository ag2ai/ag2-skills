---
name: ag2-use-builtin-tools
description: Wire AG2's shipped tools into an `Agent` instead of writing custom Python. Provider-native server-side tools in `ag2.tools` (`WebSearchTool`, `WebFetchTool` + `UrlSources` (from `ag2.tools.builtin.web_fetch`), `CodeExecutionTool`, `FileSearchTool`, `ImageGenerationTool`, `MemoryTool`, `SkillsTool`, `GoogleMapsTool`, `XSearchTool`, `MCPServerTool`); consuming MCP servers client-side (`MCPToolkit`, `MCPServerConfig`, `MCPStdioServerConfig`, `MCPAnswerPolicy`); and local toolkits for every provider (`FilesystemToolkit`, `DuckDuckSearchTool`, `TavilySearchTool`, `PerplexitySearchToolkit`, `SkillPlugin`/`SkillsToolkit`, and `ag2.extensions.tools.search` — `ExaToolkit`, `FirecrawlToolkit`, `KeenableSearchToolkit`, `SerplySearchToolkit`, `TinyFishSearchToolkit`). Use for web search/fetch, connecting an agent to an MCP server, file access, or skills. Shell (`SandboxShellTool`, `ShellTool`, `AnthropicBashTool`) → `ag2-shell-tool`; custom tools → `ag2-add-custom-tool`; serving an agent over MCP → `ag2-mcp`.
license: Apache-2.0
metadata:
  ag2_version: "1.3.0"
---

# Use AG2's built-in tools

## When to use

The user wants a capability AG2 already ships. Three families, all passed via `Agent(tools=[...])` and freely mixed with custom tools:

1. **Provider-native** — run server-side by Anthropic / OpenAI / Gemini / xAI / Z.AI. Support varies — check the matrix.
2. **MCP client** — `MCPToolkit` (AG2 connects; any provider; stdio or HTTP) or `MCPServerTool` (the provider connects).
3. **Local toolkits** — Python tools run in your process; work with **every** provider.

## Recipes

```python
from ag2 import Agent
from ag2.config import AnthropicConfig, OpenAIResponsesConfig
from ag2.tools import CodeExecutionTool, UserLocation, WebFetchTool, WebSearchTool

agent = Agent(
    "researcher",
    config=AnthropicConfig(model="claude-haiku-4-5", streaming=True),  # streaming: see pitfalls
    tools=[
        WebSearchTool(max_uses=3, user_location=UserLocation(country="US"), allowed_domains=["pypi.org"]),
        WebFetchTool(max_uses=2, citations=True),   # Anthropic / Gemini only
        CodeExecutionTool(),                         # Anthropic / OpenAI / Gemini / xAI
    ],
)
reply = await agent.ask("Find the ag2 PyPI page and report the latest version.")
```

`ImageGenerationTool(quality="low", size="1024x1024")` (OpenAI Responses only) returns images on `reply.files` (`BinaryResult.data`).

### Connect an MCP server (client side) — needs `pip install "ag2[mcp]"`

```python
from ag2.tools import MCPServerConfig, MCPStdioServerConfig, MCPToolkit

tools = [
    MCPToolkit("https://mcp.example.com/mcp"),                       # remote, no auth
    MCPToolkit(MCPServerConfig(server_url="https://mcp.example.com/mcp",
                               authorization_token=token, tool_name_prefix="docs_")),
    MCPToolkit(MCPStdioServerConfig(command="uvx", args=["mcp-server-github"],
                                    env={"GITHUB_TOKEN": gh}, allowed_tools=["list_issues"])),
]
```

AG2 discovers the server's tools lazily and executes them itself — works with every provider, credentials never leave your infra. `MCPServerTool(server_url=..., server_label=..., allowed_tools=[...])` instead forwards URL + token to the provider (Anthropic / OpenAI Responses / xAI; URL servers only). Elicitation, `protocol_mode="auto"`, prefixes and the full config tables: `references/mcp_client.md`.

### Local toolkits (any provider)

```python
import os, tempfile
from ag2.tools import DuckDuckSearchTool, FilesystemToolkit, SkillPlugin
from ag2.extensions.tools.search import KeenableSearchToolkit, SerplySearchToolkit

workspace = tempfile.mkdtemp()
agent = Agent(
    "worker",
    config=OpenAIResponsesConfig(model="gpt-4.1-mini"),
    tools=[
        FilesystemToolkit(base_path=workspace),         # read/write/update/delete/find, sandboxed to base_path
        DuckDuckSearchTool(max_results=5),              # pip install "ag2[ddgs]", no key
        KeenableSearchToolkit(max_results=5),           # no extra package, works without a key
        SerplySearchToolkit(api_key=os.environ["SERPLY_API_KEY"]).web(num=5),  # factory = one tool only
    ],
    plugins=[SkillPlugin("./my-skills")],               # local agentskills.io skills
)
```

Every multi-tool toolkit exposes per-tool factories (`fs.read_file()`, `exa.search()`, `serply.news()`, …) so you can hand the agent a subset with per-call options.

## Going deeper

- `references/builtin_tools_matrix.md` — provider × tool matrix, every parameter, Anthropic tool versions, `WebFetchTool(url_sources=...)`, `FileSearchTool`, `SkillsTool`, all search toolkits and their install lines, `SkillPlugin` vs `SkillsToolkit`.
- `references/mcp_client.md` — `MCPToolkit` / `MCPServerTool` in depth.
- Upstream: `website/docs/user-guide/tools/{builtin_tools,common_toolkits,mcp_servers,toolkits}.mdx`, `website/docs/user-guide/skills.mdx`, `website/docs/user-guide/extensions/tools/search/*.mdx`.

## Common pitfalls

- **Tool/provider mismatch raises `UnsupportedToolError`** (from `ag2.exceptions`) on the first `ask` — e.g. `WebFetchTool` on OpenAI, `MemoryTool` off Anthropic, `ImageGenerationTool` off OpenAI Responses. Use `OpenAIResponsesConfig`, not `OpenAICompletionsConfig`, for OpenAI built-in tools.
- **Anthropic + server tools truncates `reply.body`** (1.3.0): a cited answer arrives as many text blocks and non-streaming `reply.body` / `content()` keeps only the last one (often just `"."`). Use `AnthropicConfig(..., streaming=True)` — the streamed reply is complete.
- **`MCPServerTool(blocked_tools=...)` on OpenAI / xAI raises `BlockedToolsUnsupportedError`** — they only take an allow-list. Use `allowed_tools`, or `MCPToolkit`, which enforces `blocked_tools` everywhere.
- **Version-gated `WebFetchTool` options** (`use_cache` ≥ `web_fetch_20260309`, `response_inclusion` = `web_fetch_20260318`) raise `WebFetchOptionUnsupportedError` before the request — pass a matching `version=`.
- **`FilesystemToolkit` is sandboxed** — paths or glob patterns escaping `base_path` raise `PermissionError`; `read_only=True` exposes only `read_file`/`find_files`.
- **MCP tool-name collisions** — a server tool never overrides a local tool of the same name; it is dropped with a warning. Set `tool_name_prefix`.
- **Missing dependency** — an extension whose SDK is absent imports as a placeholder that raises `ImportError` with the exact `pip install` on first use. Install before delivering code: `ag2[mcp]`, `ag2[ddgs]`, `ag2[tavily]`, `ag2[perplexity]`, `"exa-py>=2.12.1,<3"`, `"firecrawl-py>=4.46,<5"`, `"tinyfish>=0.2.3"`. Keenable / Serply / Glasser / Xquik need no extra package.
- **`SkillsTool` ≠ `SkillPlugin`** — `SkillsTool("xlsx")` enables the provider's hosted skills; `SkillPlugin` / `SkillsToolkit` run your own local skill directories.
