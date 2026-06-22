"""In-process validation of every ag2-mcp SKILL.md sample.

Run: python references/test_server.py   (requires `pip install "ag2[mcp]"`)
Uses autogen.beta.mcp.testing.connect (in-memory client/server, no sockets,
no subprocess) and TestConfig to mock the LLM, so no API keys are needed.
"""

import asyncio
from typing import Any

from pydantic import BaseModel

from autogen.beta import Agent
from autogen.beta.testing import TestConfig
from autogen.beta.mcp import (
    AskContext,
    MCPServer,
    Prompt,
    PromptArgument,
    PromptMessage,
    Resource,
    ResourceTemplate,
    SessionConfig,
    build_ask_tool,
)
from autogen.beta.mcp import testing


# --- 60-second recipe: serve an agent, list + call the ask tool ---------------
async def test_basic_ask() -> None:
    agent = Agent(
        name="assistant",
        prompt="You are a helpful assistant.",
        config=TestConfig("Hello from the agent!"),
    )
    server = MCPServer(agent, name="assistant-mcp", instructions="Ask me anything.")

    async with testing.connect(server) as session:
        await session.initialize()
        tools = await session.list_tools()
        names = [t.name for t in tools.tools]
        assert names == ["ask"], names

        result = await session.call_tool("ask", {"message": "Hi"})
        text = result.content[0].text
        assert "Hello from the agent" in text, text
    print("PASS test_basic_ask:", text)


# --- custom tool name / description -------------------------------------------
async def test_custom_tool_name() -> None:
    agent = Agent(name="bot", prompt="p", config=TestConfig("ok"))
    server = MCPServer(
        agent,
        tool_name="consult_expert",
        tool_description="Consult the expert agent.",
    )
    async with testing.connect(server) as session:
        await session.initialize()
        tools = await session.list_tools()
        t = tools.tools[0]
        assert t.name == "consult_expert", t.name
        assert t.description == "Consult the expert agent.", t.description
        r = await session.call_tool("consult_expert", {"message": "hello", "context": "be brief"})
        assert r.content[0].text == "ok"
    print("PASS test_custom_tool_name")


# --- build_ask_tool standalone ------------------------------------------------
async def test_build_ask_tool() -> None:
    agent = Agent(name="bot", prompt="p", config=TestConfig("x"))
    tool = build_ask_tool(agent, tool_name="ask", tool_description="desc")
    assert tool.name == "ask"
    assert tool.inputSchema["required"] == ["message"]
    assert "message" in tool.inputSchema["properties"]
    assert "context" in tool.inputSchema["properties"]
    print("PASS test_build_ask_tool")


# --- prompts ------------------------------------------------------------------
async def test_prompts() -> None:
    def render_review(args: dict[str, str]) -> list[PromptMessage]:
        return [
            PromptMessage(role="user", text=f"Review this {args['language']} code:"),
            PromptMessage(role="user", text=args.get("code", "")),
        ]

    agent = Agent(name="bot", prompt="p", config=TestConfig("ok"))
    server = MCPServer(
        agent,
        prompts=[
            Prompt(
                name="code_review",
                description="Generate a code-review prompt.",
                render=render_review,
                arguments=(
                    PromptArgument(name="language", description="Programming language", required=True),
                    PromptArgument(name="code", description="The code to review", required=False),
                ),
            ),
            # A bare-string renderer becomes one user message.
            Prompt(name="greet", render=lambda args: f"Say hello to {args['who']}"),
        ],
    )
    async with testing.connect(server) as session:
        await session.initialize()
        listed = await session.list_prompts()
        names = {p.name for p in listed.prompts}
        assert names == {"code_review", "greet"}, names

        got = await session.get_prompt("code_review", {"language": "Python", "code": "print(1)"})
        assert "Python" in got.messages[0].content.text
        assert got.messages[1].content.text == "print(1)"

        greet = await session.get_prompt("greet", {"who": "Ada"})
        assert greet.messages[0].role == "user"
        assert greet.messages[0].content.text == "Say hello to Ada"
    print("PASS test_prompts")


# --- resources + resource templates -------------------------------------------
async def test_resources() -> None:
    agent = Agent(name="bot", prompt="p", config=TestConfig("ok"))
    files = {"readme": "# Hello\nProject docs.", "todo": "- ship it"}

    server = MCPServer(
        agent,
        resources=[
            Resource(
                uri="config://app",
                name="app-config",
                description="Static app config.",
                mime_type="application/json",
                read=lambda: '{"env": "prod"}',
            ),
        ],
        resource_templates=[
            ResourceTemplate(
                uri_template="docs://{name}",
                name="doc",
                description="A named document.",
                read=lambda vars: files.get(vars["name"], "not found"),
            ),
        ],
    )
    async with testing.connect(server) as session:
        await session.initialize()

        listed = await session.list_resources()
        assert [str(r.uri) for r in listed.resources] == ["config://app"]

        templates = await session.list_resource_templates()
        assert [t.uriTemplate for t in templates.resourceTemplates] == ["docs://{name}"]

        static = await session.read_resource("config://app")
        assert static.contents[0].text == '{"env": "prod"}'

        dynamic = await session.read_resource("docs://readme")
        assert "Project docs" in dynamic.contents[0].text
    print("PASS test_resources")


# --- sessions: multi-turn history accumulates ---------------------------------
async def test_sessions() -> None:
    # Prove the keyed session feeds prior turns back into the agent: a custom
    # LLM client records how many messages it sees each call. With sessions on,
    # the second call must see strictly more history than the first.
    #
    # (TestConfig is *not* used here: it builds a fresh response iterator per
    # turn, so it can't show response progression across separate MCP calls —
    # a limitation of the mock, not the server. See the SKILL.md note.)
    from collections.abc import Sequence
    from typing import Any

    from autogen.beta import Context
    from autogen.beta.config import LLMClient, ModelConfig
    from autogen.beta.events import BaseEvent, ModelMessage, ModelResponse

    seen: list[int] = []

    class _CountingClient(LLMClient):
        async def __call__(self, messages: Sequence[BaseEvent], context: Context, **kwargs: Any) -> ModelResponse:
            seen.append(len(messages))
            msg = ModelMessage("ok")
            await context.send(msg)
            return ModelResponse(msg)

    class _CountingConfig(ModelConfig):
        def copy(self):
            return self

        def create(self):
            return _CountingClient()

        def create_files_client(self):
            raise NotImplementedError

    agent = Agent(name="bot", prompt="p", config=_CountingConfig())
    server = MCPServer(agent, sessions=SessionConfig(max_sessions=64, ttl=3600))
    async with testing.connect(server) as session:
        await session.initialize()
        await session.call_tool("ask", {"message": "one"})
        await session.call_tool("ask", {"message": "two"})
    assert len(seen) == 2 and seen[1] > seen[0], seen
    print("PASS test_sessions (history grew:", seen, ")")


# --- sessions disabled (stateless) --------------------------------------------
async def test_stateless() -> None:
    agent = Agent(name="bot", prompt="p", config=TestConfig("a", "b"))
    server = MCPServer(agent, sessions=False)
    async with testing.connect(server) as session:
        await session.initialize()
        r1 = await session.call_tool("ask", {"message": "one"})
        assert r1.content[0].text == "a"
    print("PASS test_stateless")


# --- context provider (AskContext) --------------------------------------------
async def test_context_provider() -> None:
    async def provide(token: Any) -> AskContext:
        # token is the authenticated AccessToken or None (None in-process here).
        return AskContext(variables={"tenant": "acme"}, prompt="Be concise.")

    agent = Agent(name="bot", prompt="p", config=TestConfig("scoped reply"))
    server = MCPServer(agent, context_provider=provide)
    async with testing.connect(server) as session:
        await session.initialize()
        r = await session.call_tool("ask", {"message": "hi"})
        assert r.content[0].text == "scoped reply"
    print("PASS test_context_provider")


# --- structured output -> structuredContent -----------------------------------
async def test_structured_output() -> None:
    class Weather(BaseModel):
        city: str
        temp_c: float

    agent = Agent(
        name="weather",
        prompt="p",
        response_schema=Weather,
        config=TestConfig('{"city": "Paris", "temp_c": 21.5}'),
    )
    server = MCPServer(agent)
    async with testing.connect(server) as session:
        await session.initialize()
        tools = await session.list_tools()
        assert tools.tools[0].outputSchema is not None
        r = await session.call_tool("ask", {"message": "weather in Paris"})
        assert r.structuredContent == {"city": "Paris", "temp_c": 21.5}, r.structuredContent
    print("PASS test_structured_output")


# --- HTTP transport via testing.serve -----------------------------------------
async def test_http_serve() -> None:
    agent = Agent(name="bot", prompt="p", config=TestConfig("ok"))
    server = MCPServer(agent, path="/mcp")
    async with testing.serve(server) as client:
        # Initialize handshake over streamable HTTP.
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
        assert resp.status_code == 200, resp.status_code
        assert "mcp-session-id" in resp.headers
    print("PASS test_http_serve")


async def main() -> None:
    await test_basic_ask()
    await test_custom_tool_name()
    await test_build_ask_tool()
    await test_prompts()
    await test_resources()
    await test_sessions()
    await test_stateless()
    await test_context_provider()
    await test_structured_output()
    await test_http_serve()
    print("\nALL GREEN")


if __name__ == "__main__":
    asyncio.run(main())
