# Shell / code tools — reference (AG2 1.3)

## Tool comparison

| | `SandboxShellTool` | `AnthropicBashTool` | `ShellTool` | `SandboxCodeTool` |
|---|---|---|---|---|
| Runs | client-side, your backend | client-side, your backend | OpenAI's container | client-side, your backend |
| Providers | all | Anthropic only | OpenAI Responses only (gpt-5.2+) | all |
| Tool the model sees | `run_shell_command(command)` | Anthropic `bash_20250124` | OpenAI hosted shell | `run_code(code, language)` |
| Filters (`allowed`/`blocked`/`ignore`/`readonly`) | yes | yes | no | no |
| Local files | yes | yes | no | yes |
| Import | `ag2.tools` | `ag2.tools` | `ag2.tools` | `ag2.tools` |

Wrong provider → `ag2.exceptions.UnsupportedToolError` on the first `ask`.

## `SandboxShellTool(environment=None, *, allowed=None, blocked=None, ignore=None, readonly=False, name="run_shell_command", description=None, middleware=())`

| Param | Meaning |
|---|---|
| `environment` | backend (`SandboxFactory`). `None` → `LocalEnvironment()` |
| `allowed` | command-prefix whitelist, matched word by word; replaces the read-only set |
| `blocked` | prefix blacklist on argv, program by basename (`/bin/rm` == `rm`); can't stop `sh -c` / `env` / `xargs` |
| `ignore` | gitignore-style patterns; a command naming a matching path → `"Access denied: <path>"` |
| `readonly` | when `allowed` unset: `cat head tail ls grep egrep fgrep wc du df diff stat which pwd echo printenv cut` |
| `description` | `None` → auto text that tells the model whether it has a full shell; `{workdir}` is filled in |
| `middleware` | tool middleware, e.g. `approval_required()` from `ag2.middleware` |

`.workdir` — resolved working directory (host path for local; sandbox path like `/workspace` for containers).

### Filter order (per call)

1. `allowed` (if set) → else `"Command not allowed: '<cmd>'"`
2. restricted mode: unquoted `|`, `>`, `>>`, `<`, `;`, `&`, `&&`, `||`, newline, `` ` ``, `$(` → `"Command not allowed (shell syntax is not available in restricted mode): ..."`
3. `blocked`
4. `ignore` (literal path words; on the local backend a glob word is expanded for the check)
5. execute — `sh -c` when no filter is set; plain argv (no shell) in restricted mode

Restricted mode = any of `allowed` / `blocked` / `ignore` / `readonly`. Verified behaviour: `ls *.py` → `ls: *.py: No such file or directory`; `cat a | wc` → rejected; `git status` under `readonly=True` → not allowed.

## `AnthropicBashTool(environment=None, *, allowed=None, blocked=None, ignore=None, readonly=False)`

Same semantics as `SandboxShellTool`. Each command runs fresh (no persistent bash session); a model `restart` is answered with "nothing to restart". Anthropic also runs bash inside `CodeExecutionTool` — that one is in Anthropic's container, not on your files.

## `ShellTool(environment=...)` — OpenAI hosted shell

```python
from ag2.tools import ContainerAutoEnvironment, ContainerReferenceEnvironment, NetworkPolicy, ShellTool

ShellTool(environment=ContainerAutoEnvironment(network_policy=NetworkPolicy(allowed_domains=["pypi.org"])))
ShellTool(environment=ContainerReferenceEnvironment(container_id="cntr_..."))  # network policy set at container creation
```

- No `environment` → `ClientExecutedShellUnsupportedError` (AG2 can't execute OpenAI's client-side `shell_call`).
- Model must support hosted shell: gpt-5.2+ (gpt-4.1-mini / gpt-5-mini → 400; gpt-5.1 → "Use gpt-5.2 or later").
- `SkillsTool` (provider skills) attaches to the hosted shell's `container_auto`; it cannot be combined with `ContainerReferenceEnvironment`.

## Backends

| Backend | Import | Install | Workdir | Notes |
|---|---|---|---|---|
| `LocalEnvironment(path=None, *, cleanup=None, timeout=60, max_output=100_000, env_vars=None)` | `ag2.tools` | — | `path` or temp `ag2_sandbox_*` | `cleanup=None` → delete only temp dirs; timeout → exit code 124 |
| `DockerEnvironment(*, image="python:3.12-slim", env_vars=None, timeout=60, network_mode="none", mem_limit="512m", cpu_quota=None, user=None, auto_remove=True)` | `ag2.extensions.docker` | `"docker>=6.0.0,<8"` + daemon | `/workspace` | no `ag2[docker]` extra |
| `DaytonaEnvironment(*, api_key=None, api_url=None, target=None, snapshot=None, image=None, env_vars=None, resources=DaytonaResources(...), timeout=60, workdir="/workspace")` | `ag2.extensions.daytona` | `"daytona>=0.171.0,<1"` | `/workspace` | hosted; reads `DAYTONA_API_KEY` / `DAYTONA_API_URL` / `DAYTONA_TARGET` |
| `TenkiEnvironment(*, api_key=None, api_url=None, workspace_id=None, image=None, env_vars=None, resources=TenkiResources(...), timeout=60, max_duration=900, workdir="/home/tenki")` | `ag2.extensions.tenki` | `"tenki>=1.4.0,<2"` | `/home/tenki` | `TENKI_API_KEY`; pass `workspace_id` if the key sees several workspaces |

Use container backends as `async with env:` to scope the sandbox; it is otherwise created lazily on the first call and cleaned up at exit. One env can back both `SandboxShellTool(env)` and `SandboxCodeTool(env)` — they share files. `Variable(...)` markers are accepted for Docker `image`/`env_vars`/`network_mode`, Daytona `api_key`/`api_url`/`target`/`image`/`snapshot`/`env_vars`, Tenki `api_key`/`api_url`/`workspace_id`/`image`/`env_vars`.

## `SandboxCodeTool(environment, *, languages=("python", "bash"), runners=None, name="run_code", ...)`

`environment` is required (any backend above, or a `CodeEnvironment`). The default python runner calls `python -c`; on hosts/images with only `python3` (macOS Homebrew, Tenki) override it:

```python
from ag2.tools import LocalEnvironment, SandboxCodeTool
from ag2.tools.sandbox import LanguageRunner

SandboxCodeTool(LocalEnvironment(), runners={"python": LanguageRunner(inline_argv=("python3", "-c"))})
SandboxCodeTool(tenki_env.code_environment())   # Tenki ships the right runner
```

## Custom backends

Implement `ag2.tools.sandbox.SandboxFactory` (`open(context)` async context manager yielding a `Sandbox`). If its working directory is not `/workspace`, also implement `ag2.tools.sandbox.WorkdirAware` (a `workdir: PurePosixPath` property) so `SandboxShellTool` tells the model the right directory before any sandbox opens.

Upstream: `website/docs/user-guide/tools/{local_shell,code_execution,builtin_tools}.mdx`, `website/docs/user-guide/extensions/tenki.mdx`.
