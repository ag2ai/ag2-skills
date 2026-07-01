---
name: ag2-live
description: Build realtime voice / live audio agents with AG2's `ag2.live` module. Wrap a prompt + provider config in `LiveAgent` and open a bidirectional voice session with `agent.run()`, pumping mic audio in and playing synthesized speech out. Covers the two realtime providers — Gemini Live (`GeminiRealTimeConfig`) and OpenAI Realtime (`OpenAIRealTimeConfig`) with audio/text output modalities, voices, and user-speech transcription; audio I/O over the local sound card (`SoundDeviceRecorder` / `SoundDevicePlayer`, both sounddevice-backed); one-shot speech-to-text (`OpenAITranscriber`, `OpenAITranslationTranscriber`) and its `.pipe(agent)` voice pipeline; text-to-speech (`OpenAITTSConfig`); and `TTSObserver`, which speaks a regular text `Agent`'s streamed tokens aloud. Use when the user wants a talking agent, a phone/voice assistant, live transcription, or to add TTS playback to a text agent.
license: Apache-2.0
---

# Realtime voice / live audio agents

## When to use

- The user wants a **talking agent**: speak into a mic, the agent replies with voice, hands-free, low latency (Gemini Live or OpenAI Realtime).
- They want **live transcription** of the user's speech while the agent talks.
- They want a **voice pipeline** over an existing text `Agent`: transcribe a recorded clip → run the agent → (optionally) speak the reply.
- They want to **add spoken output (TTS)** to an otherwise text-only streaming `Agent`.

If the user only needs to *send* a recorded audio file into an agent as one input (not a live session), use `ag2-multimodal-input` (`AudioInput`) instead.

> **Hardware / keys caveat.** A real session needs (a) a provider API key and (b) a working microphone + speaker. Neither is available headless. Everything in this skill **constructs** without hardware; the parts that actually open a socket or audio device are marked **[needs keys + audio]** below.

## Installation

The live module splits across optional extras — install the ones you need:

```bash
# OpenAI Realtime + OpenAI TTS/STT
pip install "ag2[openai-realtime]"

# Gemini Live
pip install "ag2[gemini-realtime]"

# Local microphone / speaker I/O (SoundDeviceRecorder / SoundDevicePlayer)
pip install "sounddevice[numpy]"
```

A typical OpenAI voice app on the local sound card: `pip install "ag2[openai-realtime]" "sounddevice[numpy]"`.

> Required. Run the relevant install before delivering code. Without the matching extra, the public symbol resolves to a placeholder that raises `ImportError` (`openai`/`gemini`) or the additional-dependency error (`sounddevice[numpy]`) the moment you use it.

## Public API

All exported from `ag2.live`:

| Symbol | Role |
|---|---|
| `LiveAgent` | Wraps a prompt + realtime config; `agent.run()` opens the bidirectional session |
| `GeminiRealTimeConfig` | Gemini Live realtime provider config |
| `OpenAIRealTimeConfig` | OpenAI Realtime provider config |
| `OpenAITTSConfig` | Text → speech (PCM bytes) |
| `OpenAITranscriber` | One-shot speech → text (transcription) |
| `OpenAITranslationTranscriber` | One-shot speech → English text (translation) |
| `SoundDeviceRecorder` | Mic capture → `RecordedAudioEvent` on the stream |
| `SoundDevicePlayer` | Plays `SynthesizedAudioEvent` PCM out the speaker |
| `TTSObserver` | Observer that speaks a text `Agent`'s streamed tokens via a TTS config |

## How a live session works

`LiveAgent` is built around an event **stream** (a `ConversationContext`). The recorder, the provider session, and the player all share that one context:

```
mic ──SoundDeviceRecorder──▶ RecordedAudioEvent ──▶ provider session (Gemini/OpenAI)
                                                       │
                                       SynthesizedAudioEvent (assistant audio)
                                                       ▼
                                              SoundDevicePlayer ──▶ speaker
```

Along the way the provider also emits `TranscriptionChunkEvent` / `TranscriptionCompletedEvent` (your speech), `ModelMessageChunk` (assistant text), `ToolCallEvent`/`ToolResultEvent`, and `UsageEvent`. `agent.run()` is an async context manager that **yields the shared `ConversationContext`** so you can attach the recorder and player to it.

## Minimal recipe — full duplex voice loop **[needs keys + audio]**

```python title="voice_loop.py"
import asyncio

from ag2.live import (
    LiveAgent,
    OpenAIRealTimeConfig,
    SoundDevicePlayer,
    SoundDeviceRecorder,
)
from ag2.live.openai import AudioOutput, InputConfig

agent = LiveAgent(
    "voice_bot",
    "You are a friendly realtime voice assistant. Keep replies short and conversational.",
    config=OpenAIRealTimeConfig(
        "gpt-realtime",
        output=AudioOutput(voice="marin"),
        # Transcribe the user's speech so we can see it too.
        input=InputConfig(transcription={"model": "gpt-4o-mini-transcribe"}),
    ),
)

async def main() -> None:
    # run() yields the shared ConversationContext.
    async with agent.run() as ctx:
        # Recorder and player bind to the SAME context so audio flows on one bus.
        async with (
            SoundDeviceRecorder(context=ctx),   # mic  -> RecordedAudioEvent
            SoundDevicePlayer(context=ctx),     # SynthesizedAudioEvent -> speaker
        ):
            print("Talk to the agent. Ctrl-C to stop.")
            await asyncio.Event().wait()  # keep the session open

if __name__ == "__main__":
    asyncio.run(main())
```

Set `OPENAI_API_KEY` (or pass `client=AsyncOpenAI(...)`). For Gemini, set `GEMINI_API_KEY` / `GOOGLE_API_KEY` (or pass `client=`).

Swap to Gemini by changing only the config:

```python
from ag2.live import GeminiRealTimeConfig
from ag2.live.gemini import AudioOutput, InputConfig

agent = LiveAgent(
    "voice_bot",
    "You are a friendly realtime voice assistant.",
    config=GeminiRealTimeConfig(
        "gemini-2.5-flash-native-audio-preview-12-2025",
        output=AudioOutput(voice="Kore"),
        input=InputConfig(transcribe=True),   # user-speech transcription
    ),
)
```

## Provider matrix — Gemini Live vs OpenAI Realtime

| | Gemini (`GeminiRealTimeConfig`) | OpenAI (`OpenAIRealTimeConfig`) |
|---|---|---|
| Import the knob types from | `ag2.live.gemini` | `ag2.live.openai` |
| Output modes | `AudioOutput` (default) / `TextOutput` | `AudioOutput` (default) / `TextOutput` |
| Voices (`AudioOutput(voice=...)`) | `Aoede`, `Charon`, `Fenrir`, `Kore` (default), `Leda`, `Orus`, `Puck`, `Zephyr` | `alloy` (default), `ash`, `ballad`, `coral`, `echo`, `sage`, `shimmer`, `verse`, `marin`, `cedar` |
| Example models (first positional arg) | `gemini-2.5-flash-native-audio-preview-12-2025`, `gemini-3.1-flash-live-preview`, `gemini-live-2.5-flash-preview`, `gemini-2.0-flash-live-001` | `gpt-realtime`, `gpt-realtime-mini`, `gpt-audio-1.5`, `gpt-4o-realtime-preview-2024-10-01` |
| User-speech transcription | `InputConfig(transcribe=True, transcription_languages=[...])` | `InputConfig(transcription={"model": "gpt-4o-mini-transcribe"})` |
| Turn detection / VAD | `InputConfig(automatic_activity_detection=..., activity_handling=..., turn_coverage=...)` | `InputConfig(turn_detection={"type": "semantic_vad", ...})` (default on) |
| Audio I/O sample rate | in 16 kHz / out 24 kHz (fixed by API) | configurable via `AudioOutput.format` / `InputConfig.format` (default PCM 24 kHz) |
| Generation knobs | `temperature=`, `max_output_tokens=` | `max_output_tokens=` (int or `"inf"`), `tool_choice=`, `tracing=` |
| Escape hatch for raw provider config | `config=<LiveConnectConfigDict>` | `session=<RealtimeSessionCreateRequestParam>` |
| Bring your own client | `client=google.genai.Client(...)` | `client=openai.AsyncOpenAI(...)` |

The model name is a **positional** arg; everything else is keyword-only. The `Literal` model/voice lists above are accepted, but any string is allowed too (forward-compatible with new models).

`AudioOutput` adds `language_code=` (Gemini) and `speed=` + `format=` (OpenAI). `TextOutput()` takes no args — it returns text only (`ModelMessageChunk`), no audio playback.

### Tools, HITL, observers on a `LiveAgent`

`LiveAgent(...)` accepts the same surface as a normal `Agent`: `tools=`, `hitl_hook=`, `middleware=`, `observers=`, `dependencies=`, `variables=`, `plugins=`. Tool calls from the model arrive as `ToolCallEvent` and results are forwarded back into the session automatically. `run()` can override `config=`, `prompt=`, `tools=`, `observers=`, `hitl_hook=` per session.

> **Note:** `LiveAgent` only supports function tools over realtime — provider server-side tool *types* raise `NotImplementedError` in both backends.

## Audio I/O — `SoundDeviceRecorder` / `SoundDevicePlayer`

Both are async context managers (`async with`) and bind to a `ConversationContext` via `context=`. The device opens on `__aenter__` **[needs audio]**; construction alone touches no hardware.

```python
from ag2.live import SoundDeviceRecorder, SoundDevicePlayer

# Recorder: mic -> RecordedAudioEvent on the stream.
recorder = SoundDeviceRecorder(context=ctx, sample_rate=16000, channels=1)  # block_size optional

# Player: subscribes to SynthesizedAudioEvent, writes PCM to the speaker (24 kHz mono).
player = SoundDevicePlayer(context=ctx)
```

`SoundDeviceRecorder` also has a **one-shot blocking** `record(duration)` that returns a `VoiceInput` (16-bit PCM) — handy to feed the STT pipeline below without running a live session:

```python
voice = recorder.record(duration=4.0)   # blocks 4s, returns VoiceInput  [needs audio]
```

## One-shot speech-to-text + voice pipeline **[needs keys; audio only if recording live]**

`OpenAITranscriber` (transcription) and `OpenAITranslationTranscriber` (translate to English) take a `VoiceInput` and return text. `.pipe(agent)` builds a `VoicePipeline` that transcribes then runs a normal text `Agent`:

```python
from ag2 import Agent
from ag2.config import OpenAIConfig
from ag2.live import OpenAITranscriber, SoundDeviceRecorder
from ag2.live.stt import VoiceInput

agent = Agent("assistant", "You answer questions.", config=OpenAIConfig(model="gpt-4o-mini"))
pipeline = OpenAITranscriber("gpt-4o-transcribe").pipe(agent)

# Record a clip, transcribe it, and ask the agent — in one call.  [needs keys + audio]
voice: VoiceInput = SoundDeviceRecorder().record(4.0)
reply = await pipeline.ask(voice)
print(await reply.content())

# Continue the conversation with another clip:
followup = await reply.ask(SoundDeviceRecorder().record(4.0))
```

`VoiceInput(content: bytes, frame_rate: int, channels: int)` wraps 16-bit PCM; build it directly from any PCM source if you aren't using the recorder.

`OpenAITranslationTranscriber` is identical but always outputs English (useful for non-English speech in → English text out).

## Text-to-speech — `OpenAITTSConfig` and `TTSObserver`

`OpenAITTSConfig` synthesizes text into PCM bytes:

```python
from ag2.live import OpenAITTSConfig

tts = OpenAITTSConfig("gpt-4o-mini-tts", voice="alloy", speed=1.0)
pcm: bytes = await tts.synthesize("Hello there!")   # [needs keys]
```

`TTSObserver` turns any **text** streaming `Agent` into a talking one: attach it as an observer and it accumulates `ModelMessageChunk` tokens, synthesizes complete sentences, and emits `SynthesizedAudioEvent` — which a `SoundDevicePlayer` on the same stream plays aloud. This is the bridge between a normal text agent and live audio output (no realtime provider needed).

```python
from ag2 import Agent, MemoryStream
from ag2.config import OpenAIConfig
from ag2.context import ConversationContext
from ag2.live import OpenAITTSConfig, SoundDevicePlayer, TTSObserver

agent = Agent(
    "narrator",
    "You are a helpful assistant.",
    config=OpenAIConfig(model="gpt-4o-mini"),
    observers=[TTSObserver(OpenAITTSConfig("gpt-4o-mini-tts", voice="nova"))],
)

# Agent and Player must share ONE stream: agent.ask(stream=...) and
# SoundDevicePlayer(context=...) whose .stream is that same stream.
stream = MemoryStream()
ctx = ConversationContext(stream=stream)

async with SoundDevicePlayer(context=ctx):     # plays the synthesized speech  [needs audio]
    reply = await agent.ask("Tell me a one-line joke.", stream=stream)
    print(await reply.content())
```

`TTSObserver(config)` returns a `CompositeObserver`. It flushes any remaining buffered text on the final `ModelMessage`, so trailing partial sentences are still spoken.

## Usage reporting

`await LiveAgent.usage_report(ctx)` aggregates token usage over the live session's event log into a `UsageReport`.

## What is construction-tested vs needs live keys + hardware

Run by this skill's `references/test_samples.py` (all green) — **constructed / exercised offline**:

- All 9 public symbols import as real classes (not missing-dependency placeholders).
- `GeminiRealTimeConfig` / `OpenAIRealTimeConfig` construction with `AudioOutput` / `TextOutput` / `InputConfig`, voices, temperature/max-tokens, and `_build_session(instructions=...)` merge.
- `OpenAITTSConfig`, `OpenAITranscriber`, `OpenAITranslationTranscriber` construction.
- `LiveAgent` construction (string prompt, list prompt, with `tools=`/`observers=`).
- `SoundDeviceRecorder` / `SoundDevicePlayer` construction (no device opened).
- `TTSObserver` returns a `CompositeObserver`.
- `OpenAITranscriber(...).pipe(agent)` → `VoicePipeline`; `VoiceInput` dataclass.

**Requires real API keys + microphone/speaker (NOT runnable headless):**

- `agent.run()` opening a live websocket to Gemini/OpenAI.
- `async with SoundDeviceRecorder(...)` / `SoundDevicePlayer(...)` (opens the sound card on `__aenter__`).
- `SoundDeviceRecorder.record(duration)` (blocks on the mic).
- `OpenAITTSConfig.synthesize(...)`, `OpenAITranscriber.transcribe(...)`, and `VoicePipeline.ask(...)` (hit the OpenAI API).

## Common pitfalls

- **Wrong extra installed.** `pip install "ag2[openai-realtime]"` for OpenAI, `"ag2[gemini-realtime]"` for Gemini, `"sounddevice[numpy]"` for local audio. The symbol imports fine but raises on first use otherwise.
- **`numpy` missing.** `SoundDeviceRecorder` / `SoundDevicePlayer` need numpy — `sounddevice[numpy]` pulls it in. Without it you get an additional-dependency `ImportError`.
- **Importing knob types from the wrong module.** `AudioOutput` / `TextOutput` / `InputConfig` are provider-specific: `ag2.live.gemini` vs `ag2.live.openai`. They are *not* re-exported from `ag2.live`.
- **Recorder, player, and `run()` not sharing one context.** Pass the `ctx` yielded by `agent.run()` into `SoundDeviceRecorder(context=ctx)` / `SoundDevicePlayer(context=ctx)`, or audio events won't reach the session/speaker.
- **Voice not in the provider's list.** Gemini and OpenAI have different voice names (see matrix). A bare string is accepted but an unknown voice is rejected by the provider at session open.
- **Expecting server-side / provider tools over realtime.** Only function tools are supported; other tool types raise `NotImplementedError`.
- **Forgetting to keep the loop alive.** `agent.run()` is a context manager — exit closes the session. Keep it open (`await asyncio.Event().wait()` or your own loop) for a continuous conversation.

## Going deeper

- Source: `ag2/live/{realtime.py,gemini.py,openai.py,protocols.py,observer.py,stt.py,sound_device.py}`.
- `realtime.py` — `LiveAgent` + the `RealtimeConfig` protocol.
- For sending a recorded audio *file* as a one-off input to a text agent, see `ag2-multimodal-input` (`AudioInput`).
- For observers in general (token monitors, loop detection), see `ag2-observers-and-alerts`.
