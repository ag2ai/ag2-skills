---
name: ag2-telemetry
description: "Add OpenTelemetry traces to an AG2 `Agent` with `TelemetryMiddleware` (`ag2.middleware`, extra `ag2[tracing]`). Emits GenAI-semconv spans — `invoke_agent` (turn), `chat` (LLM call), `execute_tool`, `await_human_input`, plus `record_usage` spans for every `UsageEvent` (sub-agents, compaction) — with `capture_content`, `max_tool_result_chars`, `span_attributes` (tenant/env tags). Sub-agent turns nest under the parent's `execute_tool task_<worker>` span. Works with any OTLP backend (Jaeger, Tempo, Datadog, Honeycomb, Langfuse, Arize). Use for production traces, latency analysis, token attribution. For Prometheus metrics use `MetricsMiddleware` (`ag2-middleware`); for hub/network spans see `HubTelemetryListener` (`ag2-network-quickstart`)."
license: Apache-2.0
---

# Telemetry — OpenTelemetry instrumentation

## When to use

- Per-turn / per-call latency breakdowns, token attribution, end-to-end debugging with spans.
- Shipping traces to Jaeger, Grafana Tempo, Datadog, Honeycomb, Langfuse, Arize, etc.

Quick stdout debugging → `LoggingMiddleware`; dashboards/alerting on rates → `MetricsMiddleware` (both in `ag2-middleware`).

## Installation

```bash
pip install "ag2[openai,tracing]"          # + opentelemetry-exporter-otlp for OTLP export
```

> Required. Run this install before delivering the code. If you cannot run commands, state the exact `pip install` command.

## Recipe

```python
import asyncio

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

from ag2 import Agent
from ag2.config import OpenAIResponsesConfig
from ag2.middleware import TelemetryMiddleware

provider = TracerProvider(resource=Resource.create({"service.name": "ag2-app"}))
provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
trace.set_tracer_provider(provider)

agent = Agent(
    "assistant",
    prompt="You are a helpful assistant.",
    config=OpenAIResponsesConfig(model="gpt-4.1-mini"),
    middleware=[TelemetryMiddleware(tracer_provider=provider, agent_name="assistant")],
)

asyncio.run(agent.ask("What is the capital of France?"))
```

Production: `BatchSpanProcessor(OTLPSpanExporter(endpoint="http://collector:4318/v1/traces"))` (from `opentelemetry.exporter.otlp.proto.http.trace_exporter`).

## Span tree

```
invoke_agent assistant                         ag2.span.type=agent
  ├── chat <model>                             llm
  ├── record_usage model_call                  usage   (one per UsageEvent)
  ├── execute_tool task_worker                 tool    (a delegate call)
  │     └── invoke_agent worker                agent   (if the worker has its own TelemetryMiddleware)
  │           ├── chat <model> / execute_tool get_weather / record_usage ...
  ├── chat <model>
  └── await_human_input assistant              human_input
```

- Add `TelemetryMiddleware` to each worker you want detailed spans for; the parent still records the workers' `UsageEvent`s as `record_usage` spans even if they are uninstrumented.
- **Token totals:** `chat` spans and `record_usage` spans both carry `gen_ai.usage.*`, and an instrumented worker's usage appears under both the worker and the parent. When aggregating, sum only `record_usage` spans and dedupe by `ag2.usage.event_id`.
- A turn triggered by a network envelope links to the hub's span (see network telemetry upstream).

## Key attributes

| Attribute | Spans |
|---|---|
| `ag2.span.type` | all: `agent` / `llm` / `tool` / `human_input` / `usage` |
| `gen_ai.operation.name`, `gen_ai.agent.name` | all / agent, human_input |
| `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.response.finish_reasons` | agent, llm |
| `gen_ai.usage.input_tokens` / `output_tokens` / `cache_creation_input_tokens` / `cache_read_input_tokens` / `thinking_tokens` | llm, usage |
| `ag2.usage.event_id`, `ag2.usage.kind` (`model_call`, `compaction`, `aggregation`, ...), `ag2.usage.total_tokens` | usage |
| `gen_ai.tool.name`, `gen_ai.tool.call.id`, `gen_ai.tool.type` | tool |

With `capture_content=True` (default) also: `gen_ai.input.messages` (full assembled history, OpenAI-style JSON), `gen_ai.output.messages` (text and/or tool calls), `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result` (+ `ag2.tool.call.result.truncated`), `ag2.human_input.prompt` / `.response`.

## Constructor

| Parameter | Default | Notes |
|---|---|---|
| `tracer_provider` | global provider | |
| `capture_content` | `True` | set `False` for PII / external backends |
| `max_tool_result_chars` | `8192` | longer results end with `...[truncated]`; `None` disables |
| `agent_name` | `"unknown"` | set it — it names the spans |
| `provider_name`, `model_name` | auto-detected | |
| `span_attributes` | `None` | `{"deployment": "prod", "tenant": "acme"}` stamped on every span; intrinsic keys win on collision |

`describe()` reports only the *keys* of `span_attributes`.

## Anthropic SDK spans

The `anthropic` SDK adds its own `anthropic.messages.create` span under AG2's `chat` span when a global provider is set. Disable with `AnthropicConfig(model="claude-haiku-4-5", open_telemetry=False)`.

## Going deeper

- Upstream: `website/docs/user-guide/telemetry/agent.mdx`, `telemetry/network.mdx` (`Hub.open(store, tracer_provider=...)` + `HubTelemetryListener`), `metrics.mdx`.
- Eval runs can reconstruct traces from these spans — see `ag2-evaluation`.

## Common pitfalls

- **Missing extra** — without `ag2[tracing]`, using `TelemetryMiddleware` raises a missing-dependency error.
- **`SimpleSpanProcessor` + console in production** — synchronous; use `BatchSpanProcessor` + a real exporter.
- **Leaking content** — `capture_content=True` is the default; history, tool args and human input go to the backend.
- **Double-counting tokens** — see "Token totals" above.
- **Passing the class** — `TelemetryMiddleware` is a factory: pass an instance (`TelemetryMiddleware(...)`).
- **One `TelemetryMiddleware` shared across agents with one `agent_name`** — spans all get that name; create one per agent.
