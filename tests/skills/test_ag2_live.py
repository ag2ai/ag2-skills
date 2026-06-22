# Copyright (c) 2026, AG2ai, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Construction + import test for every public symbol and config in the ag2-live skill.

Live sockets / mic / speaker are NOT opened here. We construct configs and
objects and exercise their pure-Python build paths as far as possible without
network or audio hardware.
"""

import os

# Dummy keys so OpenAI/Gemini clients construct without env errors.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-dummy")
os.environ.setdefault("GEMINI_API_KEY", "test-dummy")
os.environ.setdefault("GOOGLE_API_KEY", "test-dummy")


def test_public_api_imports():
    from autogen.beta.live import (
        GeminiRealTimeConfig,
        LiveAgent,
        OpenAIRealTimeConfig,
        OpenAITTSConfig,
        OpenAITranscriber,
        OpenAITranslationTranscriber,
        SoundDevicePlayer,
        SoundDeviceRecorder,
        TTSObserver,
    )

    # None of these should be the "missing dependency" placeholder.
    for sym in (
        GeminiRealTimeConfig,
        LiveAgent,
        OpenAIRealTimeConfig,
        OpenAITTSConfig,
        OpenAITranscriber,
        OpenAITranslationTranscriber,
        SoundDevicePlayer,
        SoundDeviceRecorder,
        TTSObserver,
    ):
        assert sym is not None
    print("OK: public API imports")


def test_gemini_realtime_config():
    from autogen.beta.live import GeminiRealTimeConfig
    from autogen.beta.live.gemini import AudioOutput, InputConfig, TextOutput

    # Default = audio out, Kore voice.
    cfg = GeminiRealTimeConfig("gemini-2.5-flash-native-audio-preview-12-2025")
    assert cfg.model == "gemini-2.5-flash-native-audio-preview-12-2025"
    assert "speech_config" in cfg._config

    # Explicit audio + transcription of user speech.
    cfg2 = GeminiRealTimeConfig(
        "gemini-2.5-flash-native-audio-preview-12-2025",
        output=AudioOutput(voice="Puck", language_code="en-US"),
        input=InputConfig(transcribe=True, transcription_languages=["en-US"]),
        temperature=0.7,
        max_output_tokens=1024,
    )
    assert cfg2._config["temperature"] == 0.7
    assert "input_audio_transcription" in cfg2._config

    # Text-only modality.
    cfg3 = GeminiRealTimeConfig("gemini-3.1-flash-live-preview", output=TextOutput())
    from google.genai import types as gtypes

    assert cfg3._config["response_modalities"] == [gtypes.Modality.TEXT]

    # _build_session merges instructions (no socket).
    built = cfg2._build_session(instructions=["You are helpful."])
    assert built["system_instruction"] == "You are helpful."
    print("OK: GeminiRealTimeConfig construction + _build_session")


def test_openai_realtime_config():
    from autogen.beta.live import OpenAIRealTimeConfig
    from autogen.beta.live.openai import AudioOutput, InputConfig, TextOutput

    cfg = OpenAIRealTimeConfig("gpt-realtime")
    assert cfg.model == "gpt-realtime"
    assert cfg._session["output_modalities"] == ["audio"]

    cfg2 = OpenAIRealTimeConfig(
        "gpt-realtime",
        output=AudioOutput(voice="marin", speed=1.1),
        input=InputConfig(
            transcription={"model": "gpt-4o-mini-transcribe"},
            turn_detection={"type": "semantic_vad", "create_response": True, "interrupt_response": True},
        ),
        max_output_tokens="inf",
    )
    assert cfg2._session["audio"]["output"]["voice"] == "marin"
    assert cfg2._session["audio"]["input"]["transcription"]["model"] == "gpt-4o-mini-transcribe"

    cfg3 = OpenAIRealTimeConfig("gpt-realtime", output=TextOutput())
    assert cfg3._session["output_modalities"] == ["text"]

    built = cfg2._build_session(instructions=["Be concise."])
    assert built["instructions"] == "Be concise."
    print("OK: OpenAIRealTimeConfig construction + _build_session")


def test_openai_tts_and_stt():
    from autogen.beta.live import (
        OpenAITranscriber,
        OpenAITranslationTranscriber,
        OpenAITTSConfig,
    )

    tts = OpenAITTSConfig("gpt-4o-mini-tts", voice="alloy", speed=1.0)
    assert tts._model == "gpt-4o-mini-tts"
    assert tts._voice == "alloy"

    stt = OpenAITranscriber("gpt-4o-transcribe")
    assert stt.model == "gpt-4o-transcribe"

    tr = OpenAITranslationTranscriber("whisper-1")
    assert tr.model == "whisper-1"
    print("OK: OpenAITTSConfig / OpenAITranscriber / OpenAITranslationTranscriber")


def test_live_agent_construction():
    from autogen.beta.live import GeminiRealTimeConfig, LiveAgent

    agent = LiveAgent(
        "voice_bot",
        "You are a friendly realtime voice assistant. Keep replies short.",
        config=GeminiRealTimeConfig("gemini-2.5-flash-native-audio-preview-12-2025"),
    )
    assert agent.name == "voice_bot"
    # tools=, observers= etc. accepted.
    agent2 = LiveAgent(
        "voice_bot2",
        ["Be concise.", "Be polite."],
        config=GeminiRealTimeConfig("gemini-2.5-flash-native-audio-preview-12-2025"),
        tools=[],
        observers=[],
    )
    assert agent2.name == "voice_bot2"
    print("OK: LiveAgent construction")


def test_sound_device_objects():
    from autogen.beta.live import SoundDevicePlayer, SoundDeviceRecorder

    # Construction only; __aenter__ would open a device (not done here).
    rec = SoundDeviceRecorder(sample_rate=16000, channels=1)
    assert rec.sample_rate == 16000
    assert rec.block_size == int(16000 * 0.1)

    player = SoundDevicePlayer()
    assert player.stream is not None
    print("OK: SoundDeviceRecorder / SoundDevicePlayer construction")


def test_tts_observer():
    from autogen.beta.live import OpenAITTSConfig, TTSObserver
    from autogen.beta.observers import CompositeObserver

    obs = TTSObserver(OpenAITTSConfig("gpt-4o-mini-tts"))
    assert isinstance(obs, CompositeObserver)
    print("OK: TTSObserver wraps a TTSConfig into a CompositeObserver")


def test_tts_observer_on_text_agent():
    # The TTSObserver-on-a-text-agent recipe: shared stream wiring + nova voice.
    from autogen.beta import Agent, MemoryStream
    from autogen.beta.config import OpenAIConfig
    from autogen.beta.context import ConversationContext
    from autogen.beta.live import OpenAITTSConfig, SoundDevicePlayer, TTSObserver

    agent = Agent(
        "narrator",
        "You are a helpful assistant.",
        config=OpenAIConfig(model="gpt-4o-mini"),
        observers=[TTSObserver(OpenAITTSConfig("gpt-4o-mini-tts", voice="nova"))],
    )
    assert agent.name == "narrator"

    stream = MemoryStream()
    ctx = ConversationContext(stream=stream)
    player = SoundDevicePlayer(context=ctx)
    # Player's stream is the SAME stream the agent would publish to.
    assert player.stream is stream
    # usage_report is a static method on LiveAgent.
    from autogen.beta.live import LiveAgent

    assert hasattr(LiveAgent, "usage_report")
    print("OK: TTSObserver-on-text-agent shared-stream wiring + nova voice")


def test_stt_pipe_to_agent():
    # STTConfig.pipe(agent) -> VoicePipeline. Construct without transcribing.
    from autogen.beta import Agent
    from autogen.beta.config import OpenAIConfig
    from autogen.beta.live import OpenAITranscriber
    from autogen.beta.live.stt import VoiceInput, VoicePipeline

    agent = Agent("assistant", "You help.", config=OpenAIConfig(model="gpt-4o-mini"))
    pipeline = OpenAITranscriber("gpt-4o-transcribe").pipe(agent)
    assert isinstance(pipeline, VoicePipeline)

    # VoiceInput dataclass (16-bit PCM bytes).
    vi = VoiceInput(content=b"\x00\x00", frame_rate=24000, channels=1)
    assert vi.frame_rate == 24000
    print("OK: STTConfig.pipe(agent) -> VoicePipeline ; VoiceInput dataclass")


if __name__ == "__main__":
    test_public_api_imports()
    test_gemini_realtime_config()
    test_openai_realtime_config()
    test_openai_tts_and_stt()
    test_live_agent_construction()
    test_sound_device_objects()
    test_tts_observer()
    test_tts_observer_on_text_agent()
    test_stt_pipe_to_agent()
    print("\nALL GREEN")
