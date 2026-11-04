# Built-in tools — provider matrix and per-tool parameters (AG2 1.3)

## Provider-native tools (server-side execution)

| Tool | Anthropic | OpenAI (Responses) | Gemini | xAI | Z.AI |
|---|:---:|:---:|:---:|:---:|:---:|
| `CodeExecutionTool` | ✓ | ✓ | ✓ | ✓ | ✗ |
| `WebSearchTool` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `WebFetchTool` | ✓ | ✗ | ✓ | ✗ | ✗ |
| `FileSearchTool` | ✗ | ✓ | ✓ | ✗ | ✗ |
| `ShellTool` | ✗ | ✓ | ✗ | ✗ | ✗ |
| `AnthropicBashTool` (client-executed — see `ag2-shell-tool`) | ✓ | ✗ | ✗ | ✗ | ✗ |
| `SkillsTool` | ✓ | ✓ | ✗ | ✗ | ✗ |
| `MCPServerTool` | ✓ | ✓ | ✗ | ✓ | ✗ |
| `ImageGenerationTool` | ✗ | ✓ | ✗ | ✗ | ✗ |
| `MemoryTool` | ✓ | ✗ | ✗ | ✗ | ✗ |
| `XSearchTool` | ✗ | ✗ | ✗ | ✓ | ✗ |
| `RetrievalTool` (`ag2.tools.builtin`) | ✗ | ✗ | ✗ | ✗ | ✓ |
| `GoogleMapsTool` | ✗ | ✗ | ✓ | ✗ | ✗ |

Unsupported combinations raise `ag2.exceptions.UnsupportedToolError` when the request is built (first `ask`). For OpenAI use `OpenAIResponsesConfig` — built-in tools are a Responses API feature. All constructor params accept `Variable(...)` for runtime resolution unless noted.

### `WebSearchTool`

| Parameter | Anthropic | OpenAI | Gemini | xAI | Z.AI |
|---|:---:|:---:|:---:|:---:|:---:|
| `max_uses` | ✓ | ✓ | ✗ | ✗ | ✗ |
| `user_location=UserLocation(country=..., city=..., region=..., timezone=...)` | ✓ | ✓ | ✗ | ✓ | ✗ |
| `search_context_size` (`"low"/"medium"/"high"`) | ✗ | ✓ | ✗ | ✗ | ✓ |
| `allowed_domains` | ✓ | ✓ | ✗ | ✓ | ✗ |
| `blocked_domains` | ✓ | ✗ | ✓ | ✓ | ✗ |

Unsupported parameters are silently ignored. `version=`: `web_search_20250305` (default), `web_search_20260209`, `web_search_20260318`.

### `WebFetchTool` (Anthropic honours options; Gemini maps to `url_context` and ignores them; OpenAI raises)

| Parameter | Notes |
|---|---|
| `max_uses`, `allowed_domains`, `blocked_domains`, `citations`, `max_content_tokens`, `strict` | any version |
| `use_cache` | needs `version="web_fetch_20260309"` or later |
| `response_inclusion` (`"full"` / `"excluded"`) | needs `version="web_fetch_20260318"` |
| `url_sources=UrlSources(...)` | which sources may supply a URL (policy control) |
| `version` | `web_fetch_20250910` (default), `web_fetch_20260209`, `web_fetch_20260309`, `web_fetch_20260318` |

A version-gated option on an older `version` raises `WebFetchOptionUnsupportedError` before any request is sent.

```python
from ag2.tools import tool
from ag2.tools.builtin.web_fetch import ExceptTools, OnlyTools, UrlSources, WebFetchTool

@tool
async def search_docs(query: str) -> list[str]:
    """Return URLs of documents matching the query."""
    return ["https://docs.example.com/guide"]

tools = [
    search_docs,
    WebFetchTool(url_sources=UrlSources(
        user_input="none",                              # never fetch a URL the user pasted
        client_tool_results=OnlyTools(["search_docs"]), # only URLs returned by this tool
    )),
]
```

`user_input`: `"all"` / `"none"`. `client_tool_results` / `server_tool_results`: `"all"`, `"none"`, `OnlyTools([...])`, `ExceptTools([...])`. Naming a tool not declared in the same request raises `WebFetchUrlSourceToolNotFoundError`. An unset source is not sent and the API default (permissive) applies — fetching URLs out of user text is a prompt-injection surface, so set `user_input="none"` for agents exposed to untrusted users.

### `CodeExecutionTool`

`CodeExecutionTool(version=...)` — `code_execution_20250825` (default), `code_execution_20260120`, `code_execution_20260521` (Anthropic). Other providers ignore the version.

### `FileSearchTool` (OpenAI Responses, Gemini)

| Parameter | Notes |
|---|---|
| `vector_store_ids` (positional) | **OpenAI, required** — vector store ids |
| `store_names` | **Gemini, required** — `FileSearchStore` resource names |
| `max_num_results` | shared (Gemini `top_k`) |
| `filters` | OpenAI comparison/compound filter, passed verbatim |
| `metadata_filter` | Gemini AIP-160 filter string |
| `include_results` | return raw chunks on `BuiltinToolResultEvent` (off by default) |

### `MCPServerTool` (provider connects to the server)

| Parameter | Anthropic | OpenAI | xAI |
|---|:---:|:---:|:---:|
| `server_url`, `server_label`, `authorization_token`, `description`, `allowed_tools` | ✓ | ✓ | ✓ |
| `blocked_tools` | ✓ | raises `BlockedToolsUnsupportedError` | raises |
| `headers` | ✗ (not forwarded) | ✓ | ✓ |

For client-side MCP (any provider, stdio servers, credentials stay local) use `MCPToolkit` — see `mcp_client.md`.

### `ImageGenerationTool` (OpenAI Responses only)

`quality` (`low/medium/high/auto`), `size` (`1024x1024`, `1536x1024`, `auto`, …), `background` (`transparent/opaque/auto`), `output_format` (`png/jpeg/webp`), `output_compression` (0–100, jpeg/webp), `partial_images` (1–3). Images surface on `reply.files: list[BinaryResult]` (`.data` bytes, `.metadata["media_type"]`).

### `SkillsTool` (provider-hosted skills)

```python
from ag2.tools import Skill, SkillsTool
SkillsTool("pptx", Skill("xlsx", version="latest"))   # Anthropic: pptx/xlsx/docx/pdf; auto-adds CodeExecutionTool
SkillsTool("openai-spreadsheets")                     # OpenAI Responses: auto-adds a hosted shell
```

Combining `SkillsTool` with `ShellTool(environment=ContainerReferenceEnvironment(...))` raises. Unrelated to local `SkillPlugin`.

### Others

- `MemoryTool()` — Anthropic `/memories` file store. `version="memory_20250818"`.
- `GoogleMapsTool(latitude=, longitude=, language_code=, enable_widget=)` — Gemini 3 family only.
- `XSearchTool(allowed_x_handles=, excluded_x_handles=, from_date=, to_date=, enable_image_understanding=, enable_video_understanding=)` — xAI only.
- `RetrievalTool(knowledge_id=..., prompt_template=...)` from `ag2.tools.builtin` — Z.AI only.

### Anthropic tool versions

Newer revisions (`web_search_20260209`, `web_fetch_20260209`+) support dynamic filtering but need newer Opus/Sonnet models. The defaults work with every Claude model including Haiku.

## Local toolkits (run in your process, all providers)

### `FilesystemToolkit(base_path=..., read_only=False)`

Tools: `read_file`, `write_file` (creates parent dirs), `update_file` (replace first occurrence; empty `old_content` only allowed on an empty file), `delete_file` (a symlink is unlinked, never its target; deleting `base_path` itself is refused), `find_files` (glob, `**` supported). Paths and glob patterns escaping `base_path` raise `PermissionError`. Factories `fs.read_file()`, `fs.find_files()`, … for subsets.

### Search toolkits

| Class | Import | Install | Key | Tools / factories |
|---|---|---|---|---|
| `DuckDuckSearchTool(max_results=5, region="us-en", safesearch="moderate")` | `ag2.tools` | `ag2[ddgs]` | none | single tool |
| `TavilySearchTool(api_key=, max_results=, search_depth=, topic=, include_answer=, time_range=, include_domains=, …)` | `ag2.tools` | `ag2[tavily]` | `TAVILY_API_KEY` | single tool |
| `PerplexitySearchToolkit(api_key=)` | `ag2.tools` | `ag2[perplexity]` | `PERPLEXITY_API_KEY` | `.search()`, `.answer(model="sonar", …)` |
| `ExaToolkit(api_key=, num_results=, max_characters=)` | `ag2.extensions.tools.search` | `"exa-py>=2.12.1,<3"` | `EXA_API_KEY` | `.search()`, `.find_similar()`, `.get_contents()`, `.answer()` |
| `FirecrawlToolkit(api_key=, limit=, scrape_results=, formats=, only_main_content=, max_characters=, api_url=)` | `ag2.extensions.tools.search` | `"firecrawl-py>=4.46,<5"` | `FIRECRAWL_API_KEY` | `.search()`, `.scrape()` |
| `KeenableSearchToolkit(api_key=None, max_results=, site=, published_after=, snippet_max_length=1000)` | `ag2.extensions.tools.search` | none (httpx) | optional `KEENABLE_API_KEY` (higher limits) | `.search()`, `.fetch()` |
| `SerplySearchToolkit(api_key=, num=, gl=, hl=, proxy_location=)` | `ag2.extensions.tools.search` | none (httpx) | `SERPLY_API_KEY` (pass explicitly) | `.web()`, `.news()`, `.scholar()` |
| `TinyFishSearchToolkit(api_key=, location=, language=, format=)` | `ag2.extensions.tools.search` | `"tinyfish>=0.2.3"` | `TINYFISH_API_KEY` | `.search()`, `.fetch()` |
| `GlasserToolkit(api_key=, limit=)` (paid per call) | `ag2.extensions.tools.search` | none (httpx) | pass `api_key` (not read from env) | `.web_search()`, `.company_profile()`, `.find_people()`, `.keyword_overview()`, `.social_search()`, `.stock_quote()` |
| `XquikSearchToolkit(api_key=)` | `ag2.extensions.tools.search` | none | required | `.search()` — X/Twitter posts |

Passing a toolkit registers all its tools; call a factory to register one tool with per-call options (factories also accept `name=`, `description=`, `middleware=`). Toolkits combine with `left | right` (right wins on name clash).

### Local skills — `SkillPlugin` / `SkillsToolkit` / `SkillSearchToolkit`

```python
from ag2.tools import MemorySkill, SkillPlugin, SkillsToolkit, SkillSearchToolkit
from ag2.tools.skills import LocalRuntime

Agent(..., plugins=[SkillPlugin()])                         # recommended: catalog injected into the prompt; default dir .agents/skills
Agent(..., plugins=[SkillPlugin(LocalRuntime("~/.agents/skills"), LocalRuntime(".agents/skills"))])  # last runtime wins on name clash
Agent(..., tools=[SkillsToolkit("./my-skills")])            # no prompt injection; adds an explicit list_skills tool
Agent(..., tools=[SkillSearchToolkit()])                    # + search_skills / install_skill / remove_skill (skills.sh); set GITHUB_TOKEN
```

Runtimes are positional (`*runtimes`: path str, `LocalRuntime(dir, extra_paths=[...], timeout=, blocked=[...])`, or `MemorySkill`). Activation tools: `load_skill`, `read_skill_resource` (only if some skill has resources), `run_skill_script` (only if some skill has `scripts/`). `SkillPlugin` snapshots the catalog at construction — rebuild it after installing skills. `MemorySkill(name=, description=, instructions=)` defines a skill in code with `@skill.resource` / `@skill.script` / `@skill.instructions` callables.
