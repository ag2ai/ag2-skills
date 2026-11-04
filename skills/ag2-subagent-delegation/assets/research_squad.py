"""Research squad — explicit workers, parallel delegation, and an observed delegation tree.

A coordinator delegates to two explicit worker Agents through ``Agent.as_tool()``:

* ``researcher`` — has a ``lookup`` tool and appends to a SHARED notebook.
* ``math_expert`` — arithmetic only.

What the script demonstrates (AG2 1.3.0):

1. **Parallel fan-out** — the coordinator's model requests both delegate calls in one
   response; AG2 runs them concurrently.
2. **Shared concurrency cap** — one *function* tool middleware (an ``asyncio.Semaphore``
   closure) passed to every delegate, so they share a single cap.
3. **Observing the tree** — ``TaskStarted`` / ``TaskCompleted`` reach the parent as
   themselves (with ``agent_name`` and ``path``); everything else a worker does arrives
   as ``ChildEvent`` (inspect ``event.source_event``).
4. **Scoped variables** — ``ScopedValue([], scope=Scope.SHARED)`` is visible and mutable
   across the tree; plain values are deep-copied per worker. Read results from
   ``reply.context.variables``.
5. **Tree-wide usage** — ``await reply.usage()`` includes every worker's tokens.

Requires ``pip install "ag2[openai]"`` and ``OPENAI_API_KEY``.

Run::

    python research_squad.py
"""

import asyncio
import time

from ag2 import Agent, Context, Scope, ScopedValue, observer, tool
from ag2.config import OpenAIResponsesConfig
from ag2.events import BaseEvent, ChildEvent, TaskCompleted, TaskFailed, TaskStarted, ToolCallEvent
from ag2.middleware import ToolMiddleware

FACTS = {
    "eiffel tower": "The Eiffel Tower was completed in 1889 and is about 330 m tall.",
    "nitrogen": "Liquid nitrogen boils at -195.8 °C.",
}


@tool
def lookup(topic: str, context: Context) -> str:
    """Look up a short fact about a topic and record it in the shared notebook."""
    fact = next((f for key, f in FACTS.items() if key in topic.lower()), f"No entry for {topic!r}.")
    context.variables["notebook"].append(fact)  # SHARED binding: the root sees this
    return fact


def concurrency_limit(limit: int) -> ToolMiddleware:
    """One semaphore shared by every delegate it is passed to.

    Must be a function (closure), not a callable object: tool middleware objects are
    deep-copied per tool, which would give each delegate its own semaphore.
    """
    slots = asyncio.Semaphore(limit)

    async def limited(call_next, event, context):
        async with slots:
            return await call_next(event, context)

    return limited


# -- observers on the ROOT agent see the whole delegation tree -----------------
workers_called: list[str] = []
worker_tool_calls: list[str] = []


@observer(TaskStarted)
def on_task_started(event: TaskStarted) -> None:
    # Delegations arrive with a non-empty path; a local (root) lifecycle has path == ().
    if event.path:
        workers_called.append(event.agent_name)
        print(f"  -> {event.agent_name} started: {event.objective[:60]!r}")


@observer((TaskCompleted, TaskFailed))
def on_task_finished(event: BaseEvent) -> None:
    if event.path:
        status = "done" if isinstance(event, TaskCompleted) else f"FAILED: {event.error}"
        print(f"  <- {event.agent_name} {status}")


@observer(ChildEvent)
def on_child_event(event: ChildEvent) -> None:
    source = event.source_event
    if isinstance(source, ToolCallEvent):
        worker_tool_calls.append(f"{event.agent_name}.{source.name}({source.arguments})")


async def main() -> None:
    config = OpenAIResponsesConfig(model="gpt-4.1-mini")

    researcher = Agent(
        "researcher",
        prompt="Use the lookup tool for every topic you are given, then report the facts concisely.",
        config=config,  # workers do NOT inherit the parent's config; pass it explicitly
        tools=[lookup],
    )
    math_expert = Agent(
        "math_expert",
        prompt="You are an arithmetic specialist. Reply with only the number.",
        config=config,
    )

    cap = concurrency_limit(2)
    coordinator = Agent(
        "coordinator",
        prompt=(
            "You coordinate specialists. When a question has independent parts, call the "
            "delegate tools for ALL parts in a single response so they run in parallel, "
            "then combine their answers concisely."
        ),
        config=config,
        tools=[
            researcher.as_tool(description="Research factual topics. Put topics in objective.", middleware=[cap]),
            math_expert.as_tool(description="Solve an arithmetic problem.", middleware=[cap]),
        ],
        observers=[on_task_started, on_task_finished, on_child_event],
    )

    start = time.monotonic()
    reply = await coordinator.ask(
        "Answer both parts: (a) when was the Eiffel Tower completed and what is the boiling "
        "point of nitrogen? (b) what is 237 * 19?",
        variables={
            "notebook": ScopedValue([], scope=Scope.SHARED),  # shared across the tree
            "draft": [],  # plain value: each worker gets a deep copy
        },
    )
    elapsed = time.monotonic() - start

    print("\nFINAL:", reply.body)
    print("WORKERS_CALLED:", ", ".join(sorted(set(workers_called))))
    print("WORKER_TOOL_CALLS:", worker_tool_calls)
    print("NOTEBOOK (shared):", reply.context.variables["notebook"])
    usage = await reply.usage()
    print(f"Tokens across the tree: {usage.total.total_tokens}  |  wall time {elapsed:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
