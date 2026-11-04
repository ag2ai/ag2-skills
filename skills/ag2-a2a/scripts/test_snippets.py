#!/usr/bin/env python3
"""In-process checks for ag2.a2a — no sockets, no API keys.

Run:  python scripts/test_snippets.py
Needs: pip install "ag2[a2a]" "a2a-sdk[http-server,signing]" cryptography
"""

import asyncio

from a2a.server.tasks import InMemoryPushNotificationConfigStore
from a2a.types import AgentSkill
from a2a.utils.signing import create_agent_card_signer, create_signature_verifier
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from ag2 import Agent
from ag2.a2a import A2AConfig, A2AServer, build_card
from ag2.a2a.errors import A2ACardSignatureError
from ag2.a2a.security import api_key_scheme, bearer_scheme, require
from ag2.a2a.testing import make_test_client_factory, make_test_rest_client_factory
from ag2.testing import TestConfig


def keypair() -> tuple[bytes, bytes]:
    k = ec.generate_private_key(ec.SECP256R1())
    return (
        k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
        k.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo),
    )


async def main() -> None:
    served = Agent(name="weather_bot", prompt="You answer weather questions.", config=TestConfig("It is sunny in Paris."))
    server = A2AServer(served)

    # JSON-RPC round trip in memory
    remote = Agent("remote", config=A2AConfig(card_url="http://test", httpx_client_factory=make_test_client_factory(server, url="http://test")))
    reply = await remote.ask("Weather in Paris?")
    assert reply.body == "It is sunny in Paris.", reply.body

    # REST round trip in memory
    rest = Agent(
        "rest",
        config=A2AConfig(card_url="http://test", prefer="rest", httpx_client_factory=make_test_rest_client_factory(server, url="http://test")),
    )
    assert (await rest.ask("Weather?")).body == "It is sunny in Paris."

    # Card: skills, security (list = OR, args of require = AND)
    card = build_card(
        served,
        url="http://localhost:8000",
        version="2.1.0",
        skills=[AgentSkill(id="weather", name="Weather", description="Forecasts", tags=["weather"])],
        security=[require(bearer_scheme(name="bearer")), require(api_key_scheme(name="apikey", key_name="X-API-Key", location="header"))],
    )
    assert set(card.security_schemes) == {"bearer", "apikey"}
    assert any(e.uri == "urn:ag2:client-tools:v1" for e in card.capabilities.extensions)

    # Push: validator without a store is refused
    async def allow(url: str) -> bool:
        return url.startswith("https://")

    try:
        A2AServer(served, push_url_validator=allow)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    A2AServer(served, push_config_store=InMemoryPushNotificationConfigStore(), push_url_validator=allow)

    # Card signing: right key verifies, wrong key is refused before any request
    priv, pub = keypair()
    _, other_pub = keypair()
    signer = create_agent_card_signer(priv, {"kid": "k1", "alg": "ES256", "jku": None, "typ": "JOSE"})
    signed = A2AServer(served, card_signer=signer)
    factory = make_test_client_factory(signed, url="http://test")
    ok = Agent("ok", config=A2AConfig(card_url="http://test", httpx_client_factory=factory,
                                       card_signature_verifier=create_signature_verifier(lambda kid, jku: pub, ["ES256"])))
    assert (await ok.ask("hi")).body == "It is sunny in Paris."
    bad = Agent("bad", config=A2AConfig(card_url="http://test", httpx_client_factory=factory,
                                         card_signature_verifier=create_signature_verifier(lambda kid, jku: other_pub, ["ES256"])))
    try:
        await bad.ask("hi")
        raise AssertionError("expected A2ACardSignatureError")
    except A2ACardSignatureError:
        pass

    print("ok")


if __name__ == "__main__":
    asyncio.run(main())
