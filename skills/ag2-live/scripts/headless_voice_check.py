"""Exercise an AG2 voice session end to end without a microphone or speaker.

Synthesizes a spoken question with OpenAI TTS, streams it into a running
`LiveAgent` as `RecordedAudioEvent` chunks (followed by silence so the turn
detector closes the turn), and reports what came back on the event stream.

Default: a `CascadeConfig` (OpenAI STT + gpt-4.1-mini + OpenAI TTS) with one tool.
Swap `build_agent()` for your own `LiveAgent` to smoke-test it in CI.

Usage:
    OPENAI_API_KEY=... python headless_voice_check.py ["question to ask"]
Requires: pip install "ag2[openai]"
"""

import asyncio
import sys

from ag2.config import OpenAIResponsesConfig
from ag2.events import (
    ModelResponse,
    RecordedAudioEvent,
    SynthesizedAudioEvent,
    ToolCallEvent,
    TranscriptionCompletedEvent,
)
from ag2.live import CascadeConfig, LiveAgent, OpenAITranscriber, OpenAITTSConfig

SAMPLE_RATE = 24_000  # what SoundDeviceRecorder / OpenAI use by default (Gemini input: 16_000)
CHUNK = int(SAMPLE_RATE * 0.1) * 2  # 100 ms of mono int16


def build_agent() -> LiveAgent:
    agent = LiveAgent(
        "assistant",
        prompt="You are a voice assistant. Answer in one short sentence.",
        config=CascadeConfig(
            stt=OpenAITranscriber("gpt-4o-mini-transcribe"),
            model=OpenAIResponsesConfig("gpt-4.1-mini", streaming=True),
            tts=OpenAITTSConfig("gpt-4o-mini-tts"),
        ),
    )

    @agent.tool
    async def sum_numbers(a: int, b: int) -> int:
        """Sum two numbers."""
        return a + b

    return agent


async def main(question: str) -> None:
    pcm = await OpenAITTSConfig("gpt-4o-mini-tts").synthesize(question)
    agent = build_agent()
    seen: dict[str, object] = {"transcript": None, "reply": None, "audio_bytes": 0, "tools": []}
    replied = asyncio.Event()

    async with agent.run() as context:

        async def on_transcript(event: TranscriptionCompletedEvent) -> None:
            seen["transcript"] = event.content

        async def on_audio(event: SynthesizedAudioEvent) -> None:
            seen["audio_bytes"] = int(seen["audio_bytes"]) + len(event.content)  # type: ignore[call-overload]

        async def on_tool(event: ToolCallEvent) -> None:
            seen["tools"].append(event.name)  # type: ignore[attr-defined]

        async def on_response(event: ModelResponse) -> None:
            if not event.tool_calls:  # the final answer of the turn
                seen["reply"] = event.content
                replied.set()

        context.stream.where(TranscriptionCompletedEvent).subscribe(on_transcript)
        context.stream.where(SynthesizedAudioEvent).subscribe(on_audio)
        context.stream.where(ToolCallEvent).subscribe(on_tool)
        context.stream.where(ModelResponse).subscribe(on_response)

        await asyncio.sleep(0.5)  # let a realtime provider finish connecting
        for i in range(0, len(pcm), CHUNK):  # "speak" at roughly real-time pace
            await context.send(RecordedAudioEvent(pcm[i : i + CHUNK]))
            await asyncio.sleep(0.05)
        for _ in range(20):  # 2 s of silence closes the turn
            await context.send(RecordedAudioEvent(b"\x00" * CHUNK))
            await asyncio.sleep(0.05)

        await asyncio.wait_for(replied.wait(), timeout=60)
        await asyncio.sleep(3)  # let the TTS of the last sentence land

    usage = await LiveAgent.usage_report(context)
    for key, value in seen.items():
        print(f"{key:12}: {value}")
    print(f"{'tokens':12}: {usage.total.total_tokens}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "Use the sum tool to add seventeen and twenty five."))
