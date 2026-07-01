---
name: ag2-use-builtin-tools
description: Wire AG2's shipped tools into an `Agent` — both provider-native server-side tools (web search, web fetch, code execution, MCP, image generation, memory) and locally-executed common toolkits (filesystem, DuckDuckGo, Exa, Tavily, skills). Use when the user wants capabilities AG2 already ships rather than writing custom Python. For shell commands see `ag2-shell-tool`; for custom Python tools see `ag2-add-custom-tool`.
license: Apache-2.0
---

# Use AG2's built-in tools

## When to use

Reach for this skill when the user wants to add a capability that AG2 already ships. Two families:

1. **Provider-native tools** (`ag2.tools` — `WebSearchTool`, `CodeExecutionTool`, etc.) — executed server-side by Anthropic / OpenAI / Gemini. No Python implementation on your side.
2. **Common toolkits** (`ag2.tools` — `FilesystemToolkit`, `DuckDuckSearchTool`, `TavilySearchTool`, `SkillsToolkit`; plus `ExaToolkit` from `ag2.extensions.tools.search`) — regular Python that runs in your process and works with **every** provider.

For shell commands, use `ag2-shell-tool` (it's important enough to live in its own skill).
For custom Python tools, use `ag2-add-custom-tool`.

## 60-second recipes

### Web search (provider-native)

```python
from ag2 import Agent
from ag2.config import AnthropicConfig
from ag2.tools import WebSearchTool, UserLocation

agent = Agent(
    "researcher",
    config=AnthropicConfig(model="claude-sonnet-4-6"),
    tools=[
        WebSearchTool(
            max_uses=5,
            user_location=UserLocation(country="US"),
            allowed_domains=["github.com", "pypi.org"],
            blocked_domains=["pinterest.com"],
        ),
    ],
)
```

### Web fetch (Anthropic / Gemini only)

```python
from ag2.tools import WebFetchTool

tools = [WebFetchTool(max_uses=3, max_content_tokens=50000, citations=True)]
```

### Code execution

```python
from ag2.tools import CodeExecutionTool

agent = Agent("analyst", config=config, tools=[CodeExecutionTool()])
```

### MCP server integration

```python
from ag2.tools import MCPServerTool

tools = [
    MCPServerTool(
        server_url="https://mcp.example.com/sse",
        server_label="my-tools",
        allowed_tools=["search", "summarize"],
    ),
]
```

### Image generation (OpenAI Responses only)

```python
from ag2.config import OpenAIResponsesConfig
from ag2.tools import ImageGenerationTool

agent = Agent(
    "designer",
    config=OpenAIResponsesConfig(model="gpt-4.1"),
    tools=[ImageGenerationTool(quality="high", size="1024x1024", output_format="png")],
)
reply = await agent.ask("Generate a logo for a coffee shop.")
images = reply.files  # list[BinaryResult]
```

### Filesystem (sandboxed, any provider)

```python
from ag2.tools import FilesystemToolkit

fs = FilesystemToolkit(base_path="/tmp/workspace")
agent = Agent("worker", config=config, tools=[fs])
```

`base_path` is enforced — `../../etc/passwd` raises `PermissionError`. Use `read_only=True` to expose only `read_file` and `find_files`. For ephemeral workspaces use `tempfile.TemporaryDirectory()` rather than hardcoding `/tmp`.

### Web search via DuckDuckGo (no API key)

```python
from ag2.tools import DuckDuckSearchTool
# requires: pip install ag2[ddgs]

tools = [DuckDuckSearchTool(max_results=10, region="us-en", safesearch="moderate")]
```

### Exa neural search

```python
import os
from ag2.extensions.tools.search import ExaToolkit
# requires: pip install "exa-py>=2.12.1,<3"  (no ag2[exa] extra — install the package directly)

tools = [ExaToolkit(api_key=os.environ["EXA_API_KEY"])]
```

Each tool is exposed as a factory method (`exa.search()`, `exa.find_similar()`, `exa.get_contents()`, `exa.answer()`) so you can pass only what you need with per-call config.

### Tavily search

```python
import os
from ag2.tools import TavilySearchTool
# requires: pip install ag2[tavily]

tools = [TavilySearchTool(
    api_key=os.environ["TAVILY_API_KEY"],
    search_depth="advanced",
    include_answer=True,
)]
```

## Going deeper

- **Per-tool provider support, every parameter, version pinning** — `references/builtin_tools_matrix.md`.
- **Source docs** — `website/docs/user-guide/tools/builtin_tools.mdx` (provider-native), `website/docs/user-guide/tools/common_toolkits.mdx` (common toolkits — also covers `SkillsToolkit` and `SkillSearchToolkit`).
- **Toolkits authoring** — `website/docs/user-guide/tools/toolkits.mdx`.

## Common pitfalls

- **Mismatch between tool and provider** — `WebFetchTool` raises with OpenAI; `MemoryTool` is Anthropic-only; `ImageGenerationTool` is OpenAI Responses only. Check `references/builtin_tools_matrix.md` first.
- **Anthropic tool versions default to older revisions** — pin `version="web_search_20260209"` etc. when you need dynamic filtering on Opus 4.6 / Sonnet 4.6.
- **`FilesystemToolkit` paths are sandboxed** — by design. Don't try to bypass the path-traversal guard; choose a wider `base_path` instead.
- **Toolkits and individual tools mix freely** — `tools=[fs, exa, my_custom_tool]` is fine.
- **Optional dependency missing** — `DuckDuckSearchTool` (`ag2[ddgs]`) and `TavilySearchTool` (`ag2[tavily]`) need their `ag2[<extra>]` install; `ExaToolkit` is a extension with **no `ag2` extra** — install its package directly (`pip install "exa-py>=2.12.1,<3"`). Without the dependency you get a clear `ImportError` from the config-fallback layer, not a confusing crash. Install before delivering the code. If you cannot run commands, state the exact `pip install` command.
