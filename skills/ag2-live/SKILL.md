---
name: ag2-live
description: "Build voice agents with AG2's `ag2.live` module. Three patterns: (1) turn-by-turn STT → `Agent` → TTS — `OpenAITranscriber` / `ElevenLabsTranscriber` / `MistralTranscriber` `.pipe(agent)` (`VoicePipeline`, `VoiceInput`) plus `TTSObserver` with `OpenAITTSConfig`, `ElevenLabsStreamingTTSConfig`, `MistralTTSConfig` and friends; (2) realtime speech-to-speech `LiveAgent` with `OpenAIRealTimeConfig` or `GeminiRealTimeConfig` (`AudioOutput` / `TextOutput` / `InputConfig`, voices, VAD, tools); (3) `CascadeConfig` — any STT + any LLM + any TTS behind `LiveAgent`, with `SilenceTurnDetector` and barge-in. Audio I/O via `SoundDeviceRecorder` / `SoundDevicePlayer`; headless testing by pushing `RecordedAudioEvent`s. Use for talking agents, voice assistants, live captions, or speaking a text agent's reply. For sending one audio file into a text agent see `ag2-multimodal-input`."
license: Apache-2.0
---

# Voice and realtime agents (`ag2.live`)

## Pick a pattern

| Pattern | Build with | Latency | Use when |
|---|---|---|---|
| Turn-by-turn | `Agent` + `<Transcriber>.pipe(agent)` + `TTSObserver` | 1–3 s/turn | Add voice to an existing text agent; need structured output or other text-agent features |
| Realtime S2S | `LiveAgent` + `OpenAIRealTimeConfig` / `GeminiRealTimeConfig` | <500 ms | Barge-in, server VAD, phone-call UX |
| Cascade | `LiveAgent` + `CascadeConfig(stt=, model=, tts=)` | between | Continuous session but a specific voice/LLM/provider mix |

Patterns 2 and 3 differ only in the `config=` line. Recorded *file* into a text agent → `ag2-multimodal-input`.

## Install and keys

```bash
pip install "ag2[openai]" "sounddevice[numpy]"     # OpenAI STT/TTS/Realtime + local mic/speaker
pip install "ag2[gemini]"                          # Gemini Live          (GEMINI_API_KEY / GOOGLE_API_KEY)
pip install "ag2[elevenlabs]"                      # ElevenLabs           (ELEVENLABS_API_KEY)
pip install "ag2[mistral]" "mistralai[realtime]"   # Mistral Voxtral      (MISTRAL_API_KEY); [realtime] for the Realtime* classes
```

No `ag2[openai-realtime]` / `ag2[gemini-realtime]` extras exist. A missing SDK leaves a placeholder raising `ImportError` on first use. OpenAI classes take `api_key=` **or** `client=` (not both).

## 1. Turn-by-turn (STT → Agent → TTS)

```python
from ag2 import Agent, MemoryStream
from ag2.config import OpenAIResponsesConfig
from ag2.context import ConversationContext
from ag2.live import OpenAITTSConfig, OpenAITranscriber, SoundDevicePlayer, SoundDeviceRecorder, TTSObserver

agent = Agent(
    "assistant", "You are a helpful voice assistant. Be brief.",
    config=OpenAIResponsesConfig(model="gpt-4.1-mini", streaming=True),   # streaming -> sentence-level TTS
    observers=[TTSObserver(OpenAITTSConfig("gpt-4o-mini-tts", voice="nova"))],
)
pipeline = OpenAITranscriber("gpt-4o-mini-transcribe").pipe(agent)
recorder = SoundDeviceRecorder()
stream = MemoryStream()            # one stream for the conversation, one player per turn
reply = None
for _ in range(3):
    voice = recorder.record(duration=5)                          # blocks -> VoiceInput
    async with SoundDevicePlayer(context=ConversationContext(stream)):   # exit waits for playback to end
        reply = await (pipeline.ask(voice, stream=stream) if reply is None else reply.ask(voice))
    print(reply.body)
```

`VoiceInput(pcm16_bytes, frame_rate, channels)` works from any PCM source. Swap providers freely: `ElevenLabsTranscriber("scribe_v2")`, `MistralTranscriber("voxtral-mini-latest")`, `ElevenLabsStreamingTTSConfig("eleven_flash_v2_5")` (streams → playback at time-to-first-byte), `MistralTTSConfig(voice_id=...)` (needs `voice_id` or `ref_audio`).

## 2. Realtime speech-to-speech

```python
import asyncio
from ag2.live import LiveAgent, SoundDevicePlayer, SoundDeviceRecorder, openai

agent = LiveAgent(
    "assistant", prompt="You are a helpful voice assistant.",
    config=openai.RealTimeConfig(
        "gpt-realtime-mini",
        output=openai.AudioOutput(voice="marin"),                         # or openai.TextOutput()
        input=openai.InputConfig(transcription={"model": "gpt-4o-mini-transcribe"}),
    ),
)  # tools: decorate functions with @agent.tool, same as Agent

async def main() -> None:
    async with (
        agent.run() as context,                 # yields the shared ConversationContext
        SoundDevicePlayer(context=context),     # SynthesizedAudioEvent -> speaker
        SoundDeviceRecorder(context=context),   # mic -> RecordedAudioEvent (24 kHz default)
    ):
        await asyncio.Future()                  # keep the session open
```

Gemini: `from ag2.live import gemini` → `gemini.RealTimeConfig("gemini-3.8-live", output=gemini.AudioOutput(voice="Puck"), input=gemini.InputConfig(transcribe=True))` and **`SoundDeviceRecorder(context=context, sample_rate=16000)`** (Gemini input is fixed at 16 kHz).

## 3. Cascade

```python
from ag2.config import OpenAIResponsesConfig
from ag2.live import CascadeConfig, ElevenLabsStreamingTTSConfig, LiveAgent, OpenAITranscriber, SilenceTurnDetector

agent = LiveAgent("assistant", prompt="Answer in one or two short sentences.", config=CascadeConfig(
    stt=OpenAITranscriber("gpt-4o-mini-transcribe"),
    model=OpenAIResponsesConfig("gpt-4.1-mini", streaming=True),   # any ModelConfig
    tts=ElevenLabsStreamingTTSConfig("eleven_flash_v2_5"),           # any TTS config
    turn_detector=lambda: SilenceTurnDetector(threshold=500.0, silence=0.7),  # a factory, not an instance
    barge_in=False,                                                  # True only with headphones
))
```

Run it exactly like pattern 2. Tools work (≤10 tool round-trips per turn).

## Testing without a mic

Everything is events on `context.stream`: push `RecordedAudioEvent(pcm_chunk)` into a running session (synthesize the question with `OpenAITTSConfig(...).synthesize(text)` → 24 kHz PCM) and subscribe to `SynthesizedAudioEvent` / `ModelResponse` instead of opening devices. `scripts/headless_voice_check.py` does this for a cascade end to end.

## Pitfalls

- **Recorder, player and `run()` must share one context** (pass `context=context`), or audio never reaches the session/speaker.
- **Never attach `TTSObserver` to something that already speaks** (`AudioOutput` S2S or `CascadeConfig`) → two voices. It belongs on a text `Agent` or a `LiveAgent` with `TextOutput()`.
- **`player.join()` does not wait for playback** — use one `async with SoundDevicePlayer()` per speaking turn, or the mic records the bot's own voice.
- **Realtime turn ends with `ModelResponse`** (text via `ModelMessageChunk`), not `ModelMessage`. Gemini sends only `TranscriptionChunkEvent`s for user speech.
- **Sample rates**: player/recorder/OpenAI default 24 kHz mono int16; Gemini in 16 kHz; ElevenLabs `output_format` must stay raw PCM (`pcm_24000` default).
- **Option types are provider-module only**: `openai.AudioOutput` vs `gemini.AudioOutput` — not exported from `ag2.live`. `gemini-3.8-live` rejects `TextOutput`.
- **Only function tools** over realtime; server-side tool types raise `NotImplementedError`. No structured output on `LiveAgent` — hand off to a text agent via a tool wrapping `Agent.as_tool()`.
- **`OpenAITranslationTranscriber`** uses OpenAI's SDK-deprecated translations endpoint; in testing `whisper-1` returned French untranslated. Prefer a transcriber + an English-answering prompt.

## Reference

`references/live_reference.md` — every constructor and knob, voices/models, events, `CascadeConfig`/`SilenceTurnDetector` parameters, streaming TTS protocols, `LiveAgent.run()` overrides and `usage_report`. Upstream: `website/docs/user-guide/live/{live,stt_tts,live_agent}.mdx`, `examples/live_playground/`.
