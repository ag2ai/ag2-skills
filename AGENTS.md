# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Cursor, Copilot, etc.) when working with code in this repository.

## Repository Overview

A collection of skills for working with [AG2](https://github.com/ag2ai/ag2) — an async, protocol-driven Python agent framework (`autogen.beta`). Skills are packaged instructions and optional helper scripts that extend an agent's capabilities.

Skills follow the [Agent Skills](https://agentskills.io/) format: a `SKILL.md` with YAML frontmatter, plus optional `scripts/` and `references/` directories.

## Creating a New Skill

### Directory Structure

```
skills/
  {skill-name}/           # kebab-case directory name
    SKILL.md              # Required: skill definition
    scripts/              # Optional: executable scripts
      {script-name}.py    # Python preferred (matches AG2's runtime)
    references/           # Optional: supporting docs the agent reads on demand
  {skill-name}.zip        # Optional: packaged for distribution
```

### Naming Conventions

- **Skill directory**: `kebab-case`, prefixed `ag2-` for AG2-feature skills (e.g. `ag2-quickstart`, `ag2-middleware`).
- **`SKILL.md`**: Always uppercase, always this exact filename.
- **Scripts**: `kebab-case` or `snake_case` — pick one per skill and stay consistent.
- **Zip file**: Must match directory name exactly: `{skill-name}.zip`.

### `SKILL.md` Format

```markdown
---
name: {skill-name}
description: {One paragraph describing exactly when to use this skill. Lead with the action and key API surface; include trigger phrases and what to use it *with* (sibling skills).}
license: Apache-2.0
---

# {Skill Title}

## When to use

{Concrete situations where this skill applies. List trigger phrases and the user goals it serves.}

## Recipe

{Numbered steps or a minimal working example, in Python. Show the imports.}

## Reference

{Link to any files in `references/` or upstream AG2 docs (`website/docs/beta/...`).}
```

### Frontmatter limits (per the [Agent Skills spec](https://agentskills.io/specification))

These are hard limits, checked by `skills-ref validate ./skills/{skill-name}`. Stay within them:

| Field | Limit | Rules |
|---|---|---|
| `name` | **≤ 64 characters** | Lowercase `a–z`, `0–9`, and hyphens only; no leading/trailing hyphen; no consecutive `--`; **must equal the directory name**. |
| `description` | **≤ 1024 characters** | Non-empty; say *what* it does and *when* to use it, with trigger keywords. Must be **valid YAML**: if the text contains a colon-then-space (`: `), wrap the whole value in quotes — otherwise the frontmatter fails to parse. |
| `compatibility` | ≤ 500 characters | Optional; only for real environment requirements. |
| `license`, `metadata`, `allowed-tools` | — | Optional (see spec). |

### Body sizing (chars / tokens)

- `name` + `description` are the **only** bytes loaded at startup (the discovery budget, ~100 tokens) — keep them tight but keyword-rich.
- The full `SKILL.md` body loads on activation. The spec recommends keeping it **under ~5000 tokens** and **under 500 lines**. Aim well under that — **target < 5000 characters** for a focused skill — and push depth into `references/` (loaded only on demand).

### Best Practices for Context Efficiency

Skills are loaded on-demand — only the skill `name` and `description` from the frontmatter are loaded at startup. The full `SKILL.md` loads into context only when the agent decides the skill is relevant. To minimise context usage:

- **Keep `SKILL.md` under 500 lines** — put detailed reference material in `references/` or separate files.
- **Write specific descriptions** — list the exact AG2 classes / functions the skill covers (e.g. `BaseMiddleware.on_turn`, `KnowledgeStore`, `TestConfig`) so the agent can recognise the trigger.
- **Use progressive disclosure** — reference supporting files that get read only when needed.
- **Prefer scripts over inline code** — script execution doesn't consume context (only output does).
- **Cross-link sibling skills** — a description that says "for X see `ag2-other-skill`" is cheap and saves the agent loading the wrong file.

### Script Requirements

For Python scripts (the common case):

- Use `#!/usr/bin/env python3` shebang and `chmod +x` the file.
- Keep stdout machine-readable; print human-facing status to stderr.
- Avoid hidden global state — accept all inputs via CLI args or stdin so the script is reproducible.
- Reference the script as `skills/{skill-name}/scripts/{script}.py` from `SKILL.md`.

For Bash scripts:

- Use `#!/bin/bash` and `set -e` for fail-fast behaviour.
- Status messages to stderr, machine-readable output (JSON or plain values) to stdout.
- Include a `trap` for any temp files.

### Creating the Zip Package

After creating or updating a skill:

```bash
cd skills
rm -f {skill-name}.zip
zip -r {skill-name}.zip {skill-name}/ -x "*.DS_Store" "*/__pycache__/*"
```

The `-x` excludes keep macOS metadata and Python bytecode out of the archive.

### End-User Installation

Document these installation methods for users:

**Claude Code:**
```bash
cp -r skills/{skill-name} ~/.claude/skills/
```

**claude.ai:**
Upload the skill `.zip` from `skills/` in the project's Skills settings, or paste the contents of `SKILL.md` into the conversation.
