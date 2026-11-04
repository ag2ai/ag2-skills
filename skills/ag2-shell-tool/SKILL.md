---
name: ag2-shell-tool
description: Give an AG2 `Agent` the ability to run shell commands or code. Covers `SandboxShellTool` (client-side, any provider) with execution backends `LocalEnvironment`, `DockerEnvironment`, `DaytonaEnvironment`, `TenkiEnvironment`; command filtering and restricted mode (`allowed`, `blocked`, `ignore`, `readonly`); `AnthropicBashTool` (Anthropic's `bash_20250124`, executed locally by AG2); the provider-hosted `ShellTool` (OpenAI Responses only, needs `ContainerAutoEnvironment` / `ContainerReferenceEnvironment` + `NetworkPolicy`); and `SandboxCodeTool` (`run_code`). Use when the agent should execute commands, build/test code, or operate on a workspace. For provider-run code execution (`CodeExecutionTool`) and other built-in tools see `ag2-use-builtin-tools`; to gate commands behind human approval see `ag2-hitl`.
license: Apache-2.0
---

# Shell tools

## When to use — pick one deliberately

| Need | Use |
|---|---|
| Shell on **any** provider, you own where it runs | `SandboxShellTool` (default choice) |
| Claude using the bash tool it was trained on, executed on your side | `AnthropicBashTool` (Anthropic only) |
| Throwaway container hosted by OpenAI, no local access | `ShellTool(environment=ContainerAutoEnvironment())` (OpenAI Responses, gpt-5.2+) |
| A `run_code(code, language)` tool instead of raw shell | `SandboxCodeTool(env)` |

All take the same backends (`LocalEnvironment`, `DockerEnvironment`, `DaytonaEnvironment`, `TenkiEnvironment`) except `ShellTool`.

## Recipe — `SandboxShellTool`

```python
from ag2 import Agent
from ag2.config import OpenAIResponsesConfig
from ag2.tools import SandboxShellTool

agent = Agent(
    "coder",
    "You write and run Python code.",
    config=OpenAIResponsesConfig(model="gpt-4.1-mini"),   # any provider works
    tools=[SandboxShellTool()],
)
reply = await agent.ask("Write a hello world script with python3 and run it.")
print(await reply.content())
```

No argument → `LocalEnvironment()`: a temp dir (prefix `ag2_sandbox_`) deleted at exit. `LocalEnvironment("/path")` uses (and creates) that dir and keeps it. `tool.workdir` gives the resolved dir. Files persist across `ask()` / `reply.ask()` turns.

## Lock it down

```python
from ag2.tools import LocalEnvironment, SandboxShellTool

sh = SandboxShellTool(
    LocalEnvironment("/srv/project", timeout=30, max_output=50_000, env_vars={"CI": "1"}),
    allowed=["python", "uv run", "git log", "ls", "cat"],   # prefix match, word by word
    ignore=["**/.env", "*.key", "secrets/**"],              # path words matching -> "Access denied: <path>"
)
ro = SandboxShellTool(LocalEnvironment("/srv/project"), readonly=True)
```

**Restricted mode (1.3):** setting *any* of `allowed`, `blocked`, `ignore`, `readonly` splits the command into argv once and runs it **without a shell**. Pipes/redirects/chaining (`|`, `>`, `;`, `&&`, …) are rejected with a clear error; globs (`*.py`), `$VARS`, `~`, `$(...)` are passed literally; `echo`/`pwd` are the PATH binaries. With no filter set, commands run through `sh -c` with full syntax.

- `readonly=True` allows only `cat head tail ls grep egrep fgrep wc du df diff stat which pwd echo printenv cut` — **no `git`, `find`, `file`** (they can write/exec). Need `git log`? Pass your own `allowed=[...]` (it replaces the read-only set).
- `blocked=["rm"]` matches the program by basename (`/bin/rm` caught) — but can't stop `sh -c`, `env`, `xargs`. Not a security boundary; use `allowed` or a container backend.

## Isolated backends

```python
from ag2.extensions.docker import DockerEnvironment     # pip install "docker>=6.0.0,<8"  (needs a Docker daemon)
from ag2.tools import SandboxCodeTool, SandboxShellTool

async with DockerEnvironment(image="python:3.12-slim", network_mode="none") as env:
    agent = Agent("dev", config=config, tools=[SandboxShellTool(env), SandboxCodeTool(env)])
    await agent.ask("Write hello.txt with the shell, then read it with Python.")
# container removed here; shell and run_code shared one container (workdir /workspace)
```

`DaytonaEnvironment` (`pip install "daytona>=0.171.0,<1"`, `DAYTONA_API_KEY`) and `TenkiEnvironment` (`pip install "tenki>=1.4.0,<2"`, `TENKI_API_KEY`; workdir `/home/tenki`; use `SandboxCodeTool(env.code_environment())` because its image has `python3`, not `python`) work the same way.

## `AnthropicBashTool` and `ShellTool`

```python
from ag2.config import AnthropicConfig, OpenAIResponsesConfig
from ag2.tools import AnthropicBashTool, ContainerAutoEnvironment, NetworkPolicy, ShellTool

claude = Agent("ops", config=AnthropicConfig(model="claude-haiku-4-5"),
               tools=[AnthropicBashTool(LocalEnvironment("/srv/project"))])  # same allowed/blocked/ignore/readonly kwargs

hosted = Agent("ops", config=OpenAIResponsesConfig(model="gpt-5.2"),
               tools=[ShellTool(environment=ContainerAutoEnvironment(
                   network_policy=NetworkPolicy(allowed_domains=["pypi.org"])))])
```

Parameter tables, filter order, backend matrix, custom backends (`SandboxFactory`, `WorkdirAware`): `references/shell_reference.md`.

## Common pitfalls

- **`ShellTool()` without `environment=` raises `ClientExecutedShellUnsupportedError`** — pass `ContainerAutoEnvironment()` (or use `SandboxShellTool`). Hosted shell needs **gpt-5.2 or later**; older models return 400 "Tool 'shell' is not supported".
- **Wrong provider raises `UnsupportedToolError`** — `ShellTool` is OpenAI-only; `AnthropicBashTool` is Anthropic-only. `SandboxShellTool` works everywhere.
- **Restricted mode surprises the model** — it still writes `ls *.py | wc -l` and may conclude "0 files". `SandboxShellTool`'s auto-description says "runs without a shell"; `AnthropicBashTool`'s fixed definition cannot, so say it in the agent prompt ("commands run without a shell: no pipes, redirects or globs").
- **No filters on `LocalEnvironment` = arbitrary commands on your machine** (60 s timeout). Set `allowed`/`readonly`, or use Docker/Daytona/Tenki for real isolation.
- **`ignore` only checks literal path words** — a program that opens paths itself (`sh -c`, `find`) is not covered.
- **Shared hard-coded dirs race** across parallel agents — use `tempfile.mkdtemp()`.
- **Human approval before each command** — `SandboxShellTool(env, middleware=[approval_required()])` (`from ag2.middleware import approval_required`) plus an agent `hitl_hook`; see `ag2-hitl`.
