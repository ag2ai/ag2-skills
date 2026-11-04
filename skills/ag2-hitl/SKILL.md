---
name: ag2-hitl
description: "Pause an AG2 `Agent` mid-run for human input via `context.input()` + `hitl_hook`, or gate a tool call with `approval_required()` (`ApprovalRequired`) tool middleware. The hook receives a `HumanInputRequest`; approvals arrive as `ToolApprovalRequest` (1.3.0) carrying `tool_call_id`, so a programmatic approver can look up the tool name and arguments. Covers timeouts and `HumanInputError` (`HumanInputNotProvidedError`, `HumanInputFailedError`, `HumanInputTimeoutError`), 'always' approvals, and policy-only gates without a human. Use when the agent must ask for confirmation, request missing info, or have a human/policy approve sensitive, irreversible or expensive tool calls. See `ag2-middleware` for `on_human_input`, `ag2-subagent-delegation` for HITL in workers."
license: Apache-2.0
metadata:
  ag2_version: "1.3.0"
---

# Human-in-the-loop

## When to use

| Need | Use |
|---|---|
| A tool asks an open question and waits for a typed answer | `await context.input(...)` in the tool + `hitl_hook` on the agent |
| Approve / deny a specific tool call before its body runs | `approval_required()` tool middleware + `hitl_hook` |
| Automatic allow/deny by rule, no human at all | a plain tool middleware function (Pattern 3) |

## Pattern 1 — `context.input()` for open questions

```python
from ag2 import Agent, Context, tool
from ag2.config import OpenAIResponsesConfig
from ag2.events import HumanInputRequest, HumanMessage

@tool
async def execute_query(query: str, context: Context) -> str:
    """Run a SQL query after the user confirms."""
    answer = await context.input(f"Run this query? (yes/no)\n{query}", timeout=60.0)
    if answer.strip().lower() != "yes":
        return "Query cancelled."
    return "Query executed successfully."

def hitl_hook(event: HumanInputRequest) -> HumanMessage:
    return HumanMessage(content=input(f"{event.content}\n> "))

agent = Agent("dba", config=OpenAIResponsesConfig(model="gpt-4.1-mini"),
              tools=[execute_query], hitl_hook=hitl_hook)
```

- The hook gets `HumanInputRequest` (`.content`, `.id`, `.timeout`, `.task_id` when a worker asks) and returns `HumanMessage` or `str`. Sync or async. Supports DI (`Context`, `Variable`, `Inject`, `Depends`).
- Or register with `@agent.hitl_hook` (overrides a constructor hook with a `RuntimeWarning` — pick one place).
- `timeout=` defaults to **None** (wait forever) and covers the hook call itself.

## Pattern 2 — `approval_required()` with a programmatic approver

```python
from ag2 import Agent, Context, tool
from ag2.config import OpenAIResponsesConfig
from ag2.events import HumanInputRequest, ToolApprovalRequest, ToolCallEvent
from ag2.middleware import approval_required

@tool(middleware=[approval_required(timeout=120)])
def delete_file(path: str) -> str:
    """Delete a file."""
    return f"deleted {path}"

async def approver(event: HumanInputRequest, context: Context) -> str:
    if isinstance(event, ToolApprovalRequest):            # 1.3.0: names the gated call
        call = await context.stream.history.where(ToolCallEvent.id == event.tool_call_id).first()
        if call and call.serialized_arguments.get("path", "").startswith("/tmp/"):
            return "y"                                     # y / yes / 1 approve; "always" too
        return "n"                                         # anything else denies
    return "n"                                             # ordinary context.input() questions: answer from your UI/queue, not blocking input()

agent = Agent("ops", config=OpenAIResponsesConfig(model="gpt-4.1-mini"),
              tools=[delete_file], hitl_hook=approver)
```

- `approval_required(message=None, denied_message="User denied the tool call request", *, timeout=None, allow_always=True)` returns an `ApprovalRequired` instance (`.describe()` reports its settings).
- `ToolApprovalRequest` is a `HumanInputRequest` subclass with `tool_call_id`; `call.name` / `call.serialized_arguments` (dict) / `call.arguments` (JSON str) come from the matching `ToolCallEvent`. `event.content` is the human-readable prompt (`{tool_name}`, `{tool_arguments}` interpolated).
- On denial the model receives `denied_message` and continues; the tool body never runs.
- `"always"` approves this and later calls of the **same tool implementation** in this conversation (stored under a reserved `ag:` variable; not inherited by sub-agents, stripped over A2A/AG-UI/etc.).
- Over AG-UI with no hook, the approval becomes a client interrupt instead (see `ag2-ag-ui`).

## Pattern 3 — policy gate, no human

If no person is involved, skip HITL and write a tool middleware; it sees the call directly:

```python
from ag2.events import ToolResultEvent

async def only_tmp(call_next, event, context):
    if not event.serialized_arguments.get("path", "").startswith("/tmp/"):
        return ToolResultEvent.from_call(event, result="Denied by policy: only /tmp/ may be deleted.")
    return await call_next(event, context)

@tool(middleware=[only_tmp])
def delete_file(path: str) -> str: ...
```

## When nobody can answer

Every way `context.input()` fails **ends the turn** (it is not recorded as a tool error the model could route around):

| What happened | Raised from `agent.ask()` |
|---|---|
| No `hitl_hook` registered | `HumanInputNotProvidedError` |
| The hook raised | `HumanInputFailedError` (original on `.cause`) |
| No answer within `timeout=` | `HumanInputTimeoutError` |

All subclass `HumanInputError` (`ag2.exceptions`). Catch it in the tool to carry on, or have the hook return `"n"` as a policy. The gated tool never runs in any of these cases, and the stream stays reusable for the next `ask()`.

## Going deeper

- Upstream: `website/docs/user-guide/context/human_in_the_loop.mdx`, `website/docs/user-guide/tools/approval_required.mdx`, `website/docs/user-guide/tools/tool_middleware.mdx`.
- Agent-wide interception of every request: `BaseMiddleware.on_human_input()` (`ag2-middleware`).
- Tool middleware syntax / DI: `ag2-add-custom-tool`.

## Common pitfalls

- **`approval_required()` without a `hitl_hook`** — `HumanInputNotProvidedError` ends the turn (except when served over AG-UI).
- **Parsing `event.content` to find the tool** — use `ToolApprovalRequest.tool_call_id` + history lookup (above).
- **Only branching on "yes"** in `context.input()` — validate the answer; anything unexpected should deny.
- **Blocking `input()` in an async app** — use an async hook backed by a queue/websocket.
- **Expecting the old 30 s default timeout** — default is now `None`; set `timeout=` for production.
- **Sync tool side effects** — a sync tool already running in a thread cannot be cancelled when the turn ends; ask for approval before the side effect.
