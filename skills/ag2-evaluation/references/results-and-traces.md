# Results and traces — details (AG2 1.3)

## `RunResult` anatomy

| Member | Type / meaning |
|---|---|
| `run_id`, `label`, `created_at`, `duration_ms`, `schema_version` (`"0.2"`) | metadata |
| `tasks` | `tuple[TaskResult, ...]` — `task` (`Task`: `task_id`, `inputs`, `reference_outputs`, `tags`, `metadata`), `trace` (`Trace`), `feedback` (`tuple[Feedback, ...]`), `budget_violation`, `trace_ref` |
| `aggregates` | `Aggregates(pass_rate, score_stats, value_counts, tokens, errors, budget_violations)` |
| `pass_rate(key, *, tag=None)` | `float` over bool feedback |
| `score_stats(key, *, tag=None)` | `ScoreStats(mean, p50, p95, n)` |
| `value_counts(key, *, tag=None)` | `dict[str, int]` |
| `summary()` | printable table; bool rows show `passed/total` |
| `diff(baseline, *, strict=True)` | `RunDiff` — `regressions`, `flipped_to_fail`, `flipped_to_pass`, `pass_rate_deltas`, `mean_deltas`, `only_in_baseline`, `only_in_current`, `summary()` |
| `save(path=None)` | writes JSON (to `store_dir` when `path` is None) |

`Feedback(key, score, value, comment, detail)`: `score` is bool/int/float, `value` a categorical str, `comment` the human-readable rationale (judges), `detail` structured evidence (e.g. a thresholded judge's raw number).

`Trace`: `events`, `exception`, `duration_ms`, `tokens` (`TokenUsage(input, output, cache_creation, cache_read)`, `.total` = input + output), `events_of(EventType, name=None)` e.g. `trace.events_of(ToolCallEvent, name="get_weather")`.

```python
def pass_count(result, key):
    fbs = [fb for tr in result.tasks for fb in tr.feedback if fb.key == key and isinstance(fb.score, bool)]
    return sum(fb.score for fb in fbs), len(fbs)

failed = [tr.task.task_id for tr in result.tasks
          if any(fb.key == "final_answer_matches" and fb.score is False for fb in tr.feedback)]
crashed = [tr.task.task_id for tr in result.tasks if tr.trace.exception is not None]
```

## Budgets (observational)

```python
from ag2.eval import BudgetThresholds
result = await run_agent(suite, agent=agent, scorers=scorers,
                         budgets=BudgetThresholds(max_tokens_per_task=2_000, max_seconds_per_task=30))
assert result.aggregates.budget_violations == 0
```

Tokens come from `UsageEvent` (schema 0.2): sub-agent, compaction and aggregation spend all count.

## `run_agent` knobs

`run_agent(suite, *, agent, scorers=(), store_dir=None, model_config=None, repeats=1, budgets=None, concurrency=4, run_id=None, label=None, stream=None, variant=None, span_attributes=None, span_processors=None)`. `span_processors` are *added* to the internal in-memory exporter — use them to ship eval spans to your own backend as well.

## Bring your own spans

When your agent runs behind your own entry point, record spans with `TelemetryMiddleware` and convert:

```python
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from ag2.eval import InMemoryTraceSource, TraceRef, evaluate_traces, readable_spans_to_trace
from ag2.middleware import TelemetryMiddleware

exporter = InMemorySpanExporter()
provider = TracerProvider()
provider.add_span_processor(SimpleSpanProcessor(exporter))

await agent.ask("Capital of France?",
                middleware=[TelemetryMiddleware(tracer_provider=provider, agent_name=agent.name)])
trace = readable_spans_to_trace(exporter.get_finished_spans())

result = await evaluate_traces(
    InMemoryTraceSource([(TraceRef(trace_id="run-1", task_id="france"), trace)]),
    scorers=scorers, suite=suite, store_dir="./runs",
    on_missing_task="ignore",       # suite tasks without a trace: "error" (default) | "ignore" | "raise"
)
```

`spans_to_trace(spans: Sequence[SpanData])` does the same from SDK-free `SpanData(name, span_id, parent_id, start_ns, end_ns, attributes, status, events)`. Conventions understood: AG2 GenAI and OpenInference.

## Trace sources

Write a `DirectoryTraceSource` directory yourself (it is *not* the `store_dir` run JSON):

```python
from ag2.eval.sources import readable_span_to_data
from ag2.eval.sources.trace_source import save_trace
save_trace("traces/", "run-1", [readable_span_to_data(s) for s in exporter.get_finished_spans()], task_id="france")
```


`InMemoryTraceSource`, `DirectoryTraceSource(dir)`, `TempoTraceSource(...)` (Grafana Tempo). A trace whose `task_id` isn't in the suite is graded reference-free; a suite task with no trace follows `on_missing_task` (default `"error"` → counted as a failure with `MissingTraceError` on `trace.exception`).
