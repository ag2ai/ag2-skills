# Dependency injection in AG2 tools

Four annotations let a tool pull values from execution context without exposing them to the LLM. They use the same `fast_depends` machinery as FastAPI.

## At a glance

| Annotation | Use for | Resolves from | Lifecycle |
|---|---|---|---|
| `Context` (positional or kw) | Whole-context access (variables, dependencies, stream, prompt, `input()`) | The current `Context` object | Per call |
| `Inject(real_name="", default=..., default_factory=...)` | Pre-built complex objects (DB pool, HTTP session) | `context.dependencies` dict | Per call |
| `Variable(real_name="", default=..., default_factory=...)` | Serializable state (session id, flags, progress, lists) | `context.variables` mapping | Per invocation (mutations persist within the conversation; caller's dict is copied) |
| `Depends(callable, use_cache=True)` | Computed-on-demand dependency, side-execution, yield-based teardown | Calls `callable` at execution time | Per call (cached within the same call by default) |

Resolution annotations do **not** appear in the LLM-facing tool schema.

## `Context` — direct access

```python
from ag2 import Context, tool

@tool
def query(query: str, context: Context) -> str:
    db = context.dependencies["db"]
    session = context.variables.get("session_id")
    return f"{session}: {db.execute(query)}"
```

`Context` exposes `.dependencies`, `.variables`, `.prompt`, `.stream`, and `.input(...)` for HITL.

## `Inject` — typed dependency lookup

```python
from typing import Annotated
from ag2 import Inject, tool

@tool
def fetch(
    url: str,
    http_session: Annotated[object, Inject()],   # looks up "http_session" in deps
    db: Annotated[object, Inject("database")],   # looks up "database" in deps
) -> str:
    ...
```

Provide dependencies on the agent (broad) or per-ask (narrow). Per-ask wins on collision:

```python
agent = Agent("data", dependencies={"db": prod_db, "http_session": shared_session})
await agent.ask("Query the user table", dependencies={"db": readonly_db})
```

Defaults if missing:

```python
client: Annotated[object | None, Inject(default=None)]
client: Annotated[object, Inject(default_factory=DefaultClient)]
```

## `Variable` — serializable conversation state

```python
from typing import Annotated
from ag2 import Variable, tool

@tool
def fetch_user(
    user_id: str,
    session_id: Annotated[str, Variable()],
    theme: Annotated[str, Variable(default="dark")],
) -> str:
    ...
```

Keep secrets and runtime objects in `dependencies`, not variables: variables are deep-copied and synced to peers over A2A / AG-UI / NLIP (keys prefixed `ag:` / `a2a:` are reserved and stripped).

Provide variables on the agent or per-ask (per-ask wins on key collision). **Variables are mutable inside tools** and persist across tool calls in the same conversation:

```python
@tool
def authenticate(context: Context) -> str:
    context.variables["auth_token"] = "abc-123"
    return "Authenticated"

@tool
def fetch_secure(auth_token: Annotated[str | None, Variable(default=None)]) -> str:
    if not auth_token:
        return "Not authenticated"
    return f"Data with token {auth_token}"
```

### Copy semantics — read results from the reply

A fresh `agent.ask(...)` / `agent.run(...)` **deep-copies** the merged `Agent(variables=...)` defaults and the per-call `variables=` mapping. Tool mutations therefore never reach the caller's dict or the agent defaults, and two independent `ask()` calls never share mutable state. `reply.ask(...)` continues with the same (already mutated) variables.

```python
inputs = {"cart": []}
reply = await agent.ask("Add milk to my cart", variables=inputs)
reply.context.variables["cart"]   # ["milk"]   <- the invocation's state
inputs["cart"]                    # []         <- untouched
reply = await reply.ask("Add eggs too")   # continues with ["milk"]
```

Persist state between independent requests yourself (e.g. save `dict(reply.context.variables)` and pass it back as `variables=` next time).

### Scopes across subagents (`Scope`, `ScopedValue`, `ScopeError`)

When a tool delegates to a worker (`worker.as_tool()`, see `ag2-subagent-delegation`), each variable crosses according to its scope. Import `Scope`, `ScopedValue`, `ScopeError` from `ag2`.

| Scope | Child receives | Visibility of changes |
|---|---|---|
| `Scope.COPIED` (default for plain values) | deep copy | that branch only |
| `Scope.LOCAL` | nothing | owning branch only |
| `Scope.SHARED` | the same binding | whole delegation tree of this invocation |

```python
from ag2 import Scope, ScopedValue

reply = await coordinator.ask(
    "Research X",
    variables={
        "findings": ScopedValue([], scope=Scope.SHARED),       # workers append here
        "draft": [],                                           # each worker gets a copy
        "root_note": ScopedValue("private", scope=Scope.LOCAL),  # invisible to workers
    },
)
reply.context.variables["findings"]   # includes workers' contributions
```

Rules: shared names must be declared at the **root** (agent defaults or the root `variables=`). A worker assigning `ScopedValue(..., scope=Scope.SHARED)` for a new name, or redeclaring a scope, raises `ScopeError` (surfaced to the worker's model as a tool error). For open-ended contributions declare a shared dict at the root and add keys inside it. Sharing gives visibility, not locking — pass an `asyncio.Lock` via `dependencies` if workers race. Tools and `Variable()` receive the plain value, never the `ScopedValue` wrapper.

## `Depends` — computed dependencies

For something that must be evaluated at execution time (auth checks, short-lived sessions, side effects):

```python
from typing import Annotated
from ag2 import Depends, tool

def verify_permissions(user_id: int) -> None:
    if not _allowed(user_id):
        raise PermissionDenied(user_id)

@tool
def delete_user(
    user_id: int,
    auth: Annotated[None, Depends(verify_permissions)],
) -> str:
    return f"User {user_id} deleted."
```

`verify_permissions` runs before the tool body. Its return value is injected as `auth` (here ignored — the dependency is purely for its side effect / raise).

### Yield-based teardown

```python
def get_db_session():
    print("opening session")
    session = "db_session_object"
    yield session
    print("closing session")  # runs after the tool finishes

@tool
def fetch_records(db: Annotated[str, Depends(get_db_session)]) -> str:
    return "Records fetched."
```

### Combining `Depends` and `Inject`

`Inject` for the long-lived pool, `Depends` for the short-lived per-call resource:

```python
def get_session(pool: Annotated[Pool, Inject("database_pool")]) -> Session:
    session = pool.acquire()
    yield session
    session.release()

@tool
def fetch(session: Annotated[Session, Depends(get_session)]) -> str:
    ...

agent = Agent("data", tools=[fetch], dependencies={"database_pool": Pool()})
```

### Caching

If multiple parameters declare the same `Depends(fn)`, `fn` is called once and cached for the rest of that tool call. Pass `use_cache=False` to force re-evaluation each time.

### Test overrides

```python
def get_production_db():
    raise Exception("Do not call in tests!")

@tool
def read_data(db: Annotated[object, Depends(get_production_db)]) -> str:
    return "Data"

agent = Agent("test", tools=[read_data])
agent.dependency_provider.override(get_production_db, lambda: "mock_db")
```

For `Inject` overrides, just pass `dependencies={...}` to `agent.ask(...)`.

## When to pick which

- Need the whole context object? → `Context`.
- Pre-built object, used as-is? → `Inject`.
- Plain scalar / config value? → `Variable`.
- Computed at call time, possibly with cleanup? → `Depends`.
