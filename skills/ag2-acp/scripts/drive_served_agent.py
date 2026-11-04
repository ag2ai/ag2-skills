#!/usr/bin/env python3
"""AG2 as ACP client driving serve_stdio.py (an AG2 agent served over ACP) — both directions, real model.

Run:  OPENAI_API_KEY=... python scripts/drive_served_agent.py
The same ACPConfig shape drives Claude Code / Codex / OpenCode via their presets.
"""

import asyncio
import os
import sys
from pathlib import Path

from ag2 import Agent
from ag2.acp import ACPConfig
from ag2.events import ModelMessageChunk
from ag2.events.tool_events import BuiltinToolCallEvent

HERE = Path(__file__).resolve().parent


async def main() -> None:
    config = ACPConfig(
        command=[sys.executable, str(HERE / "serve_stdio.py")],
        cwd=str(HERE),
        env={"OPENAI_API_KEY": os.environ["OPENAI_API_KEY"]},  # the subprocess gets a TRIMMED env; pass keys explicitly
        permission_policy="auto",
        elicitation_policy="decline",
        expose_tools=False,
        turn_timeout=120,
    )
    async with config:  # tears down the subprocess (and tool gateway) on exit
        agent = Agent("remote_workie", config=config)
        chunks, tools = [], []

        def observe(event: object) -> None:
            if isinstance(event, ModelMessageChunk):
                chunks.append(event.content)
            elif isinstance(event, BuiltinToolCallEvent):
                tools.append(event.name)

        async with agent.run("Use calc_add to add 19 and 23. Reply with just the number.") as run:
            run.stream.subscribe(observe)
            reply = await run.result()
        print("reply:", reply.body, "| streamed chunks:", len(chunks), "| tool calls seen:", tools)

        follow = await reply.ask("Now double it. Just the number.")  # same ACP session
        print("follow-up:", follow.body)


if __name__ == "__main__":
    asyncio.run(main())
