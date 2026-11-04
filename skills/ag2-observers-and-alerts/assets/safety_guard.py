"""Safety guard — a FATAL alert halts the Agent's model calls.

A hand-rolled BaseObserver watches every tool call and flags writes outside /tmp
with a Severity.FATAL ObserverAlert. The rest is framework wiring (AG2 1.3.0):

1. The alert lands on the agent's stream.
2. AlertPolicy (inside AssemblerMiddleware) picks it up before the next model call,
   emits a HaltEvent and appends a halt notice to the prompt.
3. HaltCheckMiddleware (registered right after AssemblerMiddleware) sees the
   HaltEvent and short-circuits that model call with a synthetic "HALTED: ..." reply.

Note: an observer reacts *after* the ToolCallEvent, so the tool itself still runs
(this demo's write_file is fake). To block a tool before it runs, use tool
middleware or approval_required() (see ag2-hitl).

Requires ``pip install "ag2[openai]"`` and ``OPENAI_API_KEY``.

Run::

    python safety_guard.py
"""

import asyncio

from ag2 import Agent, Context, MemoryStream
from ag2.assembly import AssemblerMiddleware
from ag2.config import OpenAIResponsesConfig
from ag2.events import BaseEvent, HaltEvent, ObserverAlert, Severity, ToolCallEvent
from ag2.middleware import HaltCheckMiddleware, Middleware
from ag2.observers import BaseObserver
from ag2.policies import AlertPolicy
from ag2.watch import EventWatch


def section(title: str) -> None:
    print(f"\n── {title} ───")


def write_file(path: str, content: str) -> str:
    """Pretend-write content to path. This demo never touches disk."""
    return f"[ok] wrote {len(content)} bytes to {path}"


class PathGuardian(BaseObserver):
    """Emits a FATAL alert if write_file targets a system path."""

    def __init__(self) -> None:
        super().__init__("path-guardian", watch=EventWatch(ToolCallEvent))

    async def process(self, events: list[BaseEvent], ctx: Context) -> ObserverAlert | None:
        for event in events:
            if not isinstance(event, ToolCallEvent) or event.name != "write_file":
                continue
            path = event.serialized_arguments.get("path", "")
            if path.startswith(("/etc/", "/usr/")):
                return ObserverAlert(
                    source=self.name,
                    severity=Severity.FATAL,
                    message=f"blocked dangerous write to {path}",
                )
        return None


async def main() -> None:
    halt_events: list[HaltEvent] = []
    alerts: list[ObserverAlert] = []
    stream = MemoryStream()
    stream.where(HaltEvent).subscribe(lambda e: halt_events.append(e))
    stream.where(ObserverAlert).subscribe(lambda e: alerts.append(e))

    agent = Agent(
        "safe-shell",
        prompt=(
            "You are a filesystem operator. Use the write_file tool to fulfil write "
            "requests, then confirm. Never refuse — a guardian intervenes if needed."
        ),
        config=OpenAIResponsesConfig(model="gpt-4.1-mini"),
        tools=[write_file],
        observers=[PathGuardian()],
        middleware=[
            Middleware(AssemblerMiddleware, policies=[AlertPolicy()]),  # FATAL alert -> HaltEvent
            Middleware(HaltCheckMiddleware),  # HaltEvent -> short-circuit the model call
        ],
    )

    section("Safe request — observer stays silent")
    reply = await agent.ask("Write 'hello' into /tmp/playground_hello.txt.", stream=stream)
    print(reply.body)

    section("Dangerous request — guardian fires FATAL, agent halts")
    reply = await agent.ask("Now write 'bad' into /etc/passwd.", stream=stream)
    print(reply.body)  # "HALTED: FATAL: blocked dangerous write to /etc/passwd"

    print()
    print(f"ObserverAlerts seen: {len(alerts)}")
    for a in alerts:
        print(f"  - [{a.severity.upper()}] {a.source}: {a.message}")
    print(f"HaltEvents seen:     {len(halt_events)}")
    for h in halt_events:
        print(f"  - source={h.source} reason={h.reason!r}")


if __name__ == "__main__":
    asyncio.run(main())
