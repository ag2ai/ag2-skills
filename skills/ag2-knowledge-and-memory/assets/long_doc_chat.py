"""Long-doc chat — assembly policies + compaction (AG2 1.3).

Mirrors website/docs/user-guide/code_examples/07_long_doc_chat.mdx.

- Middleware(AssemblerMiddleware, policies=[...]) shapes what the LLM sees per
  call (the stream itself is untouched):
    1. ConversationPolicy — drop non-conversation/tool events.
    2. SlidingWindowPolicy(max_events=6) — cap events sent to the LLM.
    3. TokenBudgetPolicy(max_tokens=2000) — char-based secondary cap.
- KnowledgePlugin(compact=TailWindowCompact, compact_trigger=CompactTrigger)
  trims the stream history itself between turns; dropped events are archived
  to the store under /log/.

Run::

    python long_doc_chat.py        # needs GEMINI_API_KEY
"""

import asyncio

from ag2 import Agent
from ag2.assembly import AssemblerMiddleware
from ag2.compact import CompactTrigger, TailWindowCompact
from ag2.config import GeminiConfig
from ag2.events import CompactionCompleted
from ag2.knowledge import KnowledgePlugin, MemoryKnowledgeStore
from ag2.middleware import Middleware
from ag2.policies import ConversationPolicy, SlidingWindowPolicy, TokenBudgetPolicy
from ag2.stream import MemoryStream

QUESTIONS = [
    "Remember the word 'oak'.",
    "Remember the word 'river'.",
    "Remember the word 'lantern'.",
    "Remember the word 'sable'.",
    "Remember the word 'quartz'.",
    "Name the three most recent words I asked you to remember.",
]


async def main() -> None:
    config = GeminiConfig(model="gemini-3.6-flash", temperature=0)

    store = MemoryKnowledgeStore()
    compactions: list[CompactionCompleted] = []
    stream = MemoryStream()
    stream.where(CompactionCompleted).subscribe(lambda e: compactions.append(e))

    agent = Agent(
        "lexicon",
        prompt="Be very terse — one short sentence per reply. Answer directly without calling any tools.",
        config=config,
        middleware=[
            Middleware(
                AssemblerMiddleware,
                policies=[
                    ConversationPolicy(),
                    SlidingWindowPolicy(max_events=6, transparent=True),
                    TokenBudgetPolicy(max_tokens=2000),
                ],
            ),
        ],
        plugins=[
            KnowledgePlugin(
                store,
                expose_tool=False,
                compact=TailWindowCompact(target=4),
                compact_trigger=CompactTrigger(max_events=8),
            )
        ],
    )

    reply = await agent.ask(QUESTIONS[0], stream=stream)
    print(f"Q1> {QUESTIONS[0]}\nA1> {reply.body}")
    for i, q in enumerate(QUESTIONS[1:], start=2):
        reply = await reply.ask(q)
        print(f"Q{i}> {q}\nA{i}> {reply.body}")

    print(f"\nCompactions fired during run: {len(compactions)}")
    for c in compactions:
        print(f"  - {c.strategy}: {c.events_before} -> {c.events_after} events")
    print("Store /log/:", await store.list("/log/"))


if __name__ == "__main__":
    asyncio.run(main())
