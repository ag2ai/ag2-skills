---
name: ag2-quickstart
description: Build a minimal AG2 `Agent` end to end — pick a model provider, set a prompt, call `agent.ask()`, then continue the conversation with `reply.ask()` (multi-turn). Use when the user is starting a new AG2 project, has no working `Agent` yet, or needs the multi-turn chaining pattern. Covers `OpenAIConfig`, `AnthropicConfig`, `GeminiConfig`, `OllamaConfig` etc., and env-var fallback for API keys.
license: Apache-2.0
---

# Quickstart: build your first AG2 Agent

## When to use

- The user is starting from a blank file and wants a working AG2 agent.
- The user is unsure which provider config to use.
- The user wants to chain follow-up turns without losing conversation context.
- A larger task needs the basic Agent setup as its skeleton — start here, then layer the relevant feature skill on top.

## Prerequisites

> **Required step: install dependencies before finishing.**
> After writing AG2 code, install the matching provider extra (plus any feature extra the task needs, e.g. `ag-ui`, `tracing`). Run the install:
>
> ```bash
> pip install "ag2[openai]"   # swap in the provider/extra you used
> ```
>
> If you cannot run commands, state the exact `pip install` command. This is part of finishing the task, not an optional note.
>
> For a multi-file project (more than a throwaway script), also drop a `requirements.txt` pinning `ag2` with the extras you used (e.g. `ag2[openai]>=0.14.0`) so the environment is reproducible.

Install the right provider extra and have a key for it. Each `*Config` requires its provider SDK — without the matching extra you'll see `ImportError: ... requires optional dependencies. Install with pip install "ag2[<provider>]"`.

| Provider | Install | Env var | Config class |
|---|---|---|---|
| OpenAI | `pip install "ag2[openai]"` | `OPENAI_API_KEY` | `OpenAIConfig`, `OpenAIResponsesConfig` |
| Anthropic | `pip install "ag2[anthropic]"` | `ANTHROPIC_API_KEY` | `AnthropicConfig` |
| Gemini (API key) | `pip install "ag2[gemini]"` | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | `GeminiConfig` |
| Vertex AI (Gemini) | `pip install "ag2[gemini]"` | service-account / ADC | `VertexAIConfig` |
| Ollama (local) | `pip install "ag2[ollama]"` | — | `OllamaConfig` |
| DashScope (Qwen) | `pip install "ag2[dashscope]"` | `DASHSCOPE_API_KEY` | `DashScopeConfig` |

Load env vars from a project-root `.env` with `python-dotenv` so scripts pick up keys without exporting them in your shell:

```python
from dotenv import load_dotenv
load_dotenv()  # reads .env at project root
```

Quick sanity-check before debugging weird import errors — make sure you're running against the ag2 you think:

```bash
python -c "import sys, ag2; from importlib.metadata import version; print(sys.executable); print('ag2', version('ag2'))"
```

## 60-second recipe

```python
import asyncio
from ag2 import Agent
from ag2.config import OpenAIConfig

async def main() -> None:
    agent = Agent(
        "assistant",
        prompt="You are a helpful assistant. Reply in one sentence.",
        config=OpenAIConfig(model="gpt-4o-mini"),
    )

    # First turn
    reply = await agent.ask("What is the capital of France?")
    print(reply.body)

    # Continue the same conversation — context is preserved
    reply = await reply.ask("And of Germany?")
    print(reply.body)

asyncio.run(main())
```

`Agent.ask(...)` starts a new turn and returns an `AgentReply`. `AgentReply.ask(...)` continues the same conversation, preserving its context and history. The reply text is in `reply.body`; for typed output see the `ag2-structured-output` skill (`reply.content()`).

## Picking a provider

Each provider has its own config class in `ag2.config`. All accept `model=`, optional `api_key=`, and (where supported) `streaming=True`. **Streaming is recommended** — AG2 is async- and streaming-first.

```python
from ag2.config import OpenAIConfig          # gpt-4o, gpt-5-*, o-series, etc.
from ag2.config import OpenAIResponsesConfig # OpenAI Responses API (image gen, file_id support)
from ag2.config import AnthropicConfig       # claude-sonnet-4-6, claude-opus-4-7, etc.
from ag2.config import GeminiConfig          # Gemini Developer API (api_key)
from ag2.config import VertexAIConfig        # Gemini on Google Vertex AI (project + location)
from ag2.config import OllamaConfig          # local Ollama
from ag2.config import DashScopeConfig       # Alibaba Qwen

config = AnthropicConfig(model="claude-sonnet-4-6", streaming=True)
```

If `api_key=` is omitted, the config reads the standard env var — `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), etc.

For **OpenAI-compatible endpoints** (vLLM, LM Studio, Together, NVIDIA NIM, etc.) use `OpenAIConfig` with `base_url=` set:

```python
config = OpenAIConfig(
    model="qwen-3",
    base_url="http://localhost:8000/v1",
    api_key="NotRequired",  # pragma: allowlist secret
)
```

## Multi-turn — chain `reply.ask()`

```python
agent = Agent("planner", prompt="...", config=config)
reply = await agent.ask("Plan a 5-day Japan trip in late April.")
reply = await reply.ask("Budget is $2500 per person, two travellers.")
reply = await reply.ask("Prefer trains. Day-by-day itinerary.")
print(reply.body)
```

`reply.ask()` keeps the prior turns in scope so the LLM remembers the constraints. Calling `agent.ask(...)` again instead would start a fresh conversation. See `assets/multi_turn.py` for the full travel-planner example.

## Reusing model configs

Configs are immutable. Use `.copy(...)` to fork one with overrides:

```python
base = OpenAIConfig(model="gpt-5")
hot = base.copy(temperature=0.8)
cheap = base.copy(model="gpt-5-mini")
```

You can also override the model **per ask** — useful when the user brings their own API key per request:

```python
agent = Agent("assistant", prompt="Help.")
reply = await agent.ask("Hello!", config=OpenAIConfig(model="gpt-5", api_key="sk-..."))  # pragma: allowlist secret
```

The per-ask config completely replaces the agent's config for that turn.

## Going deeper

- Working starter (single-turn): `assets/hello_agent.py` (mirrors `code_examples/01`).
- Multi-turn starter: `assets/multi_turn.py` (mirrors `code_examples/03`).
- Full provider reference, including `VertexAIConfig` auth, `extra_body`, custom `httpx` client, env-var fallback table: `website/docs/user-guide/model_configuration.mdx`.
- Agent communication API surface (events, observing, HITL): `website/docs/user-guide/agents.mdx`.
- Static, dynamic, per-turn prompts: `website/docs/user-guide/system_prompts.mdx`.

## Common pitfalls

- **Forgetting to `await`** — every method on `Agent` / `AgentReply` is async. Wrap in `asyncio.run(main())` for scripts.
- **Calling `agent.ask()` twice expecting context to carry** — it doesn't; use `reply.ask()` instead.
- **Hardcoding API keys** — prefer env-var fallback (`OPENAI_API_KEY`, etc.) so configs commit cleanly.
- **Skipping `streaming=True`** — AG2 is streaming-first; you'll get a worse user experience without it on supported providers.
- **Per-ask `config=` is total override**, not a partial merge — be deliberate about which knobs you set.
