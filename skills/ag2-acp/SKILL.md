---
name: ag2-acp
description: "Agent Client Protocol (ACP) in both directions. CLIENT: drive CLI coding agents (Claude Code, Codex, OpenCode, Kilo Code) as AG2 Agents via `ClaudeCodeConfig`/`CodexConfig`/`OpenCodeConfig`/`KiloCodeConfig`/`ACPConfig`, or a remote agent via `ACPRemoteConfig(url=, headers=)`; stream thinking/tool calls/plans (`ACPPlan`, `ACPElicitation`), gate with `permission_policy` and `elicitation_policy`, expose AG2 tools to the CLI agent over MCP (`expose_tools`), `model=` selection, trimmed `env`, `turn_timeout`, `async with config`. SERVER: `ACPAgent(agent).run_stdio()` serves an AG2 agent to ACP clients/editors (Zed, JetBrains, VS Code, Neovim) with `SessionConfig`, `StaticTokenAuth`/`AuthProvider`, `PromptContent`, `hitl_hook`, `session/load`. Testing: `ag2.acp.testing.fake_acp_config`/`fake_remote_acp_config`/`connect`. For serving over MCP see `ag2-mcp`; A2A `ag2-a2a`; web UIs `ag2-ag-ui`; approvals `ag2-hitl`."
license: Apache-2.0
---

# ACP: drive CLI coding agents, or serve your agent to editors

| Direction | AG2 role | API |
|---|---|---|
| Orchestrate Claude Code / Codex / OpenCode / Kilo Code from Python | ACP **Client** | `Agent(config=ClaudeCodeConfig(...))` etc., `ACPRemoteConfig` |
| Make your AG2 agent usable from Zed / JetBrains / VS Code / any ACP client | ACP **Agent** | `ACPAgent(agent).run_stdio()` |

```bash
pip install "ag2[acp]"                                   # agent-client-protocol 0.12 (+ mcp)
pip install "agent-client-protocol[http]"                # only for ACPRemoteConfig (HTTP / WebSocket)
```

## Client: drive a CLI coding agent

```python
import os
from ag2 import Agent
from ag2.acp import ClaudeCodeConfig

def get_ticket(ticket_id: str) -> dict:
    """Fetch a ticket from the internal tracker."""
    return {"id": ticket_id, "status": "open"}

async with ClaudeCodeConfig(
    cwd="/path/to/repo",
    env={"ANTHROPIC_API_KEY": os.environ["ANTHROPIC_API_KEY"]},  # shell exports are NOT inherited
    permission_policy="auto",          # "ask" (default, -> hitl_hook) | "auto" | "deny"
    elicitation_policy="decline",      # "ask" (default) | "decline" — for headless runs
    turn_timeout=600,
) as config:                            # closes subprocess + tool gateway on exit
    agent = Agent("coder", config=config, tools=[get_ticket])   # tools are served to the CLI agent over MCP
    reply = await agent.ask("Look up ticket ACME-4211, then add a healthcheck endpoint with tests.")
    print(reply.body)
    follow_up = await reply.ask("Now run the tests.")             # same ACP session
```

| Preset | Launches | Auth (pass keys in `env=`, or rely on a login under `$HOME`) |
|---|---|---|
| `ClaudeCodeConfig` | `claude-agent-acp` (`npm i -g @agentclientprotocol/claude-agent-acp`) | `ANTHROPIC_API_KEY`, or `~/.claude` login / `CLAUDE_CONFIG_DIR` |
| `CodexConfig` | `codex-acp` (`npm i -g @agentclientprotocol/codex-acp`) | `CODEX_API_KEY` / `OPENAI_API_KEY`, or `codex login` |
| `OpenCodeConfig` | `opencode acp` | `opencode auth login` |
| `KiloCodeConfig` | `kilo acp` (`npm i -g @kilocode/cli`) | `kilo auth login`; **always set `model=`** |
| `ACPConfig(command=[...])` | anything speaking ACP on stdio (including an AG2 `ACPAgent`) | `env=` |

- **Env is trimmed** to `HOME, LOGNAME, PATH, SHELL, TERM, USER` + `env=`. Billing follows the credential (API key vs subscription login).
- `model=` is applied via `session/set_config_option` (Claude Code/Codex: bare ids; OpenCode/Kilo: `"provider/model"`); an unknown id → `ValueError` before the first turn.
- `tools=[...]` are now **exposed** to the CLI agent through an in-process loopback MCP gateway (`expose_tools=True` default). Provider builtins (`WebSearchTool`, …) → `UnsupportedToolError`; set `expose_tools=False` for the old behaviour. Agent must support HTTP MCP, else `MCPCapabilityError`.
- `permission_policy="ask"` / `elicitation_policy="ask"` with no `hitl_hook` → requests are denied/cancelled; use `"auto"`/`"decline"` for unattended runs.
- Images/documents: `agent.ask("What's wrong here?", ImageInput(path="shot.png"))` — sent if the agent advertises the capability, else a `[image]` placeholder.
- Always `async with config` or `await config.aclose()` — sessions outlive `agent.run()` and are never reclaimed implicitly.

Observe live work by subscribing before awaiting:

```python
from ag2.acp.events import ACPElicitation, ACPPlan
from ag2.events import ModelMessageChunk, ModelReasoning
from ag2.events.tool_events import BuiltinToolCallEvent

async with agent.run("Add a healthcheck endpoint") as run:
    run.stream.subscribe(lambda e: print(type(e).__name__))
    reply = await run.result()
```

ACP update → AG2 event: message → `ModelMessageChunk`/`ModelResponse`; thinking → `ModelReasoning`; tool call/result → `BuiltinToolCallEvent`/`BuiltinToolResultEvent` (`.name`, `.arguments` JSON string); plan → `ACPPlan` (`.entries[].content/.status`); `ACPModeChange`, `ACPAvailableCommands`, `ACPElicitation`.

### Remote agent (HTTP / WebSocket)

```python
from ag2.acp import ACPRemoteConfig
config = ACPRemoteConfig(url="https://dev-box.internal/acp",            # ws:// / wss:// -> WebSocket
                         headers={"Authorization": f"Bearer {os.environ['ACP_TOKEN']}"},
                         cwd="/path/to/repo", expose_tools=False)
```

No `command`/`env` (passing them → `TypeError`). Files/terminal stay on **your** side (mediated by `fs_root`). Exposing tools to a remote agent needs `gateway_address="10.0.0.5:8931"` (plain HTTP on that interface — keep it private). No reconnect.

## Server: serve an AG2 agent over ACP

```python title="serve_stdio.py"
import asyncio
from ag2 import Agent
from ag2.acp import ACPAgent
from ag2.config import OpenAIResponsesConfig

agent = Agent(name="workie", prompt="You are concise.",
              config=OpenAIResponsesConfig(model="gpt-4.1-mini", streaming=True))  # streaming=True or the client sees text only at turn end

if __name__ == "__main__":
    asyncio.run(ACPAgent(agent).run_stdio())    # stdout is the wire: never print()
```

Zed `settings.json`: `{"agent_servers": {"workie": {"type": "custom", "command": "/abs/.venv/bin/python", "args": ["/abs/serve_stdio.py"], "env": {"OPENAI_API_KEY": "sk-..."}}}}` — use the venv interpreter by absolute path.

`ACPAgent(agent, *, name, version, title, sessions=SessionConfig(max_sessions=1024, ttl=None, storage=None, max_queued=8, max_concurrent_turns=8, max_active_prompts=64, retain_history=False), auth=StaticTokenAuth("secret") | AuthProvider, prompt_content=PromptContent(image=True, audio=False, embedded_context=True), stream_thoughts=False, hitl_hook=..., on_session=...)`.

- Each `session/new` = isolated history + variables, but **not tenant isolation** (same tools/deps). One tenant per process.
- `session/cancel` → prompt returns `stop_reason="cancelled"`. `session/load` replays history (durable with `SessionConfig(storage=RedisStorage(...), retain_history=True)`); session id is the credential.
- `context.input()` / `approval_required()` in a served turn need `ACPAgent(hitl_hook=...)` (the agent's own hook is ignored); otherwise the turn fails with `HumanInputUnsupportedError`.
- Auth gates sessions until `authenticate(method_id="token", token=...)`; it authenticates a connection, not a principal.

## Testing (no subprocess, no keys)

- Client side: `fake_acp_config(ACPTurn(updates=[schema.AgentMessageChunk(...)]), permission_policy="auto")` / `fake_remote_acp_config(..., url=...)`; `ACPTurn(hang=True)` exercises `turn_timeout`.
- Server side: `async with ag2.acp.testing.connect(ACPAgent(agent)) as (client, recorder):` then `client.new_session(cwd=".")`, `client.prompt(session_id=..., prompt=[acp.text_block("hi")])`, `recorder.updates_for(session_id)`. It may log `Task was destroyed but it is pending` / `Send loop failed` at teardown — SDK noise, results are valid.

## Reference

- `references/test_snippets.py` — in-process tests: scripted CLI agent, served agent, auth gate, host `hitl_hook` (no keys).
- `references/serve_stdio.py` + `references/drive_served_agent.py` — real round trip: AG2 `ACPConfig` drives an AG2 `ACPAgent` subprocess (needs `OPENAI_API_KEY`).
- Upstream: `website/docs/user-guide/acp/{client,server}.mdx`, `examples/acp/server_stdio.py`; source `ag2/acp/`.
