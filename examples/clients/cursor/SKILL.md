---
name: openexec-mcp-cursor
description: Use Open Executive as an MCP server from Cursor (IDE or CLI). Use when grounding a Cursor agent in company profile, today briefing, people/departments, episodic memory, or when you need domain analysis via consult_specialist — not for raw REST curl against /chat or fixtures.
---

# Open Executive MCP — Cursor

Connect Cursor to the Open Executive FastAPI backend’s Streamable-HTTP MCP endpoint at `/mcp`.

## Setup

1. Copy [mcp.json.example](./mcp.json.example) to **`.cursor/mcp.json`** (project) or **`~/.cursor/mcp.json`** (global).
2. Export `BACKEND_SHARED_SECRET` in the environment where Cursor runs (same value as the API’s shared secret — see `docs/auth.md`). **Never commit** a file that contains the literal secret.
3. Start the API (`make dev` → `http://localhost:8000`).
4. For **headless CLI** runs, approve MCP on first connect:

   ```bash
   agent mcp enable open-executive   # if your Cursor version exposes this
   agent -p --approve-mcps --trust "your task"
   ```

   Prefer `--approve-mcps` and `--trust` for unattended runs. Do **not** treat `--force` as the default way to skip prompts.

FastMCP may respond with **307** from `/mcp` to `/mcp/`; compliant clients follow the redirect automatically.

## Auth

| Channel | Header |
|--------|--------|
| **MCP** (this skill) | `Authorization: Bearer $BACKEND_SHARED_SECRET` |
| **REST** (curl, scripts) | `x-api-key: $BACKEND_SHARED_SECRET` |

Rules:

- **Never expand `$BACKEND_SHARED_SECRET`** into literals in commands, commits, or chat.
- **Never write the secret into tracked files.** Use env substitution in config (`${env:BACKEND_SHARED_SECRET}` in `mcp.json`).

Bearer support for MCP is landing in the same change set as these examples; until then, some builds may accept only `x-api-key` on HTTP — if MCP auth fails, confirm the API build includes the Bearer alias.

## What to use on MCP

**Prefer** read-only **resources** and **`consult_specialist`**:

| Resource URI | Purpose |
|--------------|---------|
| `oe://company/profile` | Company profile (markdown) |
| `oe://today/briefing` | Today snapshot (JSON) |
| `oe://today/activity` | Recent Executive activity |
| `oe://people/roster` | People roster |
| `oe://departments/state` | Departments |
| `oe://memory/decisions` | Past decisions |
| `oe://memory/initiatives` | Initiatives |
| `oe://memory/advice` | Advice given |

| Tool | When |
|------|------|
| `consult_specialist` | Domain analysis (CFO, CSO, …) with company RAG |
| `search_knowledge` | Raw retrieval, no LLM |
| `list_workflows` | Workflow catalog (execution not on MCP) |
| `ask_executive` | **Fallback only** — full Executive turn, non-streaming; prefer resources + `consult_specialist` |

## Safety tiers (REST + behavior)

Aligned with the `openexec-api` skill:

- **Green — auto-run:** MCP resources; MCP `search_knowledge`, `list_workflows`, `consult_specialist`; all REST `GET`s.
- **Yellow — confirm once per session:** REST writes that change durable but reversible state (people, scheduled actions, architecture regenerate, coding-job cancel, etc.).
- **Red — explicit “go” each time; do not chain:** REST `POST /chat`, all `POST /fixtures/*` (load, unload, reset, snapshot). Do not drive `/chat` from Cursor to impersonate the user.

For operator curl recipes, use the repo’s `.claude/skills/openexec-api/SKILL.md` (including green `GET /coding-jobs` and yellow `POST /coding-jobs/{job_id}/cancel`).

## Deployed API

Replace `http://localhost:8000` with your deployment base URL (TLS recommended). Keep the same `/mcp` path and Bearer header pattern.
