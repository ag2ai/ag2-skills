"""Serve an AG2 agent over AG-UI 1.0 (FastAPI + uvicorn, one process) and probe it with httpx.

Prints the event types actually emitted for: a plain run, a backend tool call, and a
human-input interrupt answered by a second run on the same thread (resume).

Run:  OPENAI_API_KEY=... python references/serve_and_probe.py
Needs: pip install "ag2[ag-ui,openai]" fastapi uvicorn httpx
"""

import asyncio
import json
import socket
from typing import Any
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ag2 import Agent, Context
from ag2.ag_ui import AGUIStream, read_run_input
from ag2.config import OpenAIResponsesConfig

agent = Agent(
    name="travel_desk",
    prompt="You book flights. Always use book_flight to book; use get_weather for weather.",
    config=OpenAIResponsesConfig(model="gpt-4.1-mini"),
)


@agent.tool
async def get_weather(city: str) -> str:
    """Current weather for a city."""
    return f"Sunny, 22C in {city}"


@agent.tool
async def book_flight(context: Context, flight: str) -> str:
    """Book a flight once the traveller confirms."""
    answer = await context.input(f"Book {flight}? (yes/no)")  # no hitl_hook -> AG-UI interrupt
    return "Booked." if answer.strip().lower().startswith("y") else "Not booked."


stream = AGUIStream(agent)
app = FastAPI()


@app.get("/chat")
@app.get("/chat/capabilities")
async def capabilities() -> JSONResponse:
    return JSONResponse(stream.capabilities().model_dump(by_alias=True, exclude_none=True))


@app.post("/chat")
async def run_agent(request: Request) -> Response:
    try:
        run_input = read_run_input(await request.body())  # tolerant of newer clients; ValueError -> 400
    except ValueError:
        return JSONResponse({"error": "invalid AG-UI RunAgentInput body"}, status_code=400)
    return StreamingResponse(
        stream.dispatch(run_input, accept=request.headers.get("accept")),
        media_type="text/event-stream",
    )


def body(thread: str, text: str | None = None, resume: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    b: dict[str, Any] = {
        "threadId": thread,
        "runId": str(uuid4()),                       # new runId per POST, same threadId across a turn
        "protocolVersion": "1.0",
        "messages": [{"id": str(uuid4()), "role": "user", "content": text}] if text else [],
        "state": {},
        "context": [],
        "tools": [],
        "forwardedProps": {},
    }
    if resume is not None:
        b["resume"] = resume
    return b


async def post(client: httpx.AsyncClient, payload: dict[str, Any]) -> list[dict[str, Any]]:
    r = await client.post("/chat", json=payload, headers={"Accept": "text/event-stream"})
    r.raise_for_status()
    frames = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    print("  ", " -> ".join(f["type"] for f in frames))
    return frames


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def main() -> None:
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        async with stream, httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=60) as client:
            caps = (await client.get("/chat/capabilities")).json()
            print("capabilities:", sorted(caps))

            print("plain run:")
            frames = await post(client, body("t-plain", "Say hello in five words."))
            print("   text:", "".join(f.get("delta", "") for f in frames if f["type"] == "TEXT_MESSAGE_CHUNK"))

            print("backend tool:")
            await post(client, body("t-tool", "What's the weather in Paris?"))

            print("interrupt (context.input with no hitl_hook):")
            first = await post(client, body("t-book", "Book LH441 for me."))
            [interrupt] = first[-1]["outcome"]["interrupts"]
            print("   asked:", interrupt["message"], "| reason:", interrupt["reason"])
            answer = {"interruptId": interrupt["id"], "status": "resolved", "payload": "yes",
                      "metadata": interrupt.get("metadata")}  # echo metadata (proof) back
            second = await post(client, body("t-book", resume=[answer]))
            print("   outcome:", second[-1].get("outcome"))
    finally:
        server.should_exit = True
        await task


if __name__ == "__main__":
    asyncio.run(main())
