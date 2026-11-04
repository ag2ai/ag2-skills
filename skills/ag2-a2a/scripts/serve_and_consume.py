#!/usr/bin/env python3
"""Serve an AG2 agent over A2A JSON-RPC with uvicorn and call it from another AG2 agent — one process.

Shows: in-process uvicorn startup/shutdown, signed AgentCard + client-side verification,
multi-turn via reply.ask, a client-side (local) tool forwarded from the server's LLM.

Run:  OPENAI_API_KEY=... python scripts/serve_and_consume.py
Needs: pip install "ag2[a2a,openai]" "a2a-sdk[http-server,signing]" uvicorn cryptography
"""

import asyncio
import socket

import uvicorn
from a2a.utils.signing import create_agent_card_signer, create_signature_verifier
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from ag2 import Agent
from ag2.a2a import A2AConfig, A2AServer
from ag2.config import OpenAIResponsesConfig
from ag2.tools import tool

# --- keys for card signing (load from a secret store in production) ---
key = ec.generate_private_key(ec.SECP256R1())
private_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
public_pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
signer = create_agent_card_signer(private_pem, {"kid": "card-2026", "alg": "ES256", "jku": None, "typ": "JOSE"})
verifier = create_signature_verifier(lambda kid, jku: public_pem, ["ES256"])


@tool(description="Add two integers.")
async def calc_add(a: int, b: int) -> str:
    return str(a + b)


served = Agent(
    name="calculator",
    prompt="You are a terse calculator assistant. Use calc_add for additions.",
    config=OpenAIResponsesConfig(model="gpt-4.1-mini"),
    tools=[calc_add],  # server-side tool: runs in the server process
)


@tool(description="Return the caller's local wall-clock time.")
def get_local_time() -> str:
    return "09:41"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def main() -> None:
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    app = A2AServer(served, card_signer=signer).build_jsonrpc(url=url)  # Starlette app; card at /.well-known/agent-card.json

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serve_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        remote = Agent("remote", config=A2AConfig(card_url=url, card_signature_verifier=verifier))

        reply = await remote.ask("What is 19 + 23? Answer with just the number.")
        print("turn 1:", reply.body)
        reply = await reply.ask("Now add 100 to that. Just the number.")  # full history is re-sent
        print("turn 2:", reply.body)

        # Client-side tool: declared on the CLIENT, executed locally when the remote LLM calls it.
        local = Agent("local", config=A2AConfig(card_url=url, card_signature_verifier=verifier), tools=[get_local_time])
        reply = await local.ask("What time is it on my machine? Use get_local_time.")
        print("client tool:", reply.body)
    finally:
        server.should_exit = True
        await serve_task


if __name__ == "__main__":
    asyncio.run(main())
