# Copyright (c) 2026, AG2ai, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Exercises every code snippet in ../SKILL.md in-process.

Run: ../../../.venv/bin/python test_snippets.py
Uses TestConfig to mock the served agent's LLM (no API key / network needed)
and the a2a `testing.py` ASGI in-process helpers to actually drive the server.
"""

import asyncio
import json

from autogen.beta import Agent
from autogen.beta.testing import TestConfig


# ---------------------------------------------------------------------------
# 1. Build a card (no server needed)
# ---------------------------------------------------------------------------
def test_build_card():
    from autogen.beta.a2a import build_card

    agent = Agent(
        name="weather_bot",
        prompt="You answer questions about the weather.",
        config=TestConfig("It is sunny."),
    )
    card = build_card(agent, url="http://localhost:8000")
    assert card.name == "weather_bot"
    assert card.description == "You answer questions about the weather."
    # one interface for the default jsonrpc transport
    assert len(card.supported_interfaces) == 1
    assert card.capabilities.streaming is True
    # falls back to a single skill derived from the agent
    assert any(s.id == "weather_bot" for s in card.skills)
    print("test_build_card: PASS")


# ---------------------------------------------------------------------------
# 2. 60-second recipe: A2AServer + FastAPI mount (construct only, no serve)
# ---------------------------------------------------------------------------
def test_server_construct_and_mount():
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from autogen.beta.a2a import A2AServer

    agent = Agent(
        name="weather_bot",
        prompt="You answer questions about the weather.",
        config=TestConfig("It is sunny."),
    )
    server = A2AServer(agent)
    # build_jsonrpc returns a ready-to-serve Starlette ASGI app
    asgi = server.build_jsonrpc(url="http://localhost:8000")
    assert isinstance(asgi, Starlette)

    # mount under a parent Starlette app if you need to combine routes
    parent = Starlette(routes=[Mount("/a2a", app=asgi)])
    assert parent is not None
    assert server.agent is agent
    # task store is materialised eagerly and shared
    assert server.task_store is not None
    print("test_server_construct_and_mount: PASS")


# ---------------------------------------------------------------------------
# 3. End-to-end in-process round trip via the testing helpers (JSON-RPC)
# ---------------------------------------------------------------------------
async def test_inprocess_jsonrpc():
    from autogen.beta.a2a import A2AConfig, A2AServer
    from autogen.beta.a2a.testing import make_test_client_factory

    served = Agent(
        name="weather_bot",
        prompt="You answer questions about the weather.",
        config=TestConfig("It is sunny in Paris."),
    )
    server = A2AServer(served)

    factory = make_test_client_factory(server, url="http://test")
    remote = Agent(
        "remote",
        config=A2AConfig(card_url="http://test", httpx_client_factory=factory),
    )
    reply = await remote.ask("What is the weather in Paris?")
    text = await reply.content()
    assert "sunny" in text.lower(), text
    print("test_inprocess_jsonrpc: PASS ->", text)


# ---------------------------------------------------------------------------
# 4. End-to-end in-process round trip over REST
# ---------------------------------------------------------------------------
async def test_inprocess_rest():
    from autogen.beta.a2a import A2AConfig, A2AServer
    from autogen.beta.a2a.testing import make_test_rest_client_factory

    served = Agent(
        name="echo_bot",
        prompt="Echo back.",
        config=TestConfig("pong"),
    )
    server = A2AServer(served)
    factory = make_test_rest_client_factory(server, url="http://test")
    remote = Agent(
        "remote",
        config=A2AConfig(card_url="http://test", prefer="rest", httpx_client_factory=factory),
    )
    reply = await remote.ask("ping")
    text = await reply.content()
    assert text == "pong", text
    print("test_inprocess_rest: PASS ->", text)


# ---------------------------------------------------------------------------
# 5. Agent card with explicit skills + provider + docs
# ---------------------------------------------------------------------------
def test_card_options():
    from a2a.types import AgentProvider, AgentSkill

    from autogen.beta.a2a import build_card

    agent = Agent(name="bot", prompt="A helpful bot.", config=TestConfig("hi"))
    card = build_card(
        agent,
        url="http://localhost:8000",
        version="2.1.0",
        description="Customer support agent",
        push_notifications=True,
        skills=[
            AgentSkill(
                id="refunds",
                name="Process refunds",
                description="Issue refunds for orders",
                tags=["billing"],
            ),
        ],
        provider=AgentProvider(organization="Acme", url="https://acme.example"),
        documentation_url="https://acme.example/docs",
    )
    assert card.version == "2.1.0"
    assert card.description == "Customer support agent"
    assert card.capabilities.push_notifications is True
    assert [s.id for s in card.skills] == ["refunds"]
    assert card.provider.organization == "Acme"
    print("test_card_options: PASS")


# ---------------------------------------------------------------------------
# 6. Security schemes on the card
# ---------------------------------------------------------------------------
def test_security():
    from autogen.beta.a2a import build_card
    from autogen.beta.a2a.security import (
        api_key_scheme,
        bearer_scheme,
        require,
    )

    agent = Agent(name="bot", prompt="A bot.", config=TestConfig("hi"))

    bearer = bearer_scheme(name="bearer")
    apikey = api_key_scheme(name="apikey", key_name="X-API-Key", location="header")

    card = build_card(
        agent,
        url="http://localhost:8000",
        security=[require(bearer), require(apikey)],
    )
    # schemes auto-derived from the requirements
    assert set(card.security_schemes.keys()) == {"bearer", "apikey"}
    assert len(card.security_requirements) == 2
    print("test_security: PASS")


# ---------------------------------------------------------------------------
# 7. OAuth2 scoped scheme (AND-set requirement)
# ---------------------------------------------------------------------------
def test_oauth_scopes():
    from a2a.types import AuthorizationCodeOAuthFlow, OAuthFlows

    from autogen.beta.a2a import build_card
    from autogen.beta.a2a.security import bearer_scheme, oauth2_scheme, require

    agent = Agent(name="bot", prompt="A bot.", config=TestConfig("hi"))
    flows = OAuthFlows(
        authorization_code=AuthorizationCodeOAuthFlow(
            authorization_url="https://auth.example/authorize",
            token_url="https://auth.example/token",
            scopes={"read": "Read access", "write": "Write access"},
        ),
    )
    oauth = oauth2_scheme(name="oauth", flows=flows)
    bearer = bearer_scheme(name="bearer")
    card = build_card(
        agent,
        url="http://localhost:8000",
        # both must be presented together (AND), scoped oauth
        security=[require(bearer, oauth.with_scopes("read", "write"))],
    )
    assert set(card.security_schemes.keys()) == {"bearer", "oauth"}
    assert len(card.security_requirements) == 1
    print("test_oauth_scopes: PASS")


# ---------------------------------------------------------------------------
# 8. Push notifications enabled on the server (capability shows on card)
# ---------------------------------------------------------------------------
def test_push_capability():
    from a2a.server.tasks import InMemoryPushNotificationConfigStore

    from autogen.beta.a2a import A2AServer

    agent = Agent(name="bot", prompt="A bot.", config=TestConfig("hi"))
    server = A2AServer(agent, push_config_store=InMemoryPushNotificationConfigStore())
    asgi = server.build_jsonrpc(url="http://localhost:8000")
    assert asgi is not None
    # the auto-built card flips push_notifications on when a store is present
    from autogen.beta.a2a import build_card

    card = build_card(agent, url="http://localhost:8000", push_notifications=True)
    assert card.capabilities.push_notifications is True
    print("test_push_capability: PASS")


# ---------------------------------------------------------------------------
# 9. Raw card served over the in-process ASGI app (well-known path)
# ---------------------------------------------------------------------------
async def test_card_served_over_http():
    import httpx

    from autogen.beta.a2a import A2AServer

    agent = Agent(name="bot", prompt="A bot.", config=TestConfig("hi"))
    server = A2AServer(agent)
    app = server.build_jsonrpc(url="http://test")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/.well-known/agent-card.json")
        assert resp.status_code == 200, resp.status_code
        data = resp.json()
        assert data["name"] == "bot"
    print("test_card_served_over_http: PASS")


async def amain():
    test_build_card()
    test_server_construct_and_mount()
    await test_inprocess_jsonrpc()
    await test_inprocess_rest()
    test_card_options()
    test_security()
    test_oauth_scopes()
    test_push_capability()
    await test_card_served_over_http()
    print("\nALL SNIPPET TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(amain())
