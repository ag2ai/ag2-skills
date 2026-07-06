---
name: ag2-acp
description: "Drive external CLI coding agents (Claude Code, Codex, OpenCode) as first-class AG2 Agents over the Agent Client Protocol (ACP): point an Agent at a ClaudeCodeConfig / CodexConfig / OpenCodeConfig preset from ag2.acp and ask()/run() it like any agent, with its thinking, tool calls, plans and permission prompts externalized onto AG2's event stream. Use when orchestrating, observing, or gating CLI coding agents from Python — permission_policy ask/auto/deny HITL gating, fs_root confinement, turn timeouts, in-process testing via fake_acp_config. To expose an AG2 agent to other systems instead, see ag2-a2a or ag2-mcp; for approval plumbing see ag2-hitl."
license: Apache-2.0
---

# CLI coding agents over ACP

Drive external CLI coding agents — **Claude Code**, **Codex**, **OpenCode** — as first-class AG2 `Agent`s, using the [Agent Client Protocol](https://agentclientprotocol.com) (ACP). AG2 plays the ACP **Client** role; each CLI agent runs as an ACP **Agent** subprocess. Everything the agent does — message output, thinking, tool calls, plans, permission prompts — is externalized onto AG2's event stream, so you can observe, gate, and orchestrate it like any other AG2 agent.

The integration is just a config class (`ACPConfig` and its presets) — no changes to the `Agent` API.

## When to use

- You want to **orchestrate CLI coding agents from Python**: headless refactoring pipelines, "manager" agents delegating to coder agents, batch code-mod runs.
- You want to **observe** a coding agent's work live — thoughts, tool calls, plans — on the AG2 event stream.
- You want to **gate** the agent's sensitive actions (file writes, shell commands) with a permission policy or a human in the loop.
- You need the agent's file access **confined** to a workspace root (`fs_root`) and its terminal use mediated by AG2.

Not this skill: exposing an AG2 agent *to* other systems — that is `ag2-a2a` (A2A protocol) or `ag2-mcp` (MCP server). For the HITL input plumbing that `permission_policy="ask"` relies on, see `ag2-hitl`.

## Installation

```bash
pip install "ag2[acp]"
```

The `acp` extra pulls in the `agent-client-protocol` SDK. Each CLI agent additionally needs its own ACP adapter on `PATH`:

| Agent | Adapter install | Auth |
|---|---|---|
| Claude Code | `npm i -g @agentclientprotocol/claude-agent-acp` (bin `claude-agent-acp`) | `ANTHROPIC_API_KEY` in `env`, or `CLAUDE_CONFIG_DIR` pointing at an existing Claude Code login |
| Codex | `npm i -g @agentclientprotocol/codex-acp` (bin `codex-acp`) | `CODEX_API_KEY` (takes precedence) or `OPENAI_API_KEY` |
| OpenCode | `opencode` CLI itself (`opencode acp`) | `opencode auth login` (or env / `.env`) |

No global install? Override the launch command to use npx: `ClaudeCodeConfig(command=["npx", "-y", "@agentclientprotocol/claude-agent-acp"])`.

Public API (`from ag2.acp import ...`): `ACPConfig`, `ClaudeCodeConfig`, `CodexConfig`, `OpenCodeConfig`.

## 60-second recipe — ask a coding agent to do work

```python
import asyncio

from ag2 import Agent
from ag2.acp import ClaudeCodeConfig

async def main():
    config = ClaudeCodeConfig(cwd="/path/to/repo")  # workspace root
    agent = Agent("coder", config=config)
    try:
        reply = await agent.ask("Refactor the auth module and add tests")
        print(reply.body)
    finally:
        await config.aclose()  # tear down the CLI subprocess

asyncio.run(main())
```

One `ask()` / `run()` = one ACP **prompt turn**: the CLI agent runs its own internal tool loop (possibly many tool calls) and AG2 streams every step as it happens.

## Choosing an adapter

- `ClaudeCodeConfig()` — launches `claude-agent-acp`. Select the model via the adapter's `ANTHROPIC_MODEL` env var.
- `CodexConfig()` — launches `codex-acp`. Model via the adapter's `MODEL_PROVIDER` env var.
- `OpenCodeConfig()` — launches `opencode acp`. Model in OpenCode's own config (`opencode.json`: `"model": "provider/model"`).

> The presets' `model` field is **response metadata only** — it is not sent to the agent. Pick the model through each adapter's own mechanism (env var / config file) as above.

## Observing the agent's work

Subscribe to the run's stream **before** awaiting the result:

```python
from ag2 import Agent
from ag2.acp import ClaudeCodeConfig
from ag2.acp.events import ACPPlan
from ag2.events import ModelMessageChunk, ModelReasoning
from ag2.events.tool_events import BuiltinToolCallEvent

agent = Agent("coder", config=ClaudeCodeConfig(cwd="/path/to/repo"))

def observe(event):
    if isinstance(event, ModelReasoning):
        print("thinking:", event.content)
    elif isinstance(event, ModelMessageChunk):
        print(event.content, end="")
    elif isinstance(event, BuiltinToolCallEvent):
        print(f"tool: {event.name}({event.arguments})")  # arguments = JSON string of the tool input
    elif isinstance(event, ACPPlan):
        for step in event.entries:
            print(f"  [{step.status}] {step.content}")

async with agent.run("Add a healthcheck endpoint") as run:
    run.stream.subscribe(observe)
    reply = await run.result()
```

How ACP session updates map onto AG2 events:

| ACP update | AG2 event |
|---|---|
| agent message chunk | `ModelMessageChunk` → final `ModelResponse` |
| thinking chunk | `ModelReasoning` |
| tool call / tool result | `BuiltinToolCallEvent` / `BuiltinToolResultEvent` |
| plan | `ACPPlan` (entries with `.content` / `.status` / `.priority`) |
| mode change | `ACPModeChange` (`.mode_id`) |
| available commands | `ACPAvailableCommands` (`.commands`) |

The ACP-specific events live in `ag2.acp.events`; the rest are the standard events from `ag2.events`.

## Permissions (human-in-the-loop)

When the agent wants to perform a sensitive action (write a file, run a command), it sends a permission request. `permission_policy` decides the answer:

| Policy | Behavior |
|---|---|
| `"ask"` (default) | Route to the agent's `hitl_hook` / `context.input` — a human decides |
| `"auto"` | Approve automatically (headless orchestration) |
| `"deny"` | Reject automatically |

```python
agent = Agent(
    "coder",
    config=ClaudeCodeConfig(cwd="/repo", permission_policy="auto"),  # fully autonomous
)
```

> **Pitfall:** `"ask"` with no input route available (no `hitl_hook`, no interactive context) **denies** the request — a headless run with the default policy will quietly reject every sensitive action. For unattended runs set `permission_policy="auto"` explicitly.

## Configuration reference

`ACPConfig` (and every preset) accepts:

| Field | Default | Purpose |
|---|---|---|
| `command` | preset per agent | Executable + args launching the agent in ACP mode |
| `cwd` | `"."` | Workspace root for the session |
| `env` | `None` | Extra env vars, merged over a **trimmed** base env (`HOME`, `PATH`, `USER`, `SHELL`, `TERM`, `LOGNAME` — not the full parent env); pass API keys here explicitly |
| `model` | `None` | Response metadata only — see "Choosing an adapter" |
| `permission_policy` | `"ask"` | `"ask"` / `"auto"` / `"deny"` |
| `fs_root` | `cwd` | Root for mediated `fs/*` access (path-confined) |
| `allow_terminal` | `True` | Advertise the ACP terminal capability |
| `additional_directories` | `[]` | Extra workspace roots |
| `startup_timeout` | `30.0` | Subprocess spawn + handshake timeout (s) |
| `turn_timeout` | `None` | Per-prompt-turn timeout (s); on expiry the turn is cancelled and the reply body is whatever streamed so far |
| `cancel_timeout` | `5.0` | Grace period (s) after a timed-out turn signals `session/cancel` before the subprocess is hard-stopped |

File and terminal operations the agent requests are mediated by AG2: file access is confined to `fs_root`, and commands run under AG2's control. `config.copy(**overrides)` clones a config (sessions are not carried over).

## Lifecycle

The ACP subprocess is spawned on the first turn and **reused** across turns of the same run. Call `await config.aclose()` to tear down all live subprocesses started from a config (a finalizer terminates them as a safety net if you forget).

## Testing — in-process, no subprocess, no API keys

`ag2.acp.testing.fake_acp_config` wires an `ACPConfig` to a scripted in-process agent: each `ACPTurn` describes one prompt turn (the `session/update`s it emits and the stop reason). Your code exercises the full public `Agent.run` path.

```python
import asyncio

from acp import schema

from ag2 import Agent
from ag2.acp.testing import ACPTurn, fake_acp_config

def text(t):
    return schema.TextContentBlock(type="text", text=t)

async def main():
    config = fake_acp_config(
        ACPTurn(updates=[
            schema.AgentThoughtChunk(session_update="agent_thought_chunk", content=text("planning")),
            schema.AgentMessageChunk(session_update="agent_message_chunk", content=text("done")),
        ]),
        permission_policy="auto",  # overrides forward to ACPConfig
    )
    agent = Agent("coder", config=config)
    try:
        reply = await agent.ask("hello")
        assert reply.body == "done"
    finally:
        await config.aclose()

asyncio.run(main())
```

`ACPTurn(hang=True)` blocks until cancelled — use it to exercise `turn_timeout` handling.

## Common pitfalls

- **Missing `acp` extra** — `pip install "ag2[acp]"`; without it `from ag2.acp import ...` fails on the missing `acp` SDK.
- **Exported API keys are not inherited** — the subprocess env is a trimmed base set plus `env=`, so `export ANTHROPIC_API_KEY=...` in your shell does not reach the agent. Pass it via `env={"ANTHROPIC_API_KEY": ...}`. (`CLAUDE_CONFIG_DIR` logins work because `HOME` *is* in the base set.)
- **Deprecated adapter name** — the old `claude-code-acp` (`@zed-industries/claude-code-acp`) is deprecated; use `claude-agent-acp` (`@agentclientprotocol/claude-agent-acp`), which is what `ClaudeCodeConfig` launches.
- **`"ask"` in headless runs = deny** — with no human input route, every permission request is rejected. Set `permission_policy="auto"` for unattended orchestration.
- **`model=` does nothing on the wire** — select the model via `ANTHROPIC_MODEL` / `MODEL_PROVIDER` / `opencode.json` instead.
- **Adapter not on `PATH`** — `startup_timeout` errors usually mean the launch command wasn't found; install the adapter globally or use the `npx -y` command override.
- **AG2 `tools=[...]` are not exposed to the CLI agent yet** — CLI-backed agents use their own built-in tools; the MCP tool bridge for AG2-provided tools is an upstream roadmap item.

## Going deeper (source of truth)

- `ag2/acp/config.py` — `ACPConfig` + the three presets and their defaults.
- `ag2/acp/client.py` / `bridge.py` / `session.py` — the ACP Client, event bridging, subprocess lifecycle.
- `ag2/acp/mappers.py` — the exact ACP-update → AG2-event mapping.
- `ag2/acp/permissions.py` — how `permission_policy` resolves permission requests.
- `ag2/acp/events.py` — `ACPPlan`, `ACPModeChange`, `ACPAvailableCommands`.
- `ag2/acp/testing.py` — `fake_acp_config`, `ACPTurn`.
- ACP protocol: https://agentclientprotocol.com
