---
name: ag2-overview
description: Map of AG2 (ag2 1.3) capabilities and which sibling skill to reach for. Load first when the user mentions building with AG2 (ag2) but the specific feature isn't yet clear — agents, model config, tools, MCP, delegation/subagents, memory and context assembly, middleware, observers, structured output, HITL approval, testing, evaluation, AG-UI, A2A, ACP, realtime voice, telemetry, or multi-agent networks. Also lists the 1.3 APIs that are deprecated and removed in 1.4.
license: Apache-2.0
---

# AG2 — capability map

AG2 (`ag2`) is an async, protocol-driven agent framework. These skills target **ag2 1.3**. Full reference docs live under `website/docs/user-guide/` in the ag2 repo. This skill is the index: pick the sibling skill below, then load its `SKILL.md`.

## Before you start

1. **Install the provider extra** — `pip install "ag2[openai]"`, `ag2[anthropic]`, `ag2[gemini]`, … Without it the `*Config` raises `ImportError: ... requires optional dependencies`. Feature extras are separate (e.g. `ag2[tracing]` for evaluation/telemetry, `ag2[ag-ui]`, `ag2[a2a]`, `ag2[redis]`); each skill states its own. Run the install before delivering code, or state the exact command if you can't.
2. **Set the API key** — `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` (or `GOOGLE_API_KEY`). `from dotenv import load_dotenv; load_dotenv()` is the common pattern.
3. **Check which ag2 you import** — `python -c "import sys; from importlib.metadata import version; print(sys.executable, version('ag2'))"`.

Per-provider table (install + env var + config class) → `ag2-quickstart`.

## Deprecated in 1.3, removed in 1.4 — don't write these

| Old | Use instead | Skill |
|---|---|---|
| `OpenAIConfig(...)` (Chat Completions) | `OpenAIResponsesConfig` (default), or `OpenAICompletionsConfig` (Chat Completions / OpenAI-compatible `base_url=`) | `ag2-quickstart` |
| `AnthropicConfig(temperature=/top_p=/top_k=)` | omit sampling params | `ag2-quickstart` |
| `Agent(knowledge=KnowledgeConfig(...))` | `plugins=[KnowledgePlugin(store, ...)]` | `ag2-knowledge-and-memory` |
| `Agent(assembly=[...])` | `middleware=[Middleware(AssemblerMiddleware, policies=[...]), Middleware(HaltCheckMiddleware)]` | `ag2-middleware` |
| `TaskConfig`, `Agent(tasks=...)`, `run_subtask(s)` | explicit workers: `worker.as_tool()`, subagent tools | `ag2-subagent-delegation` |
| `agent.task(...)` | `Task(owner_id=..., spec=TaskSpec(...))` from `ag2.task`, or `ask(..., capability=)` | `ag2-network-governance` |

## Pick the right skill

| User intent | Skill | Key APIs |
|---|---|---|
| Build an `Agent`, pick a model, multi-turn | `ag2-quickstart` | `Agent`, `OpenAIResponsesConfig`, `AnthropicConfig`, `GeminiConfig`, `ask()` / `reply.ask()`, `reply.body` |
| Custom Python tool, DI, shared state | `ag2-add-custom-tool` | `@tool`, `Inject`, `Depends`, `Variable`, `Scope`/`ScopedValue`, `reply.context.variables` |
| Shipped tools; consume an MCP server | `ag2-use-builtin-tools` | `WebSearchTool`, `WebFetchTool`, `CodeExecutionTool`, `MCPServerTool`, `MCPToolkit`, `FilesystemToolkit`, search toolkits, `SkillsTool` |
| Run shell commands / code | `ag2-shell-tool` | `SandboxShellTool` + `LocalEnvironment`/Docker, `ShellTool`, `AnthropicBashTool`, `SandboxCodeTool` |
| Typed Pydantic / dataclass output | `ag2-structured-output` | `response_schema=`, `reply.content()`, `PromptedSchema`, retries |
| Images / audio / video / PDFs in | `ag2-multimodal-input` | `ImageInput`, `DocumentInput`, `BinaryInput`, `FilesAPI` |
| Coordinator + worker agents, parallel fan-out | `ag2-subagent-delegation` | `Agent.as_tool()`, subagent tools, `TaskStarted`/`ChildEvent`, shared concurrency cap |
| Human input / tool approval | `ag2-hitl` | `context.input()`, `hitl_hook`, `approval_required()`, `ToolApprovalRequest` |
| Logging, retry, interception, context assembly | `ag2-middleware` | `BaseMiddleware` (pass the **class**), `Middleware(Cls, **kw)`, `RetryMiddleware`, `AssemblerMiddleware`, `statusbar_middleware` |
| Unit-test agents without API calls | `ag2-testing` | `TestConfig`, `TrackingConfig`, `ToolCallEvent(arguments=...)` |
| Offline evaluation, CI gate | `ag2-evaluation` | `Suite`, `run_agent`, scorers, `agent_judge`, `diff()` (needs `ag2[tracing]`) |
| Compare models / prompts | `ag2-eval-comparison` | `run_variants`, `run_pairwise`, `pairwise_judge`, `human_pairwise` |
| Memory across runs, history caps, compaction | `ag2-knowledge-and-memory` | `KnowledgePlugin`, `SqliteKnowledgeStore`/`DiskKnowledgeStore`, `WorkingMemoryPolicy`, `SlidingWindowPolicy`, `SummarizeCompact` |
| Token / loop monitoring, alerts, halts | `ag2-observers-and-alerts` | `BaseObserver`, `TokenMonitor`, `LoopDetector`, `AlertPolicy` + `HaltCheckMiddleware` (only FATAL halts) |
| OpenTelemetry traces / metrics | `ag2-telemetry` | `TelemetryMiddleware`, GenAI semconv |
| Web frontend (AG-UI 1.0 / CopilotKit) | `ag2-ag-ui` | `AGUIStream`, `read_run_input`, event types |
| Serve an agent over MCP | `ag2-mcp` | `MCPServer`, `@mcp_tool`, `Elicit`/`Sample`, `MCPApp` |
| A2A protocol (serve / consume) | `ag2-a2a` | `A2AServer`, `A2AConfig`, `AgentCard`, card signing |
| ACP — drive coding agents / serve to editors | `ag2-acp` | `ACPConfig`, `ACPAgent`, `ACPRemoteConfig` |
| Realtime voice, STT, TTS | `ag2-live` | `LiveAgent`, `CascadeConfig`, realtime configs, transcribers, `TTSObserver` |

## Multi-agent networks

When two or more *registered* agents collaborate through a shared `Hub`, load **`ag2-network-quickstart`** first (hub setup, `consulting` 1Q1R and `conversation` adapters, reading what was said). Then:

| User intent | Skill |
|---|---|
| N-party round-robin discussion | `ag2-network-discussion` |
| Declarative orchestration, `TransitionGraph`, GroupChat migration | `ag2-network-workflow` |
| Rate limits, access rules, expectations, audit, task observation | `ag2-network-governance` |
| Network tools (`delegate`/`peers`/…), custom handlers, views, envelopes | `ag2-network-tools-and-views` |

**Not a network task:** one coordinator calling worker agents as tools → `ag2-subagent-delegation` (no hub, no channels).
