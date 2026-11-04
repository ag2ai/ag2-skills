"""In-process tests for an ag2.mcp.MCPServer — no sockets, no API keys.

Run:  python references/test_server.py   (needs `pip install "ag2[mcp]"`)

Covers: the `ask` tool, the conversation handle, prompts, resources, a
deterministic `@mcp_tool` reading `MCPRequestContext`, a `Resolve(Elicit)`
parameter answered by the test client, and the HTTP transport via testing.serve.
"""

import asyncio
from typing import Annotated

from mcp.types import ElicitResult
from pydantic import BaseModel

from ag2 import Agent
from ag2.mcp import (
    Elicit,
    MCPRequestContext,
    MCPServer,
    Prompt,
    PromptArgument,
    PromptMessage,
    Resolve,
    Resource,
    ResourceTemplate,
    mcp_tool,
)
from ag2.mcp.testing import connect, serve
from ag2.testing import TestConfig


class Colour(BaseModel):
    answer: str


def pick_colour() -> Elicit[Colour]:
    return Elicit("What colour?", Colour)


@mcp_tool
def paint(room: str, colour: Annotated[Colour, Resolve(pick_colour)]) -> str:
    """Paint a room (colour is asked of the calling client, not the model)."""
    return f"painted {room} {colour.answer}"


@mcp_tool
async def whoami(ctx: MCPRequestContext) -> str:
    """Report the connected client's name (ctx is hidden from inputSchema)."""
    params = ctx.session.client_params
    return params.client_info.name if params else "unknown"


def render_review(args: dict[str, str]) -> list[PromptMessage]:
    return [PromptMessage(role="user", text=f"Review this {args['language']} code:\n{args.get('code', '')}")]


async def main() -> None:
    agent = Agent(name="assistant", prompt="p", config=TestConfig("Hello from the agent!"))
    server = MCPServer(
        agent,
        name="assistant-mcp",
        instructions="Ask me anything.",
        tools=[paint, whoami],
        prompts=[
            Prompt(
                name="code_review",
                render=render_review,
                arguments=(PromptArgument(name="language", required=True), PromptArgument(name="code")),
            )
        ],
        resources=[Resource(uri="config://app", name="cfg", mime_type="application/json", read=lambda: '{"env": "prod"}')],
        resource_templates=[ResourceTemplate(uri_template="greet://{name}", name="greet", read=lambda v: f"hi {v['name']}")],
    )

    async def on_elicit(context, params):  # the client's answer to a Resolve(Elicit) parameter
        return ElicitResult(action="accept", content={"answer": "teal"})

    # connect() yields an already-initialized ClientSession (do not call initialize() again).
    async with connect(server, elicitation_callback=on_elicit) as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}
        assert set(tools) == {"ask", "paint", "whoami"}, tools
        assert "colour" not in tools["paint"].input_schema["properties"]
        assert "conversation" in tools["ask"].input_schema["properties"]

        first = await session.call_tool("ask", {"message": "Hi"})
        assert first.content[0].text == "Hello from the agent!"
        handle = first.meta["ai.ag2/conversation"]  # opaque UUID; also echoed in a 2nd text block
        again = await session.call_tool("ask", {"message": "More", "conversation": handle})
        assert not again.is_error
        bad = await session.call_tool("ask", {"message": "x", "conversation": "nope"})
        assert bad.is_error  # unknown handle -> tool error, not protocol error

        painted = await session.call_tool("paint", {"room": "kitchen"})
        assert painted.content[0].text == "painted kitchen teal", painted
        me = await session.call_tool("whoami", {})
        print("whoami ->", me.content[0].text)

        prompt = await session.get_prompt("code_review", {"language": "python", "code": "x = 1"})
        assert "python" in prompt.messages[0].content.text

        res = await session.read_resource("config://app")
        assert res.contents[0].text == '{"env": "prod"}'
        res = await session.read_resource("greet://Ada")
        assert res.contents[0].text == "hi Ada"

    # HTTP transport in memory (initialize handshake + mcp-session-id).
    async with serve(server) as client:
        resp = await client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1.0"},
                },
            },
        )
        assert resp.status_code == 200
        assert "mcp-session-id" in resp.headers

    print("ok")


if __name__ == "__main__":
    asyncio.run(main())
