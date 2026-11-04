"""Token watchdog — budget alerts that can halt the agent.

Observers on one Agent (AG2 1.3.0):

1. TokenMonitor — built-in; sums UsageEvents and emits WARNING / CRITICAL alerts.
2. LoopDetector — built-in; WARNING on repeated identical tool calls.
3. alert_console — an @observer that prints every ObserverAlert.
4. escalate — turns CRITICAL into FATAL, because only FATAL alerts halt.

AssemblerMiddleware + AlertPolicy shows alerts to the model and converts FATAL into
a HaltEvent; HaltCheckMiddleware then short-circuits the next model call of the
turn with "HALTED: ...". The tool round-trip guarantees a second model call, so
the halt is visible within a single ask().

Requires ``pip install "ag2[anthropic]"`` and ``ANTHROPIC_API_KEY``.

Run::

    python token_watchdog.py
"""

import asyncio

from ag2 import Agent, Context, observer, tool
from ag2.assembly import AssemblerMiddleware
from ag2.config import AnthropicConfig
from ag2.events import HaltEvent, ObserverAlert, Severity
from ag2.middleware import HaltCheckMiddleware, Middleware
from ag2.observers import LoopDetector, TokenMonitor
from ag2.policies import AlertPolicy

alerts: list[ObserverAlert] = []
halts: list[HaltEvent] = []


@observer(ObserverAlert)
def alert_console(event: ObserverAlert) -> None:
    alerts.append(event)
    print(f"    [{event.severity.upper():<8}] {event.source}: {event.message}")


@observer(ObserverAlert)
async def escalate(event: ObserverAlert, context: Context) -> None:
    """Only FATAL halts. Re-emit CRITICAL budget alerts as FATAL."""
    if event.severity == Severity.CRITICAL:
        await context.send(
            ObserverAlert(source="escalator", severity=Severity.FATAL, message=f"budget exceeded: {event.message}")
        )


@observer(HaltEvent)
def on_halt(event: HaltEvent) -> None:
    halts.append(event)


@tool
def word_count(text: str) -> int:
    """Count the words in a text."""
    return len(text.split())


async def main() -> None:
    # Deliberately tiny thresholds so a single ask trips them.
    token_monitor = TokenMonitor(warn_threshold=30, alert_threshold=50)

    agent = Agent(
        "writer",
        prompt="Write what the user asks for, then call word_count on it and report the count.",
        config=AnthropicConfig(model="claude-haiku-4-5"),
        tools=[word_count],
        observers=[token_monitor, LoopDetector(window_size=5, repeat_threshold=2), alert_console, escalate, on_halt],
        middleware=[
            Middleware(AssemblerMiddleware, policies=[AlertPolicy()]),
            Middleware(HaltCheckMiddleware),
        ],
    )

    reply = await agent.ask("Write a 200-word story about a robot.")

    print()
    print("Reply (truncated):", (reply.body or "")[:160])
    print(f"Tokens tracked by TokenMonitor: {token_monitor.total_tokens}")
    print(f"ALERTS: {len(alerts)}")
    print(f"HALTS:  {len(halts)}")
    print("DONE")


if __name__ == "__main__":
    asyncio.run(main())
