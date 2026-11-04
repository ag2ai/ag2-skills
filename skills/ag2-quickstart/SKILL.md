---
name: ag2-quickstart
description: Build a minimal AG2 `Agent` end to end — pick a model provider config, set a prompt, call `agent.ask()`, read `reply.body`, then continue the conversation with `reply.ask()` (multi-turn). Use when the user is starting a new AG2 project, has no working `Agent` yet, needs the multi-turn chaining pattern, or asks which provider config to use. Covers `OpenAIResponsesConfig` (OpenAI default), `OpenAICompletionsConfig` (Chat Completions and OpenAI-compatible `base_url` endpoints such as vLLM / LM Studio), `AnthropicConfig`, `GeminiConfig`, `VertexAIConfig`, `BedrockConfig`, `MistralConfig`, `XAIConfig`, `ZAIConfig`, `OllamaConfig`, `DashScopeConfig`, `config.copy(...)`, per-ask `config=`, and env-var fallback for API keys. For tools see `ag2-add-custom-tool`; typed output `ag2-structured-output`; images/files `ag2-multimodal-input`; tests `ag2-testing`.
license: Apache-2.0
metadata:
  ag2_version: "1.3.0"
---

# Quickstart: build your first AG2 Agent

## When to use

- Starting from a blank file and needing a working AG2 agent.
- Unsure which provider config class to use.
- Chaining follow-up turns without losing conversation context.
- A larger task needs the basic Agent skeleton — start here, then layer a feature skill on top.

## Prerequisites

> **Required step: install dependencies before finishing.** Install the provider extra (plus any feature extra the task needs, e.g. `ag-ui`, `tracing`):
>
> ```bash
> pip install "ag2[openai]"   # swap in the provider/extra you used
> ```
>
> If you cannot run commands, state the exact `pip install` command. For a multi-file project also add a `requirements.txt`, e.g. `ag2[openai]>=1.3.0`.

Without the matching extra a config raises `ImportError: ... Install with pip install "ag2[<provider>]"`.

| Provider | Extra | Env var | Config class |
|---|---|---|---|
| OpenAI (Responses API — **default**) | `openai` | `OPENAI_API_KEY` | `OpenAIResponsesConfig` |
| OpenAI Chat Completions / OpenAI-compatible | `openai` | `OPENAI_API_KEY` | `OpenAICompletionsConfig` |
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` | `AnthropicConfig` |
| Gemini (API key) | `gemini` | `GEMINI_API_KEY` / `GOOGLE_API_KEY` | `GeminiConfig` |
| Gemini on Vertex AI | `gemini` | ADC / service account | `VertexAIConfig` |
| Amazon Bedrock | `bedrock` | AWS creds or `AWS_BEARER_TOKEN_BEDROCK` | `BedrockConfig` |
| Mistral | `mistral` | `MISTRAL_API_KEY` | `MistralConfig` |
| xAI (Grok) | `xai` | `XAI_API_KEY` | `XAIConfig` |
| Z.AI (GLM) | `zai` | `ZAI_API_KEY` | `ZAIConfig` |
| Ollama (local) | `ollama` | — | `OllamaConfig` |
| DashScope (Qwen) | `dashscope` | `DASHSCOPE_API_KEY` | `DashScopeConfig` |

All live in `ag2.config`. Load keys from a project-root `.env` with `from dotenv import load_dotenv; load_dotenv()`.

> **Migration note:** `OpenAIConfig` is deprecated in 1.3.0 (emits `DeprecationWarning`) and becomes an alias of `OpenAIResponsesConfig` in 1.4.0. Don't write new code with it.

## 60-second recipe

```python
import asyncio
from ag2 import Agent
from ag2.config import OpenAIResponsesConfig

async def main() -> None:
    agent = Agent(
        "assistant",
        prompt="You are a helpful assistant. Reply in one sentence.",
        config=OpenAIResponsesConfig(model="gpt-4.1-mini", streaming=True),
    )
    reply = await agent.ask("What is the capital of France?")
    print(reply.body)                      # str | None — the text answer

    reply = await reply.ask("And of Germany?")  # same conversation
    print(reply.body)

asyncio.run(main())
```

**Reading the answer:** `reply.body` is the raw text (sync property). `await reply.content()` returns the *typed* value when a `response_schema` is set (see `ag2-structured-output`); without a schema it just returns `reply.body`. Use `reply.body` for plain text.

## Picking a provider

Every config takes `model=`, optional `api_key=` (falls back to the env var), and `streaming=True` where supported — **enable streaming**, AG2 is streaming-first.

```python
from ag2.config import OpenAIResponsesConfig, OpenAICompletionsConfig, AnthropicConfig, GeminiConfig

OpenAIResponsesConfig(model="gpt-4.1-mini", streaming=True)      # new OpenAI code
OpenAICompletionsConfig(model="gpt-4.1-mini", streaming=True)    # explicit Chat Completions
AnthropicConfig(model="claude-haiku-4-5", streaming=True)
GeminiConfig(model="gemini-3.6-flash", streaming=True)
```

**OpenAI-compatible endpoints** (vLLM, LM Studio, Together, NVIDIA NIM…) speak Chat Completions — use `OpenAICompletionsConfig` with `base_url=`:

```python
config = OpenAICompletionsConfig(
    model="qwen-3",
    base_url="http://localhost:8000/v1",
    api_key="NotRequired",  # pragma: allowlist secret
)
```

Provider gotchas:
- **Anthropic:** don't pass `temperature` / `top_p` / `top_k` — deprecated (removed in 1.4.0; the anthropic 2.x API rejects them). Custom `http_client=` must be an `httpx2.AsyncClient`.
- **OpenAI reasoning:** `OpenAIResponsesConfig(reasoning={"effort": "low"})` vs `OpenAICompletionsConfig(reasoning_effort="low")`. Responses uses `max_output_tokens` (not `max_tokens`).
- Reasoning models (`gpt-5*`, `o*`) reject `temperature`; use it on `gpt-4.1*` / Gemini.

## Multi-turn — chain `reply.ask()`

```python
reply = await agent.ask("Plan a 5-day Japan trip in late April.")
reply = await reply.ask("Budget is $2500 per person, two travellers.")
reply = await reply.ask("Prefer trains. Day-by-day itinerary.")
print(reply.body)
```

`reply.ask()` keeps prior turns in scope. Calling `agent.ask(...)` again starts a **fresh** conversation. Full example: `assets/multi_turn.py`.

## Reusing model configs

Configs are immutable; fork with `.copy(...)`:

```python
base = OpenAIResponsesConfig(model="gpt-4.1-mini")
hot = base.copy(temperature=0.8)
cheap = base.copy(model="gpt-5-nano")
```

Override the model **per ask** (e.g. bring-your-own-key per request) — this fully replaces the agent's config for that turn, no merge:

```python
agent = Agent("assistant", prompt="Help.")
reply = await agent.ask("Hello!", config=OpenAIResponsesConfig(model="gpt-4.1-mini", api_key=user_key))
```

## Going deeper

- Starters: `assets/hello_agent.py`, `assets/multi_turn.py`.
- Full provider reference (Vertex auth, Bedrock auth, `extra_body`, `httpx2` clients, OpenAI Decisions / TypeSafe): `website/docs/user-guide/model_configuration.mdx`.
- Agent API (events, streaming, HITL): `website/docs/user-guide/agents.mdx`; prompts: `system_prompts.mdx`.

## Common pitfalls

- **Forgetting `await`** — all `Agent` / `AgentReply` methods are async; wrap scripts in `asyncio.run(main())`.
- **Expecting `agent.ask()` to remember** — use `reply.ask()`.
- **Hardcoding API keys** — rely on env-var fallback.
- **`reply.content()` without `await`** — it's a coroutine; for plain text just use `reply.body`.
