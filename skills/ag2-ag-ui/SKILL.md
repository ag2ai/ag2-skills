---
name: ag2-ag-ui
description: "Expose an AG2 `Agent` over the AG-UI 1.0 protocol (`ag-ui-protocol>=1.0`) so a web frontend (CopilotKit, `@ag-ui/client` HttpAgent, custom React/Next.js) or a CopilotKit Channel (Slack etc.) can stream text, render backend and frontend tool calls, sync shared state, see delegations, and answer human-in-the-loop interrupts. Covers `AGUIStream(agent)` with `stream.dispatch(...)` in FastAPI or `stream.build_asgi()` as a Starlette route, `read_run_input` (tolerant body parsing), `stream.capabilities()`, the `RunAgentInput` body (`threadId`/`runId`/`messages`/`state`/`context`/`tools`/`resume`), emitted events (`TEXT_MESSAGE_CHUNK`, `TEXT_MESSAGE_START/CONTENT/END`, `TOOL_CALL_*`, `STATE_SNAPSHOT`, `SUBAGENT_*`, `RUN_FINISHED` outcomes), interrupts/`resume`, `Retention`, `require_resume_proof`. For A2A interop see `ag2-a2a`; MCP `ag2-mcp`; IDE agents `ag2-acp`."
license: Apache-2.0
metadata:
  ag2_version: "1.3.0"
---

# AG-UI integration (AG-UI 1.0)

`ag2.ag_ui.AGUIStream` turns an `Agent` into an AG-UI Server-Sent-Events endpoint. Use it when a browser UI (or a CopilotKit Channel) should talk to an AG2 agent with a standard contract; for a narrow app-specific API, a plain endpoint is simpler.

```bash
pip install "ag2[ag-ui,openai]" fastapi uvicorn
```

## When to use

- A web UI (React / Next.js / anything HTTP + SSE) should talk to an AG2 agent.
- You want streamed text, reasoning, tool-call rendering, shared state or sub-task steps surfaced to the frontend through a standard protocol.
- You use or consider CopilotKit as the React client.

For a narrow app-specific API whose contract you own end to end, write a plain endpoint instead.

## Server (FastAPI, manual dispatch — recommended)

```python title="run_ag_ui.py"
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ag2 import Agent
from ag2.ag_ui import AGUIStream, read_run_input
from ag2.config import OpenAIResponsesConfig

agent = Agent(
    name="support_bot",
    prompt="You help users with billing questions.",
    config=OpenAIResponsesConfig(model="gpt-4.1-mini"),
)
stream = AGUIStream(agent)
app = FastAPI()

@app.get("/chat")
@app.get("/chat/capabilities")
async def capabilities() -> JSONResponse:
    return JSONResponse(stream.capabilities().model_dump(by_alias=True, exclude_none=True))

@app.post("/chat")
async def run_agent(request: Request) -> Response:
    try:
        run_input = read_run_input(await request.body())   # strips unknown roles/parts from newer clients
    except ValueError:
        return JSONResponse({"error": "invalid AG-UI RunAgentInput body"}, status_code=400)
    return StreamingResponse(
        stream.dispatch(run_input, accept=request.headers.get("accept")),
        media_type="text/event-stream",                    # always SSE; don't echo Accept
    )
```

Do **not** declare `run_input: RunAgentInput` as a FastAPI parameter — a newer client's unknown role/part gets a 422; `read_run_input` serves it. `dispatch(...)` also takes `variables=`, `dependencies=` (server-only secrets, e.g. the verified user), `prompt=`, `tools=`, `config=`, `middleware=`, `hitl_hook=`, `context_prompt=`.

No custom logic? Add the generated endpoint as a **route** (not `mount`, which 307-redirects `/chat` → `/chat/`):

```python
from starlette.routing import Route
endpoint = stream.build_asgi()
app.routes.append(Route("/chat", endpoint))
app.routes.append(Route("/chat/capabilities", endpoint))
```

## Request body (`RunAgentInput`, camelCase JSON)

```bash
curl -N -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" -H "Accept: text/event-stream" -d '{
  "threadId": "thread-1", "runId": "run-1", "protocolVersion": "1.0",
  "messages": [{"id": "m1", "role": "user", "content": "Hello"}],
  "state": {}, "context": [], "tools": [], "forwardedProps": {}
}'
```

- New `runId` per POST; same `threadId` across a turn (and for resumes). Send the full message history each run.
- `state` (JSON object) seeds `context.variables`; `context: [{"description", "value"}]` is appended to the system prompt as prose (not variables).
- `tools: [{"name", "description", "parameters": <JSON schema>}]` are **frontend tools**.
- Omit `protocolVersion` → treated as pre-1.0 (tool results downgraded to strings); major `2.x` → lone `RUN_ERROR` `UNSUPPORTED_PROTOCOL_VERSION`.

## Events actually emitted (verified, AG2 1.3.0)

Streaming is optional — `AGUIStream` works with either. For a chat UI, set `streaming=True` on the model config (e.g. `OpenAIResponsesConfig(model=..., streaming=True)`) so text renders token by token; without it the whole reply arrives as one chunk.

| Situation | Event sequence |
|---|---|
| Plain reply, non-streaming model | `RUN_STARTED → TEXT_MESSAGE_CHUNK → RUN_FINISHED` |
| Model config `streaming=True` | `RUN_STARTED → TEXT_MESSAGE_START → TEXT_MESSAGE_CONTENT×n → TEXT_MESSAGE_END → RUN_FINISHED` |
| Backend tool | `TOOL_CALL_START → TOOL_CALL_ARGS → TOOL_CALL_END → TOOL_CALL_RESULT` then text |
| Frontend tool (from `tools`) | `RUN_STARTED → TOOL_CALL_CHUNK → RUN_FINISHED` with `outcome: {"type":"success","pendingToolCallIds":[...]}`; client runs it and posts a new run with the assistant `toolCalls` + `role:"tool"` message |
| Reasoning | `REASONING_START`, `REASONING_MESSAGE_START/CONTENT/END`, `REASONING_END` |
| State changed | `STATE_SNAPSHOT` (client state + server variables) at start/end when different |
| Delegation (`agent.as_tool`) | `SUBAGENT_STARTED` / `SUBAGENT_FINISHED` / `SUBAGENT_ERROR` (replaces old `STEP_*`) |
| Failure | `RUN_ERROR` (logged on `ag2.ag_ui`; `dispatch` does not raise) |

Text deltas are in `event["delta"]`. `RUN_FINISHED`/`RUN_ERROR` carry `usage` per provider/model.

## Human-in-the-loop interrupts

A tool calling `await context.input("Book LH441? (yes/no)")` on an agent **without** `hitl_hook` ends the run with `RUN_FINISHED` + `outcome: {"type": "interrupt", "interrupts": [{"id", "reason": "input_required", "message", "responseSchema", "expiresAt", "metadata"}]}`; the coroutine stays suspended in-process. Answer with a new run on the same thread:

```json
{"threadId": "thread-1", "runId": "run-2", "messages": [], "state": {}, "context": [], "tools": [],
 "resume": [{"interruptId": "<id>", "status": "resolved", "payload": "yes", "metadata": <interrupt.metadata>}]}
```

→ `RUN_STARTED → TOOL_CALL_RESULT → TEXT_MESSAGE_CHUNK → RUN_FINISHED`. `"status": "cancelled"` stops the turn. Tools wrapped in `approval_required()` interrupt with `reason: "tool_call"` + `toolCallId`; answer `payload: true/false`. With a `hitl_hook` (agent or `dispatch`) no interrupt is emitted.

Ops: held turns live in one process → **route by `threadId` (sticky)**; restarts drop them. Tune `AGUIStream(agent, retention=Retention(ttl=900.0, max_held=128), require_resume_proof=False)`. Call `await stream.aclose()` on shutdown (or `async with stream:`). Refusals: `RUN_ERROR` codes `INTERRUPT_NOT_COVERED` / `INTERRUPT_NOT_PROVEN` / `INTERRUPT_PAYLOAD_REFUSED`.

## Frontends

- **CopilotKit** (React/Next.js): `npx copilotkit@latest create -f ag2` or the `ag2ai/ag2-copilotkit-starter`; bridge with `new HttpAgent({ url: "http://localhost:8000/chat" })` from `@ag-ui/client` 1.x in a `CopilotRuntime`. See `references/copilotkit.md`.
- **Channels** (Slack etc.): a Node 22 process with `@copilotkit/channels` calls the same `/chat` endpoint per message (`website/docs/user-guide/ag-ui/channels.mdx`).
- **Dojo** for protocol testing: https://dojo.ag-ui.com/ag2/feature/agentic_chat

## Pitfalls

- Expecting `TEXT_MESSAGE_START/CONTENT/END` from a non-streaming model — you get one `TEXT_MESSAGE_CHUNK`. Handle both.
- Secrets in `variables` or the agent's variables are sent to the browser in `STATE_SNAPSHOT` — use `dispatch(dependencies=...)`.
- A fresh run (no `resume`) on a thread with a held interrupt is refused (`INTERRUPT_NOT_COVERED`).
- `@ag-ui/client` / CopilotKit `useInterrupt` don't echo interrupt `metadata`; only matters with `require_resume_proof=True`.
- Proxies must not buffer `text/event-stream`; add CORS for the frontend origin; authenticate both GET and POST routes.
- Frontend tool names in the UI must exactly match the names the model is given.

## Reference

- `scripts/serve_and_probe.py` — FastAPI + uvicorn in-process; prints real event sequences for plain run, backend tool, interrupt + resume (needs `OPENAI_API_KEY`).
- `references/copilotkit.md` — CopilotKit route/layout snippets, auth/production checklist.
- Upstream: `website/docs/user-guide/ag-ui/{overview,backend-deepdive,copilotkit-quickstart,channels}.mdx`; `examples/ag_ui/human_input.py`; source `ag2/ag_ui/`.
