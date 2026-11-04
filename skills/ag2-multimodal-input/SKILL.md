---
name: ag2-multimodal-input
description: Send images, audio, video, or documents into an AG2 `Agent` alongside text. Pass `ImageInput`, `AudioInput`, `VideoInput`, or `DocumentInput` as positional args to `agent.ask(...)`. Use when the user wants the agent to process non-text input — describe a photo, transcribe audio, summarise a PDF, analyse a video. Covers the per-provider support matrix (OpenAI Responses / Completions, Gemini, Anthropic, xAI, Mistral, Bedrock), the four ways to source data (URL / path / bytes / file_id), `BinaryInput(..., vendor_metadata=...)` for Gemini media-resolution / video clipping, OpenAI image detail and Anthropic prompt caching, Gemini YouTube URLs, and `FilesAPI` for upload lifecycle. For tools that return images see `ag2-add-custom-tool`; basic agent setup `ag2-quickstart`.
license: Apache-2.0
metadata:
  ag2_version: "1.3.0"
---

# Multimodal inputs

## When to use

The user wants the agent to process non-text input: an image to describe, audio to transcribe, video to summarise, or a PDF / document to extract from. The same factory pattern works across providers; per-provider support varies.

## 60-second recipe

```python
from ag2 import Agent
from ag2.config import GeminiConfig
from ag2.events import ImageInput

agent = Agent(
    "vision",
    "You describe images.",
    config=GeminiConfig(model="gemini-3.6-flash"),
)

image = ImageInput("https://example.com/photo.jpg")
reply = await agent.ask("Describe this image in detail.", image)
print(reply.body)
```

Multiple inputs in one ask are fine:

```python
reply = await agent.ask(
    "Compare these two images.",
    ImageInput("https://example.com/before.jpg"),
    ImageInput("https://example.com/after.jpg"),
)
```

## Input factories

| Factory | Formats |
|---|---|
| `ImageInput(...)` | JPEG, PNG, GIF, WebP |
| `AudioInput(...)` | WAV, MP3, OGG, FLAC, AAC |
| `VideoInput(...)` | MP4, WebM, MOV, MKV, MPEG |
| `DocumentInput(...)` | PDF, TXT, HTML, Markdown, CSV, JSON, Office formats |

Each accepts the same four data sources:

```python
from ag2.events import ImageInput

ImageInput("https://example.com/photo.jpg")     # URL
ImageInput(path="photo.jpg")                    # local file (type from extension)
ImageInput(data=raw_bytes, media_type="image/png")  # bytes
ImageInput(file_id="file-abc123")               # provider-uploaded
```

## Provider matrix

| Input type | OpenAI Completions | OpenAI Responses | Gemini | Anthropic | xAI | Mistral | Bedrock |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Image (URL) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | – |
| Image (binary) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Audio (URL) | – | – | ✓ | – | – | – | – |
| Audio (binary) | ✓ | – | ✓ | – | – | – | – |
| Video (URL) | – | – | ✓ | – | – | – | – |
| Video (binary) | – | – | ✓ | – | – | – | ✓ |
| Document (URL) | – | ✓ | ✓ | ✓ | ✓ | ✓ | – |
| Document (binary) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| File ID (via `FilesAPI`) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | – |

"OpenAI Completions" = `OpenAICompletionsConfig`; "OpenAI Responses" = `OpenAIResponsesConfig`. PDF bytes and file ids were verified on both OpenAI configs, Gemini and Anthropic in 1.3.0 (the upstream table under-reports them). Bedrock accepts bytes only (download URLs yourself).

Unsupported combinations raise `UnsupportedInputError` with a clear message.

**Gemini has the broadest multimodal support.** If you don't know which provider to pick for a multimodal task, start there.

## Provider-specific `vendor_metadata`

The `ImageInput` / `VideoInput` / `DocumentInput` / `AudioInput` factories do **not** accept `vendor_metadata`. Build a `BinaryInput` directly (provider hints are only applied to binary inputs):

```python
from ag2.events import BinaryInput, BinaryType

raw = open("photo.png", "rb").read()

# Gemini — token/cost trade-off (str, or {"level": ...} / {"num_tokens": ...}, never both)
BinaryInput(raw, media_type="image/png", kind=BinaryType.IMAGE,
            vendor_metadata={"media_resolution": "MEDIA_RESOLUTION_LOW"})

# Gemini — clip a local video and sample at 0.5 fps
BinaryInput(open("lecture.mp4", "rb").read(), media_type="video/mp4", kind=BinaryType.VIDEO,
            vendor_metadata={"video_metadata": {"start_offset": "60s", "end_offset": "120s", "fps": 0.5}})

# OpenAI / Mistral / xAI — image detail: "low" | "high" | "auto"
BinaryInput(raw, media_type="image/png", kind=BinaryType.IMAGE, vendor_metadata={"detail": "low"})

# Anthropic — prompt-cache a large attachment across turns
BinaryInput(open("report.pdf", "rb").read(), media_type="application/pdf", kind=BinaryType.DOCUMENT,
            vendor_metadata={"cache_control": {"type": "ephemeral"}})
```

**Gemini YouTube:** `VideoInput("https://www.youtube.com/watch?v=...")` works directly (URL inputs carry no clipping metadata).

## `FilesAPI` — upload once, reference by id

Supported by `OpenAIResponsesConfig`, `OpenAICompletionsConfig`, `AnthropicConfig`, `GeminiConfig`, `XAIConfig`, `MistralConfig`, `ZAIConfig`:

```python
from ag2 import FilesAPI
from ag2.config import AnthropicConfig
from ag2.events import DocumentInput

files = FilesAPI(AnthropicConfig(model="claude-haiku-4-5"))
uploaded = await files.upload(path="report.pdf")                       # or data=..., filename=... (required with data)
reply = await agent.ask("Summarize.", DocumentInput(file_id=uploaded.file_id, filename="report.pdf"))

reply = await agent.ask("Summarize.", uploaded)   # an UploadedFile is itself a file-id input
all_files = await files.list()
await files.delete(uploaded.file_id)
```

- OpenAI: pass `purpose="assistants"`; file ids are always sent as `input_file`, so use them for **documents**, not images (send images as URL/bytes).
- Anthropic: pass `filename=` with `file_id` — the extension picks image vs document block. `files.read()` only works for files Anthropic itself generated.
- Gemini: `files.read()` raises `NotImplementedError`; large videos may sit in `PROCESSING` before use.
- Mistral: uploads default to the `ocr` purpose, which is what message references need.
- A file id from one provider raises `UnsupportedInputError` on another.

## Going deeper

- `website/docs/user-guide/multimodal/inputs.mdx` — full provider matrix and `vendor_metadata` reference.
- `website/docs/user-guide/advanced/files.mdx` — `FilesAPI` reference (upload / list / read / delete).
- For tools that **return** images / binary back to the LLM, see `ag2-add-custom-tool` (`ImageInput`, `BinaryInput`, `ToolResult`).

## Common pitfalls

- **Unsupported provider/input combo** — raises `UnsupportedInputError` (e.g. audio on `OpenAIResponsesConfig`). Check the matrix; Gemini is broadest.
- **`vendor_metadata=` on a factory** — `TypeError`; use `BinaryInput(..., kind=BinaryType.X, vendor_metadata=...)`.
- **`files.upload(data=...)` without `filename=`** — `ValueError`.
- **More than one source to a factory** — not an error; priority is `url` > `file_id` > `path` > `data`. Zero sources raises `ValueError`.
- **`path=` with an unknown extension** — pass `media_type=` (it overrides the extension).
- **Image by `file_id` on OpenAI** — rejected by the API; send the image as URL/bytes.
