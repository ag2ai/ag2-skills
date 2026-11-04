#!/usr/bin/env python3
"""Serve an AG2 agent over ACP stdio (for Zed / JetBrains / any ACP client, or ACPConfig).

The CLIENT launches this file. stdout is the protocol wire: never print() here.
Client config (Zed settings.json):
  {"agent_servers": {"workie": {"type": "custom",
     "command": "/abs/.venv/bin/python", "args": ["/abs/serve_stdio.py"],
     "env": {"OPENAI_API_KEY": "sk-..."}}}}
"""

import asyncio

from ag2 import Agent
from ag2.acp import ACPAgent
from ag2.config import OpenAIResponsesConfig
from ag2.tools import tool


@tool(description="Add two integers.")
async def calc_add(a: int, b: int) -> str:
    return str(a + b)


agent = Agent(
    name="workie",
    prompt="You are a concise assistant. Use tools when they help.",
    config=OpenAIResponsesConfig(model="gpt-4.1-mini", streaming=True),  # streaming=True -> token-by-token in the client
    tools=[calc_add],
)

if __name__ == "__main__":
    asyncio.run(ACPAgent(agent).run_stdio())
