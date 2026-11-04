#!/usr/bin/env python3
"""Serve an AG2 agent over streamable HTTP and consume it two ways, in one process.

1. `mcp.client.Client` (raw MCP SDK) threading a conversation handle.
2. Another AG2 Agent using the server's tools through `MCPToolkit` (client side).

Run:  OPENAI_API_KEY=... python scripts/serve_and_consume.py
Needs: pip install "ag2[mcp,openai]" uvicorn
"""

import asyncio

import uvicorn
from mcp.client import Client

from ag2 import Agent
from ag2.config import OpenAIResponsesConfig
from ag2.mcp import MCPServer, mcp_tool
from ag2.tools import MCPServerConfig, MCPToolkit

config = OpenAIResponsesConfig(model="gpt-4.1-mini")


@mcp_tool
def stock_level(sku: str) -> str:
    """Return how many units of a SKU are in stock."""
    return f"{sku}: 42 units"


served = Agent(name="assistant", prompt="You are a helpful, terse assistant.", config=config)
app = MCPServer(served, name="shop-mcp", tools=[stock_level])  # tools: ask + stock_level


async def main() -> None:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    url = "http://127.0.0.1:8765/mcp"
    try:
        # 1) Raw MCP client: the conversation handle comes back in result.meta.
        async with Client(url) as client:
            first = await client.call_tool("ask", {"message": "My name is Ada. Reply OK."})
            handle = first.meta["ai.ag2/conversation"]
            second = await client.call_tool("ask", {"message": "What is my name?", "conversation": handle})
            print("raw client:", second.content[0].text)

        # 2) An AG2 agent consuming the server's tools (client side).
        consumer = Agent(
            name="clerk",
            prompt="Answer stock questions using your tools.",
            config=config,
            tools=[MCPToolkit(MCPServerConfig(server_url=url, allowed_tools=["stock_level"]))],
        )
        reply = await consumer.ask("How many units of SKU-7 are in stock?")
        print("toolkit agent:", reply.body)
    finally:
        server.should_exit = True
        await task


if __name__ == "__main__":
    asyncio.run(main())
