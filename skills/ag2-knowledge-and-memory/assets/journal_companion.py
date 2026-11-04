"""Journal companion — knowledge store with working memory (AG2 1.3).

Mirrors website/docs/user-guide/code_examples/06_journal_companion.mdx. Persistent
agent memory using three primitives:

- KnowledgeStore — virtual filesystem for agent state (Sqlite here: no extra
  install; swap in DiskKnowledgeStore for human-readable files, which needs
  `pip install "watchdog>=4.0,<7"`).
- KnowledgePlugin(aggregate=WorkingMemoryAggregate, aggregate_trigger=...) —
  LLM-driven rollup that runs when each conversation ends and writes
  /memory/working.md.
- Middleware(AssemblerMiddleware, policies=[WorkingMemoryPolicy(), ...]) —
  reads /memory/working.md and injects it into every LLM call.

The agent "remembers" across a full restart because the state lives in the
store, not in conversation history.

Run::

    python journal_companion.py        # needs GEMINI_API_KEY
"""

import asyncio
import shutil
import tempfile
from pathlib import Path

from ag2 import Agent
from ag2.aggregate import AggregateTrigger, WorkingMemoryAggregate
from ag2.assembly import AssemblerMiddleware
from ag2.config import GeminiConfig
from ag2.knowledge import KnowledgePlugin, SqliteKnowledgeStore
from ag2.middleware import Middleware
from ag2.policies import ConversationPolicy, WorkingMemoryPolicy


def section(title: str) -> None:
    print(f"\n── {title} ───")


async def main() -> None:
    config = GeminiConfig(model="gemini-3.6-flash", temperature=0)
    workdir = Path(tempfile.mkdtemp(prefix="journal-companion-"))

    try:
        db_path = workdir / "journal.db"

        def build_agent() -> Agent:
            # A fresh store object per "process" proves persistence lives on disk.
            store = SqliteKnowledgeStore(db_path)
            return Agent(
                "journal",
                prompt=(
                    "You are a supportive daily journal companion. Keep a "
                    "running understanding of what the user is working on. "
                    "Be brief and reference their past entries when relevant."
                ),
                config=config,
                plugins=[
                    KnowledgePlugin(
                        store,
                        expose_tool=False,  # memory is policy-driven; no `knowledge` tool for the LLM
                        write_event_log=False,  # don't dump /log/{stream_id}.jsonl
                        aggregate=WorkingMemoryAggregate(config=config),
                        aggregate_trigger=AggregateTrigger(on_end=True),
                    )
                ],
                middleware=[
                    Middleware(
                        AssemblerMiddleware,
                        policies=[
                            WorkingMemoryPolicy(),  # inject /memory/working.md (injection first)
                            ConversationPolicy(),  # then filter to conversation events
                        ],
                    ),
                ],
            )

        section("Session 1 — tell the journal what you're doing")
        agent1 = build_agent()
        r = await agent1.ask(
            "Today I started learning to build a home espresso setup. Still "
            "choosing between a Silvia Pro and a Linea Mini."
        )
        print(r.body)
        r = await r.ask(
            "Also started reading The Pragmatic Programmer. On chapter 2 about "
            "orthogonality. That's the whole update."
        )
        print(r.body)
        # Aggregation (on_end) has run by the time ask() returned.

        working = await SqliteKnowledgeStore(db_path).read("/memory/working.md")
        print("\n## /memory/working.md after session 1")
        print(working)

        section("Session 2 — new Agent + new store object, same DB: memory persists")
        agent2 = build_agent()
        r2 = await agent2.ask("Quick check-in: what was I working on? Answer in one line.")
        print(r2.body)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
