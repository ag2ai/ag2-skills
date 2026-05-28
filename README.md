# AG2 Skills

A collection of skills for [AG2](https://github.com/ag2ai/ag2) — an async, protocol-driven Python agent framework (`autogen.beta`). Skills are packaged instructions and optional helper scripts that extend an AI agent's capabilities.

Skills follow the [Agent Skills](https://agentskills.io/) format.

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
- Opt-outs — `KnowledgeConfig(expose_tool=False, write_event_log=False)`, `DefaultBootstrap(mention_tool=False)`
- Lifecycle events on the stream — `AggregationStarted` / `AggregationFailed` / `CompactionStarted` / `CompactionFailed` / `EventLogFailed`

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

Single-agent recursion and parallel fan-out within one AG2 `Agent`.

**Use when:**

- One coordinator wants to break work into its own sub-tasks
- Fanning out concurrent sub-tasks from a single agent
- Calling a specialist agent as a lightweight tool (no hub, no registry)

**Topics covered:**

- Auto-injected `run_subtask` / `run_subtasks(parallel=True)` (opt in via `tasks=TaskConfig(...)`)
- `Agent.as_tool()` for invoking named delegates from inside another agent
- Context flow, recursion safety
- `persistent_stream` for sub-task history

> For **two or more agents actually collaborating** through a shared hub with registry, durable channels, governance, and turn-taking, use the **`ag2-network-*`** skills below instead.

### ag2-network-quickstart

Build a multi-agent AG2 network — the standard pattern whenever two or more agents need to interact. **Load this first** for any multi-agent task.

**Use when:**

- "Have two agents talk to each other"
- "Set up a multi-agent system / agent network"
- "Agents that can call each other"
- "Replace the classic `GroupChat` / `ConversableAgent.handoffs`"
- Adding a registry, audit trail, or shared inbox for agents

**Topics covered:**

- The mental model — `Hub`, `HubClient`, `AgentClient`, `Envelope`, `Channel`, `LocalLink`
- `Hub.open(MemoryKnowledgeStore())` and the channel lifecycle (INVITED → ACTIVE → CLOSING → CLOSED)
- `Passport` / `Resume` identity basics; `Passport.kind` (`"agent"` / `"human"` / `"remote_agent"`)
- `HumanClient` / `register_human` — non-LLM participants (user-in-the-loop, queue gateway, UI bridge)
- The two 2-party channel adapters — `consulting` (strict 1Q1R, auto-closes) and `conversation` (free-form, app-controlled halt)
- `agent_client.open(...)`, `channel.send(...)`, `wait_for_channel_event`, `hub.read_wal(...)`
- Plugin tools (`NetworkPlugin`: `delegate` / `peers` / `channels` / `tasks` / `context`) vs adapter-owned tools (e.g. `say`); when to register with `attach_plugin=False`
- The five channel-close routes (app `close()`, agent tool, adapter sentinel, workflow `TerminateTarget`, TTL/expectations)
- Routing table to the other 4 network skills

### ag2-network-discussion

Open an AG2 network `discussion` channel — N-party round-robin with fixed turn order.

**Use when:**

- "Three agents debating in turn"
- "Panel discussion / brainstorm with a fixed cast"
- "Round-robin reviewers commenting on a draft"

**Topics covered:**

- `agent_client.open(type="discussion", target=[...], knobs={"ordering": ORDERING_ROUND_ROBIN})`
- `expected_next_speaker` rotation
- The `hc.can_send(...)` probe pattern (handlers skip LLM when it isn't their turn)
- Putting a `HumanClient` in the rotation — non-LLM moderator taking their turn between agents
- Custom handler escape — bypassing the adapter-owned `say` tool when an agent's domain tools shouldn't be hijacked mid-turn
- `DiscussionState`, view-window sizing for N participants
- `turn_within` expectation defaults (`warn` at 120s / `hide` at 600s)
- Four close patterns for `discussion`

### ag2-network-workflow

Build a declarative AG2 network `workflow` channel using `TransitionGraph` — the modern replacement for classic `GroupChat + Agent.handoffs`.

**Use when:**

- Conditional handoffs between agents
- Multi-step pipelines (researcher → writer → editor)
- Triage agent routes to specialists
- Drafter / reviewer feedback loop
- Migrating from classic `GroupChat` / `ReplyResult(target=...)`

**Topics covered:**

- `TransitionGraph` with `initial_speaker`, `transitions`, `default_target`, `max_turns`
- Convenience factories — `TransitionGraph.sequence([...])` and `.round_robin([...])`
- Built-in targets — `AgentTarget`, `RoundRobinTarget`, `StayTarget`, `RevertToInitiatorTarget`, `TerminateTarget`
- Built-in conditions — `Always`, `FromSpeaker`, `ToolCalled`, `ContextEquals`
- Typed `Handoff` return for dynamic routing
- Channel-scoped context variables (`EV_CONTEXT_SET`, `set_context`, `ChannelStateInject`)
- `register_target` / `register_condition` for custom serializable subclasses
- The packet execution model and idempotent-tool requirement
- All eight cookbook patterns (pipeline, hierarchical, star, escalation, redundant, feedback loop, context-aware routing, triage)
- Side-by-side migration from classic `GroupChat`
- Kickoff gotcha — seeding the brief from a `HumanClient` so the first agent drafts from it instead of consuming it as their turn
- `channel.close()`-from-a-tool termination when the graph can't infer "done" from speaker / `ContextEquals` alone
- Exact close-reason semantics — `max_turns` closes with reason `"max_turns"` (not `default_target`'s reason)

### ag2-network-governance

Govern an AG2 multi-agent network — identity, rules, expectations, audit, and task observation.

**Use when:**

- Rate limits, access policy, inbox caps, channel TTLs
- Custom access policy layered on top of `Rule` (e.g. gate on `claimed_capabilities`)
- Authenticate agents at registration
- Set or tune channel-close timing (`acks_within`, `reply_within`, `max_silence`, `turn_within`)
- Live observability on the hub — log rejected sends, alert on inbox pressure, watch turn failures
- Query the audit log for compliance
- Build a capability track record on each agent for peer ranking

**Topics covered:**

- `Passport` / `Resume` (claimed capabilities + hub-mutated `observed`)
- `Rule` with `AccessBlock` / `LimitsBlock` (which nests `RateBlock` and `InboxBlock`)
- `HubArbiter` / `BaseHubArbiter` / `RuleBasedArbiter` / `register_arbiter` — swappable access & routing decisions (`Allow` / `Deny`); layer your own logic on top of the rule data
- `HubListener` / `BaseHubListener` / `register_listener` — live observability hooks (`on_envelope_posted`, `on_envelope_rejected`, `on_turn_failed`, `on_inbox_pressure`, …)
- `AuthAdapter` / `AuthRegistry` registration
- Channel-level `Expectation`s with `audit` / `warn` / `auto_close` handlers
- The hub's append-only audit log and `AUDIT_KIND_*` constants
- Task observation via `agent.task(..., capability=...)` and `TaskMirror`
- `ObservedStat` and reading the track record

### ag2-network-tools-and-views

Shape what an AG2 network agent perceives and which actions its LLM can take.

**Use when:**

- Limit / extend the LLM's network tool surface
- Build a non-LLM participant (gateway, queue forwarder, UI bridge) in a network
- Write a custom envelope handler
- Customise what each agent sees of the channel (view policy)
- Wire peer discovery via skill markdown
- Send custom event types

**Topics covered:**

- The auto-injected LLM tools — plugin tools (`NetworkPlugin`: `delegate` / `peers` / `channels` / `tasks` / `context`) vs adapter-owned tools (`say`, via `adapter.tools_for`); `attach_plugin=False` to drop plugin tools without losing `say`
- `HumanClient` / `register_human` — non-LLM participants; push (`on_envelope`) and pull (`next_envelope`) surface; `auto_ack_invites`
- Replacing the default handler via `agent_client.on_envelope(callback)` — what you lose when you do, and how to delegate non-`EV_TEXT` envelopes back to `default_handler` for invite-ack + lifecycle bookkeeping
- The default handler's public hooks — `read_wal_until`, `resolve_view_policy`, `stamp_dependencies`
- Bypassing adapter tools — running `agent.ask(...)` directly when you need full control of the round-trip
- `ViewPolicy` protocol; built-in `FullTranscript` and `WindowedSummary(recent_n=N)`; writing custom views
- Skill markdown (`skill_md=`, `parse_skill_frontmatter`, `hub.set_skill`, `render_fallback_skill`)
- Full `Envelope` reference — `EV_*` event taxonomy, `audience`, `Priority`, `causation_id`, `visible_to`
- Sending raw envelopes with custom event types

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

### ag2-evaluation

Evaluate, test, and track an AG2 agent offline — run a suite, grade the answers, gate it in CI, and diff runs over time.

**Use when:**

- "Evaluate / test / benchmark my agent", or build a regression / CI gate
- Grade answers for correctness, tool use, cost, or subjective quality
- Track a metric across versions (did this change help or regress?)

**Topics covered:**

- `Suite.from_list` + `run_agent`; the `RunResult` scorecard (`summary`, `pass_rate`, `score_stats`, `value_counts`)
- Prebuilt scorers — `final_answer_matches`, `tool_called`, `no_tool_errors`, `token_budget`, `failure_attribution`, `agent_judge`
- Custom `@scorer` + the return-type → aggregation rule (bool → pass_rate / num → score_stats / str → value_counts)
- CI with deterministic `TestConfig` cassettes (agent factory + `model_config`)
- Persistence — `store_dir`, `load_run`, `diff().regressions`; grading existing traces with `evaluate_traces`

**See also:** `ag2-eval-comparison` for head-to-head and leaderboard comparison.

### ag2-eval-comparison

Compare AG2 agents, models, or prompts to decide which is better — a leaderboard or head-to-head.

**Use when:**

- A/B test prompts or models; rank N configs on a leaderboard
- Decide which of two is better, head-to-head
- Collect human preference labels

**Topics covered:**

- `run_variants` + `Variants.from_configs` / `from_prompts` / `from_tools` / `from_middleware` / `from_targets`; `board.summary` / `best` / `results`
- `run_pairwise` + `pairwise_judge` — dual-order position swap, `win_rate` (Wilson CI), `flips`, `agreement` (Cohen's κ)
- `human_pairwise` — blinded human vote via an inline `ask` callback
- Offline labeling at scale — `export_pairwise_cases`, `human_labels`, `evaluate_pairwise`

**See also:** `ag2-evaluation` for running and grading a single agent.

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
