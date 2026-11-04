# `ag2.live` reference (AG2 1.3)

## Provider support

| Provider | STT | Streaming STT | TTS | Streaming TTS | Native S2S | Install / key |
|---|---|---|---|---|---|---|
| OpenAI | `OpenAITranscriber`, `OpenAITranslationTranscriber` | transcription deltas | `OpenAITTSConfig` (buffered only) | — | `OpenAIRealTimeConfig` | `ag2[openai]`, `OPENAI_API_KEY` |
| ElevenLabs | `ElevenLabsTranscriber` (Scribe) | — | `ElevenLabsTTSConfig` | `ElevenLabsStreamingTTSConfig` | — | `ag2[elevenlabs]`, `ELEVENLABS_API_KEY` |
| Mistral (Voxtral) | `MistralTranscriber`, `MistralRealtimeTranscriber` | realtime deltas | `MistralTTSConfig` | `MistralStreamingTTSConfig`, `MistralRealtimeTTSConfig` (text-streaming) | — | `ag2[mistral]` (+ `mistralai[realtime]` for `Realtime*`), `MISTRAL_API_KEY` |
| Gemini | — | — | — | — | `GeminiRealTimeConfig` | `ag2[gemini]`, `GEMINI_API_KEY`/`GOOGLE_API_KEY` |

All exported from `ag2.live` (plus `LiveAgent`, `CascadeConfig`, `TTSObserver`, `SilenceTurnDetector`, `TurnDetector`, `SoundDeviceRecorder`, `SoundDevicePlayer`). Provider modules `ag2.live.openai`, `ag2.live.gemini`, `ag2.live.elevenlabs`, `ag2.live.mistral` expose the same classes under short names (`openai.RealTimeConfig`, `openai.STTConfig`, `openai.TTSConfig`, …) plus option types (`AudioOutput`, `TextOutput`, `InputConfig`) that are **not** re-exported from `ag2.live`. `VoiceInput`, `VoicePipeline`, `VoiceReply`, `STTConfig` live in `ag2.live.stt`.

## Constructors

### OpenAI (`ag2.live.openai`)

- `OpenAITranscriber(model, *, client=None, api_key=None)` — e.g. `"gpt-4o-mini-transcribe"`, `"gpt-4o-transcribe"`; streams `TranscriptionChunkEvent`s then `TranscriptionCompletedEvent`.
- `OpenAITranslationTranscriber(model="whisper-1", *, client=None, api_key=None)` — OpenAI's translations endpoint is deprecated in the SDK.
- `OpenAITTSConfig(model, *, client=None, api_key=None, voice="alloy", speed=omit)` — `"gpt-4o-mini-tts"`, `"tts-1"`; `await tts.synthesize(text) -> bytes` (24 kHz mono int16 PCM).
- `OpenAIRealTimeConfig(model, *, output=AudioOutput(), input=InputConfig(), max_output_tokens=, tool_choice=, tracing=, session=<raw RealtimeSessionCreateRequestParam>, client=None, api_key=None)`.
  - `AudioOutput(voice="alloy", format={"type": "audio/pcm", "rate": 24000}, speed=1.0)`; voices `alloy ash ballad coral echo sage shimmer verse marin cedar`.
  - `TextOutput()` — text only (`ModelMessageChunk` + `ModelResponse`), no audio.
  - `InputConfig(format=pcm 24 kHz, transcription=None, noise_reduction=None, turn_detection={"type": "semantic_vad", "create_response": True, "interrupt_response": True})`. For synthetic/headless audio use `{"type": "server_vad", "silence_duration_ms": 500}` — semantic VAD may never close a turn on digital silence.
  - Models: `"gpt-realtime"`, `"gpt-realtime-mini"`, `"gpt-realtime-2"`, … (any string accepted).

### Gemini (`ag2.live.gemini`)

`GeminiRealTimeConfig(model, *, output=AudioOutput(), input=InputConfig(), temperature=, max_output_tokens=, config=<raw LiveConnectConfigDict>, client=google.genai.Client(...))`.
- Models (Literal hints): `"gemini-3.8-live"`, `"gemini-3.8-live-extended-thinking"`; older ids like `"gemini-2.5-flash-native-audio-preview-12-2025"` still work as strings.
- `AudioOutput(voice="Kore", language_code=None)`; voices `Aoede Charon Fenrir Kore Leda Orus Puck Zephyr`. `TextOutput()` is rejected by `gemini-3.8-live` (audio-only; read the text from the transcript).
- `InputConfig(transcribe=False, transcription_languages=..., automatic_activity_detection=..., activity_handling=..., turn_coverage=...)`.
- Audio is fixed: **16 kHz in**, 24 kHz out → `SoundDeviceRecorder(context=ctx, sample_rate=16000)`. User speech arrives only as `TranscriptionChunkEvent`s.

### ElevenLabs (`ag2.live.elevenlabs`) — key from `ELEVENLABS_API_KEY` or `client=AsyncElevenLabs(...)`; no `api_key=`

- `ElevenLabsTranscriber(model_id="scribe_v2", *, language_code=, diarize=False, num_speakers=, tag_audio_events=, keyterms=[...], client=)`. Scribe inserts `(laughter)`-style tags unless `tag_audio_events=False`.
- `ElevenLabsTTSConfig(model_id="eleven_v3", *, voice_id=<Rachel>, output_format="pcm_24000", voice_settings=VoiceSettings(...), language_code=, seed=, client=)` — buffered; required for `eleven_v3`.
- `ElevenLabsStreamingTTSConfig(model_id="eleven_flash_v2_5", ...same...)` — `async for chunk in tts.stream(text)`; preferred for flash/turbo.
- `output_format` must be raw PCM — the player does not decode mp3.

### Mistral (`ag2.live.mistral`, `ag2.live.mistral_realtime`) — `MISTRAL_API_KEY` or `api_key=` / `client=`

- `MistralTranscriber(model="voxtral-mini-latest", *, language=None, context_bias=(), diarize=False)` — uploads WAV, one `TranscriptionCompletedEvent`.
- `MistralRealtimeTranscriber(model="voxtral-mini-transcribe-realtime-2602", *, target_streaming_delay_ms=None)` — mono only; websocket; deltas then completed; raises `RuntimeError` if the connection drops.
- `MistralTTSConfig(model="voxtral-mini-tts-2603", *, voice_id=None, ref_audio=None)` / `MistralStreamingTTSConfig(...)` — exactly one of `voice_id` (saved voice) or `ref_audio` (base64 reference clip, e.g. a base64 WAV) — else `ValueError`. Output converted to int16 PCM.
- `MistralRealtimeTTSConfig(model="voxtral-mini-tts-3", *, voice_id=, ref_audio=, streaming_mode="responsive"|"long_form", expressiveness=>=1)` — consumes the reply text as it streams over one connection per reply (`min_chars` does not apply).

## TTS protocols (`ag2.live.protocols`)

- `TTSConfig[bytes]`: `async synthesize(text) -> bytes`.
- `StreamingTTSConfig`: + `stream(text) -> AsyncIterator[bytes]` — `TTSObserver`/`CascadeConfig` detect it and forward chunks (playback at time-to-first-byte).
- `TextStreamingTTSConfig`: + `stream_text(AsyncIterator[str]) -> AsyncIterator[bytes]` (Mistral realtime).

Implement `synthesize` on your own class to plug any TTS in.

## `TTSObserver(config, *, min_chars=60) -> CompositeObserver`

Buffers `ModelMessageChunk`s into sentences ≥ `min_chars`, synthesizes, emits `SynthesizedAudioEvent` on the same stream; flushes on `ModelMessage`. The agent should stream (`streaming=True`). Pass the player's stream: `agent.ask(..., stream=player.stream)`.

## Turn-by-turn pipeline

`stt.pipe(agent) -> VoicePipeline`; `await pipeline.ask(voice, stream=None, dependencies=, variables=, prompt=, config=, tools=, middleware=, observers=, hitl_hook=) -> VoiceReply`; `VoiceReply` has `.body`, `await .content()`, `.response`, `.history`, `.files`, and `await .ask(next_voice)`.

## `LiveAgent(name, prompt=(), *, config, hitl_hook=None, tools=(), middleware=(), observers=(), dependencies=None, variables=None, plugins=(), stream=None)`

- `async with agent.run(*, dependencies=, variables=, prompt=, config=, tools=, middleware=, observers=, hitl_hook=) as context:` — tools/middleware/observers are added; `config`/`prompt`/`hitl_hook` replace. Each `run()` opens a fresh `MemoryStream` unless `stream=` was given to the constructor.
- `@agent.tool` works; tool calls go through AG2's executor and results are sent back into the session.
- `await LiveAgent.usage_report(context)` → `UsageReport` (`.total`, `.by_model`, `.by_provider`, `.by_kind`).
- Consume events: `with context.stream.where(ModelMessageChunk | TranscriptionChunkEvent).join() as events: async for e in events: ...`.

## `CascadeConfig(*, stt, model, tts, turn_detector=SilenceTurnDetector, barge_in=False, min_chars=60)`

- `turn_detector` is a **factory** (`Callable[[], TurnDetector]`), e.g. `lambda: SilenceTurnDetector(threshold=500.0)`.
- Half-duplex by default: mic audio is ignored between `AudioPlaybackStartedEvent` and `AudioPlaybackCompletedEvent` (emitted by `SoundDevicePlayer`). `barge_in=True` cancels the in-flight turn on speech and sends `AudioInterruptedEvent` (player drops queued audio) — headphones only; no echo cancellation.
- Raw audio is stripped from history before each model call; tool loop capped at 10 per turn (`RuntimeError` beyond).

### `SilenceTurnDetector(*, sample_rate=24000, channels=1, threshold=500.0, silence=0.7, min_speech=0.2, prefix_padding=0.3, max_duration=30.0)`

RMS energy gate. `push(chunk) -> VoiceInput | None` returns the utterance when a turn closes; `.speaking`; `reset()`. Implement the `TurnDetector` protocol (`speaking`, `push`, `reset`) to plug in a real VAD.

## Audio I/O (`sounddevice[numpy]`)

- `SoundDeviceRecorder(*, context=None, sample_rate=24000, channels=1, block_size=None)` — `async with` streams 100 ms `RecordedAudioEvent`s; `record(duration) -> VoiceInput` blocks (one-shot).
- `SoundDevicePlayer(*, context=None, output_stream=None)` — `async with` plays `SynthesizedAudioEvent`s on a worker thread; `await play(bytes)` enqueues; `.stream`; `clear()`; exiting the block waits for playback to drain (`join()` does not).
- Construction touches no hardware; devices open on `__aenter__`.

## Events (`ag2.events`)

`RecordedAudioEvent(bytes)` mic → session · `SynthesizedAudioEvent(bytes)` → player · `TranscriptionChunkEvent(str)` / `TranscriptionCompletedEvent(str)` user speech · `ModelMessageChunk` / `ModelResponse` assistant text (realtime) · `AudioPlaybackStartedEvent` / `AudioPlaybackCompletedEvent` (player) · `AudioInterruptedEvent` (barge-in) · `ToolCallEvent` / `ToolResultEvent` · `UsageEvent`.

## Headless testing

Push `RecordedAudioEvent` chunks into `context` and subscribe to output events — see `../scripts/headless_voice_check.py`. Verified against 1.3.0: cascade (OpenAI STT + gpt-4.1-mini + OpenAI TTS, with a tool), OpenAI realtime (`gpt-realtime-mini`, server VAD), Gemini Live (`gemini-3.8-live`, 16 kHz input), Mistral STT/TTS (all five classes), OpenAI TTS/STT/pipeline, `TTSObserver`.
