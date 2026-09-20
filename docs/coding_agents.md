# Coding agents (Cursor CLI & OpenCode)

Open Executive is the **brain**; Cursor CLI (`agent`) and OpenCode are **hands** for read-only exploration of allowlisted repos. This page covers **inbound** MCP (clients → OE) and **outbound** job tools (OE → clients) landing in the same PR train.

## Prerequisites

- API running (`make dev` → `http://localhost:8000`).
- `BACKEND_SHARED_SECRET` set (same as UI/API proxy — see [auth.md](./auth.md)).
- For outbound jobs (P1): `CURSOR_API_KEY` and/or OpenCode auth on the **job host**, not inside the API container.

## 1. Inbound — attach clients to OE MCP

OE exposes a Streamable-HTTP MCP server at **`/mcp`** (mounted from `openexecutive.mcp_server`). Tools and resources are defined in `packages/core/openexecutive/mcp_server/server.py`.

### Auth

Send the shared secret as:

```http
Authorization: Bearer <BACKEND_SHARED_SECRET>
```

The existing **`x-api-key`** header still works for REST and for MCP; Bearer is the preferred alias for MCP clients.

Never commit secrets. Use env indirection in config files.

### Cursor

Copy [examples/clients/cursor/mcp.json.example](../examples/clients/cursor/mcp.json.example) to `.cursor/mcp.json` or `~/.cursor/mcp.json`. Skill reference: [examples/clients/cursor/SKILL.md](../examples/clients/cursor/SKILL.md).

```json
{
  "mcpServers": {
    "open-executive": {
      "url": "http://localhost:8000/mcp",
      "headers": {
        "Authorization": "Bearer ${env:BACKEND_SHARED_SECRET}"
      }
    }
  }
}
```

Headless CLI: `agent -p --approve-mcps --trust` (avoid `--force` as default).

### OpenCode

Copy [examples/clients/opencode/opencode.json.example](../examples/clients/opencode/opencode.json.example) or:

```bash
opencode mcp add open-executive \
  --url http://localhost:8000/mcp \
  --header "Authorization: Bearer $BACKEND_SHARED_SECRET"
```

Set `"oauth": false` for remote header auth. Skill: [examples/clients/opencode/SKILL.md](../examples/clients/opencode/SKILL.md).

### Redirect note

FastMCP may respond **307** from `/mcp` to `/mcp/`. Compliant MCP clients follow automatically.

### What agents should call

| Prefer | Avoid as default |
|--------|------------------|
| MCP resources (`oe://company/profile`, `oe://today/briefing`, roster, memory, …) | `ask_executive` (full chat turn) |
| `consult_specialist` | `POST /chat` over REST |
| `search_knowledge`, `list_workflows` | Fixture load/reset |

## 2. Outbound — OE dispatches coding jobs (P1)

Async jobs via Executive tools (`openexecutive.coding_agents`) and HTTP list/get/cancel (see architecture **Coding Agents** section).

### Executive tools

| Tool | Role |
|------|------|
| `start_coding_job` | Enqueue ask/plan work; returns `job_id` |
| `get_coding_job` | Poll status/output |
| `cancel_coding_job` | Cancel a queued/running job |

Jobs do **not** block the API request. The model must pass **`workspace_id`** from the allowlist — never a raw filesystem path.

### Modes

- **ask** and **plan** only in P1.
- **write / agent** mode is **refused** with a clear JSON error (P2).
- Default paths must **not** use `--force`, `--yolo`, or `--dangerously-skip-permissions`.

### Allowlist — `company/coding_agents.yaml`

Gitignored company file (like `company/profile.yaml`). Example template: [`packages/core/coding_agents.yaml.example`](../packages/core/coding_agents.yaml.example).

Expected shape (from product spec):

```yaml
# Logical ids → paths on the JOB HOST (not inside the API Docker image)
workspaces:
  - id: main-app
    path: /abs/path/to/repo
    description: Primary monorepo
runtimes:
  cursor:
    enabled: true
  opencode:
    enabled: true
    # optional: serve_url for HTTP session API instead of CLI run
```

Only listed `workspace_id` values are valid for `start_coding_job`. Paths must exist on the machine that runs Cursor/OpenCode subprocesses.

### Where jobs run

The **API Docker container has no git checkouts**. Run the API on a host (or sidecar) that can execute `agent` / `opencode` against allowlisted paths, with isolated env:

- **In:** task prompt, workspace id, mode ask|plan.
- **In (runtime auth):** `CURSOR_API_KEY`, OpenCode credentials as configured.
- **Out:** do not forward Slack, Google, or `BACKEND_SHARED_SECRET` into coding subprocesses except any deliberate MCP callback header (optional in P1).

### Runtimes (operator reference)

**Cursor CLI (headless):**

```bash
agent -p --trust --workspace <allowlisted-path> \
  --sandbox enabled --mode ask|plan --output-format json "task"
```

Legacy binary name: `cursor-agent`.

**OpenCode:** prefer `opencode serve` HTTP when `serve_url` is configured; else `opencode run --dir <path> --format json --agent plan "task"`. `--auto` is out of scope until P2.

### HTTP job API

Same shared-secret gate as other routes (`x-api-key` or `Authorization: Bearer`). Start is chat-only (`start_coding_job`); there is no `POST /coding-jobs`.

```bash
curl -s -H "Authorization: Bearer $BACKEND_SHARED_SECRET" $OE_API/coding-jobs
curl -s -H "Authorization: Bearer $BACKEND_SHARED_SECRET" $OE_API/coding-jobs/$JOB_ID
curl -sX POST -H "Authorization: Bearer $BACKEND_SHARED_SECRET" $OE_API/coding-jobs/$JOB_ID/cancel
```

## Safety summary

| Tier | Examples |
|------|----------|
| Green | MCP resources, `consult_specialist`, REST GET |
| Yellow | Reversible REST writes (confirm once) |
| Red | `POST /chat`, `POST /fixtures/*` |

## See also

- [architecture.md](./architecture.md) — orchestrator and MCP gateway
- [deployment.md](./deployment.md) — production topology
- `.claude/skills/openexec-api/SKILL.md` — curl against REST
