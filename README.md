# AG2 Skills

A collection of skills for [AG2](https://github.com/ag2ai/ag2) — an async, protocol-driven Python agent framework (`autogen.beta`). Skills are packaged instructions and optional helper scripts that extend an AI agent's capabilities.

Skills follow the [Agent Skills](https://agentskills.io/) format.

## Available Skills

### ag2-overview

Map of AG2 beta capabilities and which sibling skill to reach for. **Load this first** when the user mentions building with AG2 beta but the specific feature isn't yet clear.

**Use when:**

- "I want to build an AG2 agent"
- "How do I use autogen.beta?"
- The task touches AG2 but spans multiple features

**Topics covered:**

- Index of every sibling skill with a one-line summary
- Three prerequisites for any AG2 build (provider extra, API key, env loading)

### ag2-quickstart

Build a minimal AG2 beta `Agent` end to end — pick a model provider, set a prompt, call `agent.ask()`, then continue the conversation with `reply.ask()` (multi-turn).

**Use when:**

- Starting a new AG2 beta project
- No working `Agent` yet
- Need the multi-turn chaining pattern

**Topics covered:**

- `OpenAIConfig`, `AnthropicConfig`, `GeminiConfig`, `OllamaConfig`
- Env-var fallback for API keys
- Multi-turn `reply.ask()` pattern

### ag2-add-custom-tool

Add a custom Python tool to an AG2 `Agent` using the `@tool` decorator.

**Use when:**

- Giving an agent a new capability backed by Python (API calls, DB queries, computations, file ops)
- Returning typed text / data / images / binary from a tool
- Wiring dependency injection into tools

**Topics covered:**

- Sync and async tools, parameter typing, Pydantic schema customisation
- Returning `Input` / `ToolResult` (text / data / images / binary)
- `final=True` early-exit
- Dependency injection via `Context` / `Inject` / `Variable` / `Depends`

### ag2-use-builtin-tools

Wire AG2's shipped tools into an `Agent` — both provider-native server-side tools and locally-executed common toolkits.

**Use when:**

- The user wants capabilities AG2 already ships rather than custom Python
- Adding web search, web fetch, code execution, MCP, image generation, or memory
- Mounting filesystem / DuckDuckGo / Exa / Tavily / Skills toolkits

**See also:** `ag2-shell-tool` for shell commands, `ag2-add-custom-tool` for custom Python tools.

### ag2-shell-tool

Give an AG2 `Agent` the ability to run shell commands.

**Use when:**

- Agent needs to execute commands, build/test code, manage files, operate on a workspace

**Topics covered:**

- `LocalShellTool` (client-side `subprocess`, works with any provider)
- Provider-native `ShellTool` (Anthropic / OpenAI execution)
- Sandboxing — `allowed`, `blocked`, `ignore`, `readonly`

### ag2-structured-output

Get a typed Python value back from an AG2 `Agent` instead of free text.

**Use when:**

- The user wants validated structured output, classification, extraction, or scoring
- Need to parse via `await reply.content()` instead of reading text

**Topics covered:**

- `response_schema=` (Pydantic, dataclass, primitive, union, `ResponseSchema`)
- `@response_schema` validator decorator
- `PromptedSchema` for providers without native structured output
- Per-turn override and validation retries

### ag2-multimodal-input

Send images, audio, video, or documents into an AG2 `Agent` alongside text.

**Use when:**

- Describing a photo, transcribing audio, summarising a PDF, analysing a video
- Passing `ImageInput`, `AudioInput`, `VideoInput`, `DocumentInput` to `agent.ask(...)`

**Topics covered:**

- Per-provider support matrix
- Four ways to source data (URL / path / bytes / `file_id`)
- Gemini-specific YouTube + media-resolution + clipping
- OpenAI image-detail, Anthropic prompt-caching on attachments
- `FilesAPI` upload lifecycle

### ag2-knowledge-and-memory

Persist agent state across runs, shape what the LLM sees per turn, and cap history to fit a context window.

**Use when:**

- Agent should remember between conversations
- Managing long histories
- Controlling prompt assembly

**Topics covered:**

- `KnowledgeStore` (memory / sqlite / disk / redis)
- `KnowledgeConfig` (`store=`, `compact=`, `aggregate=`, `bootstrap=`)
- Aggregation — `WorkingMemoryAggregate`, `ConversationSummaryAggregate`
- Assembly policies — `WorkingMemoryPolicy`, `EpisodicMemoryPolicy`, `ConversationPolicy`, `SlidingWindowPolicy`, `TokenBudgetPolicy`, `AlertPolicy`
- Compaction — `TailWindowCompact`, `SummarizeCompact`

### ag2-middleware

Intercept the AG2 agent loop with `BaseMiddleware`.

**Use when:**

- Adding retry, logging, history trimming, request mutation, tool auditing, guardrails, rate limiting

**Topics covered:**

- Hooks — `on_turn`, `on_llm_call`, `on_tool_execution`, `on_human_input`
- Built-ins — `LoggingMiddleware`, `RetryMiddleware`, `HistoryLimiter`, `TokenLimiter`, `TelemetryMiddleware`
- Per-tool hooks (see also `ag2-add-custom-tool`)

### ag2-observers-and-alerts

Monitor an AG2 agent's stream — log events, detect repeated tool calls, track token spend, build trigger-driven observers, route alerts to the model, and halt on FATAL conditions.

**Use when:**

- Need observability, runtime safety guards, alerts, or batch/time-based reactive logic

**Topics covered:**

- `@observer(...)` (stateless), `BaseObserver` (stateful)
- Built-ins — `TokenMonitor`, `LoopDetector`
- `Watch` primitives — `EventWatch`, `CadenceWatch`, `DelayWatch`, `IntervalWatch`, `CronWatch`, `AllOf`, `AnyOf`, `Sequence`
- `ObserverAlert` (`Severity.INFO/WARNING/CRITICAL/FATAL`), `AlertPolicy`, `HaltEvent`

### ag2-subagent-delegation

Delegate work from one AG2 `Agent` to another.

**Use when:**

- A coordinator should spawn sub-tasks, fan out concurrent work, or hand off to a specialist agent

**Topics covered:**

- Auto-injected `run_subtask` / `run_subtasks(parallel=True)` (opt in via `tasks=TaskConfig(...)`)
- `Agent.as_tool()` for named delegates between distinct agents
- Context flow, recursion safety
- `persistent_stream` for sub-task history

### ag2-hitl

Pause an AG2 `Agent` mid-run to collect human input, or gate a tool call with approval.

**Use when:**

- Agent should ask for confirmation, request missing info (passwords, API keys, data)
- A human must approve sensitive / irreversible / expensive tool calls (sending emails, deleting records, payments)

**Topics covered:**

- `context.input()` for in-run human prompts
- `approval_required()` middleware

### ag2-ag-ui

Expose an AG2 `Agent` over the AG-UI protocol so a frontend (CopilotKit, custom React/Next.js, or any AG-UI client) can stream responses, render tool calls, sync shared state, and surface human-input checkpoints.

**Use when:**

- Building a web frontend for an AG2 agent rather than a CLI / script

**Topics covered:**

- `AGUIStream(agent)` wrapper
- FastAPI mounting via `stream.dispatch(...)` or `stream.build_asgi()`

### ag2-telemetry

Add OpenTelemetry traces to an AG2 `Agent` via `TelemetryMiddleware`.

**Use when:**

- Need production-grade traces, latency analysis, token-usage attribution
- Shipping telemetry into an existing observability stack (Jaeger, Grafana Tempo, Datadog, Honeycomb, Langfuse)

**Topics covered:**

- Spans for full turn, each LLM call, each tool execution, each human-input request
- OpenTelemetry GenAI semantic conventions
- Any OTLP backend

### ag2-testing

Test AG2 agents and tools without hitting a real LLM provider.

**Use when:**

- Writing pytest tests for an `Agent` or `Tool`

**Topics covered:**

- `TestConfig(...)` from `autogen.beta.testing` — pass as agent's config or per-`ask`
- Mocking LLM responses
- Injecting `ToolCallEvent`s to simulate tool execution
- Asserting success / error paths

## Installation

Install the whole collection with the [`skills`](https://skills.sh) CLI:

```bash
npx skills add ag2ai/ag2-skills
```

To install a single skill, append `@<skill-name>`:

```bash
npx skills add ag2ai/ag2-skills@ag2-quickstart
```

### Manual install (Claude Code)

```bash
git clone https://github.com/ag2ai/ag2-skills.git
cp -r ag2-skills/skills/ag2-overview ~/.claude/skills/
cp -r ag2-skills/skills/ag2-quickstart ~/.claude/skills/
# ...repeat for the skills you need
```

### claude.ai

Upload the corresponding `.zip` from `skills/` in the project's Skills settings, or paste the contents of `SKILL.md` into the conversation.

### AG2 agents (programmatic)

AG2's built-in `Skills` toolkit can load a local skills directory — see the `ag2-use-builtin-tools` skill for the wiring.

## Skill Structure

Each skill contains:

- `SKILL.md` — instructions for the agent (required)
- `scripts/` — helper scripts for automation (optional)
- `references/` — supporting documentation (optional)

Skills are loaded on-demand: only the `name` and `description` from the frontmatter are present at startup. The full `SKILL.md` is loaded only when the agent decides the skill is relevant. See [`AGENTS.md`](./AGENTS.md) for authoring guidance.

## Contributing

See [`AGENTS.md`](./AGENTS.md) for skill format, naming conventions, and packaging steps.

## License

Apache-2.0 — see [`LICENSE`](./LICENSE).
