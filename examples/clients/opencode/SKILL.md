---
name: openexec-mcp-opencode
description: Use Open Executive as a remote MCP server from OpenCode. Use when an OpenCode session needs company context (MCP resources) or specialist analysis via consult_specialist — not for REST /chat or fixture mutations.
---

# Open Executive MCP — OpenCode

Wire OpenCode to OE’s Streamable-HTTP MCP server at `/mcp` with header auth (no OAuth).

## Setup

**Config file:** merge [opencode.json.example](./opencode.json.example) into your OpenCode config, or add the server via CLI:

```bash
export BACKEND_SHARED_SECRET=...   # user exports locally — never commit
opencode mcp add open-executive \
  --url http://localhost:8000/mcp \
  --header "Authorization: Bearer $BACKEND_SHARED_SECRET"
```

Important fields in the example JSON:

- `"type": "remote"` — HTTP MCP, not a local stdio server
- `"oauth": false` — shared-secret header only
- `"Authorization": "Bearer {env:BACKEND_SHARED_SECRET}"` — env interpolation (OpenCode syntax)

Inspect and manage servers with `opencode mcp` subcommands (list, remove, etc.) per OpenCode docs.

Start the API first (`make dev`). FastMCP may **307** `/mcp` → `/mcp/`; remote clients should follow the redirect.

## Auth

| Channel | Header |
|--------|--------|
| **MCP** | `Authorization: Bearer $BACKEND_SHARED_SECRET` |
| **REST** | `x-api-key: $BACKEND_SHARED_SECRET` |

Never paste the literal secret into config checked into git. Never expand `$BACKEND_SHARED_SECRET` in shared transcripts.

Bearer on MCP is part of the P0 auth alias landing with this work; `x-api-key` remains valid for REST and legacy MCP clients.

## MCP usage

Same surface as Cursor — see [../cursor/SKILL.md](../cursor/SKILL.md) for resource URIs and tool guidance.

**Prefer** `oe://…` resources and **`consult_specialist`**. Use **`ask_executive`** only when you need one synthesized cross-domain answer and accept a full Executive turn.

## Safety tiers

- **Green:** MCP resources; `consult_specialist`, `search_knowledge`, `list_workflows`; REST `GET`.
- **Yellow:** reversible REST mutations (including `POST /coding-jobs/{job_id}/cancel`) — confirm once per session.
- **Red:** `POST /chat`, `POST /fixtures/*` — print intent, wait for explicit approval, no chaining.

REST details: `.claude/skills/openexec-api/SKILL.md` (green `GET /coding-jobs`, yellow cancel).

## Deployed API

Use your deployment origin instead of `localhost:8000`, same `/mcp` path and Bearer header.
