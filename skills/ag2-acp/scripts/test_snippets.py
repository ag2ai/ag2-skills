#!/usr/bin/env python3
"""In-process checks for ag2.acp, both directions — no subprocess, no API keys.

Run:  python scripts/test_snippets.py      (needs `pip install "ag2[acp]"`)
"""

import asyncio

import acp
from acp import schema

from ag2 import Agent, Context
from ag2.acp import ACPAgent, StaticTokenAuth
from ag2.acp.testing import ACPTurn, connect, fake_acp_config
from ag2.events import ModelReasoning
from ag2.testing import TestConfig


def text(t: str) -> schema.TextContentBlock:
    return schema.TextContentBlock(type="text", text=t)


async def client_side() -> None:
    """AG2 drives a (scripted) CLI agent — the ACPConfig path."""
    config = fake_acp_config(
        ACPTurn(updates=[
            schema.AgentThoughtChunk(session_update="agent_thought_chunk", content=text("planning")),
            schema.AgentMessageChunk(session_update="agent_message_chunk", content=text("done")),
        ]),
        permission_policy="auto",   # overrides forward to ACPConfig
        expose_tools=False,
    )
    async with config:              # tears down the session on exit
        seen = []
        async with Agent("coder", config=config).run("hello") as run:
            run.stream.subscribe(lambda e: seen.append(e))
            reply = await run.result()
        assert reply.body == "done", reply.body
        assert any(isinstance(e, ModelReasoning) for e in seen)


async def server_side() -> None:
    """An AG2 agent served over ACP to a (real, in-process) ACP client."""
    agent = Agent(name="workie", config=TestConfig("200"))
    async with connect(ACPAgent(agent)) as (client, recorder):
        session = await client.new_session(cwd=".")
        resp = await client.prompt(session_id=session.session_id, prompt=[acp.text_block("what's 100 + 100")])
        assert resp.stop_reason == "end_turn"
        texts = [u.content.text for u in recorder.updates_for(session.session_id) if isinstance(u, schema.AgentMessageChunk)]
        assert texts == ["200"], texts

    # auth: session methods refused until authenticate succeeds
    async with connect(ACPAgent(agent, auth=StaticTokenAuth("s3cret"))) as (client, _):
        try:
            await client.new_session(cwd=".")
            raise AssertionError("expected refusal")
        except acp.RequestError:
            pass
        await client.authenticate(method_id="token", token="s3cret")
        assert (await client.new_session(cwd=".")).session_id

    # hitl_hook on ACPAgent answers context.input() in served turns
    asked = []

    async def confirm(context: Context) -> str:
        """Ask the user to confirm."""
        return await context.input("Proceed?")

    from ag2.events import ToolCallEvent

    tool_agent = Agent(
        name="workie",
        config=TestConfig(ToolCallEvent(name="confirm", arguments="{}"), "ok"),
        tools=[confirm],
    )

    async def host_hook(event) -> str:
        asked.append(event.content)
        return "yes"

    async with connect(ACPAgent(tool_agent, hitl_hook=host_hook)) as (client, _):
        session = await client.new_session(cwd=".")
        resp = await client.prompt(session_id=session.session_id, prompt=[acp.text_block("go")])
        assert resp.stop_reason == "end_turn" and asked == ["Proceed?"], (resp, asked)


async def main() -> None:
    await server_side()
    await client_side()
    print("ok")


if __name__ == "__main__":
    asyncio.run(main())
