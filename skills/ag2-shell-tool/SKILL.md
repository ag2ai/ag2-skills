---
name: ag2-shell-tool
description: Give an AG2 beta `Agent` the ability to run shell commands. Covers `SandboxShellTool` (client-side `subprocess` via `LocalEnvironment`, works with any provider) and the provider-native `ShellTool` (OpenAI Responses execution). Use when the user wants the Agent to execute commands, build/test code, manage files, or operate on a workspace. Always pair with sandboxing — `allowed`, `blocked`, `ignore`, or `readonly`.
license: Apache-2.0
---

# Shell tools

## When to use

Two distinct tools, both named "shell" — pick deliberately:

| Need | Use | Why |
|---|---|---|
| Works with any model provider; full control over what runs and where | `SandboxShellTool` | Client-side `subprocess` (via `LocalEnvironment`). You own the sandbox. |
| Provider-managed sandbox (container, network policy) on OpenAI Responses | `ShellTool` | Server-side execution. No local subprocess. |

**`SandboxShellTool` is the workhorse**. Reach for it unless you specifically need provider-managed isolation and you're on OpenAI Responses.

## 60-second recipe — `SandboxShellTool`

```python
from autogen.beta import Agent
from autogen.beta.config import AnthropicConfig
from autogen.beta.tools import SandboxShellTool

agent = Agent(
    "coder",
    "You write and run Python code.",
    config=AnthropicConfig(model="claude-sonnet-4-6"),
    tools=[SandboxShellTool()],
)

reply = await agent.ask("Write a hello world script and run it.")
print(await reply.content())
```

`SandboxShellTool` is provider-agnostic — swap `AnthropicConfig` for `OpenAIConfig(model="gpt-4.1")`, `GeminiConfig(model="gemini-2.5-pro")`, etc. Make sure you've installed the matching `ag2[<provider>]` extra and set the matching env var (see `ag2-quickstart` → Prerequisites).

With no arguments, `SandboxShellTool` defaults to a `LocalEnvironment()` that creates a temporary working directory (prefixed `ag2_sandbox_`) and cleans it up when the process exits. Pass a `LocalEnvironment` with a path to use a specific directory:

```python
from pathlib import Path
from autogen.beta.tools import LocalEnvironment, SandboxShellTool

SandboxShellTool(LocalEnvironment("/tmp/my_project"))
SandboxShellTool(LocalEnvironment(Path("/tmp/my_project")))
```

When a path is given, the directory is created if it does not exist and is **not** deleted on exit. Inspect the resolved working directory via `tool.workdir`.

## Sandboxing (`LocalEnvironment` + tool-level filters)

For anything beyond a throwaway demo, lock down what the agent can do. The **environment** (`LocalEnvironment`) decides *where* commands run and carries backend config (`path`, `timeout`, `max_output`, `env_vars`); the **tool** (`SandboxShellTool`) decides the agent-facing policy (`allowed` / `blocked` / `ignore` / `readonly`). Filtering is applied in this order on every call:

1. `allowed` — if set, the command must match at least one prefix. In this restricted mode, shell operators (`>`, `>>`, `|`, `;`, `&&`, `||`, `` ` ``, `$(`) are also rejected.
2. `blocked` — if set, the command must not match any prefix. Best-effort only (head-command prefix match; chaining can bypass it).
3. `ignore` — literal path tokens in the command are checked against gitignore-style patterns; matches return `"Access denied: <path>"`.
4. Execute via the environment's `subprocess`.

```python
from autogen.beta.tools import LocalEnvironment, SandboxShellTool

sh = SandboxShellTool(
    LocalEnvironment(
        path="/tmp/my_project",
        timeout=30,
        max_output=50_000,
    ),
    allowed=["python", "uv run", "git"],
    blocked=["rm -rf", "curl", "wget"],
    ignore=["**/.env", "*.key", "secrets/**"],
)
```

### Read-only mode

For inspection-only access (`cat`, `head`, `tail`, `ls`, `grep`, `find`, `git log`, `git diff`, `git status`, …):

```python
from autogen.beta.tools import LocalEnvironment, SandboxShellTool

sh = SandboxShellTool(LocalEnvironment(path="/my/codebase"), readonly=True)
```

Pass an explicit `allowed=[...]` to override the built-in read-only allowlist.

### Parameter reference

`LocalEnvironment` (the environment — where and how commands run):

| Parameter | Default | Description |
|---|---|---|
| `path` | `None` | Working dir. `None` → temp dir (prefix `ag2_sandbox_`), deleted on exit |
| `cleanup` | `None` | `None` → auto (`True` when `path=None`, `False` otherwise). Deletes `path` on close |
| `timeout` | `60` | Per-command timeout in seconds (returns `"Command timed out after Ns"` with exit code 124) |
| `max_output` | `100_000` | Max characters returned (truncated output is suffixed `[truncated: …]`) |
| `env_vars` | `None` | Extra env vars merged into each command |

`SandboxShellTool` (the tool — agent-facing command policy):

| Parameter | Default | Description |
|---|---|---|
| `environment` | `None` | The backend. `None` → `LocalEnvironment()` (local subprocess, temp dir) |
| `allowed` | `None` | Whitelist of command prefixes. `None` → all commands allowed |
| `blocked` | `None` | Blacklist of command prefixes (best-effort, not a security boundary) |
| `ignore` | `None` | Gitignore-style path patterns; matches block the command |
| `readonly` | `False` | When `True` and `allowed` unset, restricts to a built-in read-only list |

## Stateful multi-turn workspaces

Files persist in `workdir` across `ask()` calls, so the agent can build on prior work:

```python
from autogen.beta.tools import LocalEnvironment, SandboxShellTool

sh = SandboxShellTool(LocalEnvironment(path="/tmp/counter_demo"))
agent = Agent("coder", "You manage files.", config=config, tools=[sh])

reply1 = await agent.ask("Create counter.txt with value 0")
reply2 = await reply1.ask("Increment the counter by 1")
reply3 = await reply2.ask("Read the counter and tell me the value")
```

## Provider-native `ShellTool` (OpenAI Responses only)

`ShellTool` is a provider-executed capability flag. **Only the OpenAI Responses API runs shell server-side.** Anthropic's `bash` tool is client-side and is rejected with `UnsupportedToolError` — use `SandboxShellTool` there. Gemini is also unsupported.

```python
from autogen.beta.config import OpenAIResponsesConfig
from autogen.beta.tools import ShellTool

agent = Agent("devops", config=OpenAIResponsesConfig(model="gpt-4.1"), tools=[ShellTool()])
```

OpenAI lets you configure the execution environment:

```python
from autogen.beta.config import OpenAIResponsesConfig
from autogen.beta.tools import ContainerAutoEnvironment, NetworkPolicy, ShellTool

agent = Agent(
    "devops",
    config=OpenAIResponsesConfig(model="gpt-4.1"),
    tools=[
        ShellTool(
            environment=ContainerAutoEnvironment(
                network_policy=NetworkPolicy(allowed_domains=["pypi.org"]),
            ),
        ),
    ],
)
```

Environment options (OpenAI-only):

| Environment | Description |
|---|---|
| `ContainerAutoEnvironment` | Provider-managed container with optional `NetworkPolicy` |
| `ContainerReferenceEnvironment` | Reference an existing container by ID |

## `SandboxShellTool` vs `ShellTool`

| | `SandboxShellTool` | `ShellTool` |
|---|---|---|
| **Execution** | Client-side `subprocess` | Provider-side container |
| **Provider support** | Any provider | OpenAI Responses only |
| **Environment control** | Full (`allowed`, `blocked`, `ignore`, `readonly`, …) | Limited (provider-dependent) |
| **Local FS access** | Yes (you choose what's exposed) | No |
| **Network control** | Via `blocked` / `allowed` patterns | OpenAI: `NetworkPolicy` |
| **Import** | `from autogen.beta.tools import SandboxShellTool, LocalEnvironment` | `from autogen.beta.tools import ShellTool` |

## Going deeper

- `website/docs/beta/tools/sandbox.mdx` — full `SandboxShellTool` / `LocalEnvironment` reference, command-filtering semantics.
- `website/docs/beta/tools/builtin_tools.mdx#shell` — provider-native `ShellTool` setup and environment configs.
- For **human-approval gating before each shell call**, layer `approval_required()` middleware (see `ag2-hitl`).

## Common pitfalls

- **Forgetting sandboxing in production** — `SandboxShellTool()` with no filters runs anything anywhere with a 60s timeout. Set `allowed`, `blocked`, or `readonly` for any non-trivial use.
- **`ignore` only checks literal paths in the command string** — variable substitution, command substitution (`` `cat secrets.key` ``), and dynamic glob expansion are not inspected. Layer in `blocked=["cat", "less"]` if you also want to block readers.
- **`blocked` is best-effort, not a security boundary** — it only matches the head command's prefix, so chaining (`echo x; rm -rf ~`) bypasses `blocked=["rm"]`. Use `allowed` / `readonly` or an isolated container backend for real isolation.
- **Trying to use `ShellTool` on Anthropic or Gemini** — unsupported, will raise `UnsupportedToolError`. Use `SandboxShellTool` instead.
- **Using a hardcoded path that another process is also touching** — multiple agents sharing `/tmp/my_project` will race. Use `tempfile.mkdtemp(prefix="...")` for parallel runs.
- **Expecting `ShellTool` to access local files** — it doesn't; it runs in the provider's container. Use `SandboxShellTool` for anything on your filesystem.
- **Trusting the LLM with shell access** — even sandboxed, write `prompt`s that scope what's allowed and consider pairing with `approval_required()` for destructive operations.
