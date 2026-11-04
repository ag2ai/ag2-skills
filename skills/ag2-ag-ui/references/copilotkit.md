# CopilotKit + AG2 over AG-UI 1.0 (frontend wiring)

Not executed here (needs Node/Next.js); copied from `website/docs/user-guide/ag-ui/copilotkit-quickstart.mdx` (AG2 1.3.0). Versions there: `@copilotkit/react-core` / `@copilotkit/runtime` 1.76.0, `@ag-ui/client` 1.0.1, Node ≥ 20.9. CopilotKit APIs change across majors — check https://docs.copilotkit.ai/ag2 for the version you install.

Starting points: the AG2 weather sample `https://github.com/ag2ai/ag2-samples` (backend `uv run python -m backend`, UI `pnpm dev`; shows delegations, `ApprovalRequired` interrupts, shared state, frontend tools), or `npx copilotkit@latest init` and pick AG2.

## Runtime route (Next.js App Router)

```typescript title="app/api/copilotkit/route.ts"
import { HttpAgent } from "@ag-ui/client";
import { NextRequest } from "next/server";
import { CopilotRuntime, createCopilotRuntimeHandler } from "@copilotkit/runtime/v2";

export const POST = async (req: NextRequest) => {
  const runtime = new CopilotRuntime({
    agents: {
      supportAgent: new HttpAgent({
        url: "http://localhost:8000/chat",                                   // your AGUIStream endpoint
        headers: { Authorization: req.headers.get("authorization") ?? "" }, // forward caller auth
      }),
    },
  });
  return createCopilotRuntimeHandler({ runtime, basePath: "/api/copilotkit", mode: "single-route" })(req);
};
```

## Provider

```tsx
import { CopilotKit } from "@copilotkit/react-core/v2";

<CopilotKit agent="supportAgent" runtimeUrl="/api/copilotkit">{children}</CopilotKit>
```

Hooks from `@copilotkit/react-core/v2`: `useAgent` (`agent.state` ← `STATE_SNAPSHOT`), `useInterrupt` (approve/reject `reason: "tool_call"` interrupts with `resolve(true|false)`; sends no `metadata`, so incompatible with `require_resume_proof=True`), `useFrontendTool` (frontend tools → `TOOL_CALL_CHUNK`), `useRenderTool` (render backend tool calls). Tool names must match exactly.

## Backend auth pattern

```python
@app.post("/chat")
async def run_agent(request: Request, authorization: str | None = Header(None)) -> Response:
    user = verify_token(authorization)                       # your check; 401 on failure
    run_input = read_run_input(await request.body())
    return StreamingResponse(
        stream.dispatch(run_input, dependencies={"user": user}),  # never sent to the browser
        media_type="text/event-stream",
    )
```

Protect the capabilities GET route too.

## Production checklist

- CORS for the frontend origin if the browser calls AG2 directly (POST, OPTIONS, auth headers).
- Disable SSE buffering on proxies (Nginx `X-Accel-Buffering: no`, CDN settings).
- Sticky routing by `threadId` if any tool uses `context.input()` / `approval_required()` without a `hitl_hook` (held turns are in-process).
- `await stream.aclose()` on shutdown.
- Treat tool inputs as untrusted; authorize server-side.
- Watch the `ag2.ag_ui` logger — run failures become `RUN_ERROR` and are not raised from `dispatch`.
