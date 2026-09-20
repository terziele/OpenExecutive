# Coding agents P1 gap-fill

Build contract for `archon-plan-to-pr`. Implements leftover **P1** items from the Cursor CLI / OpenCode integration on `anvil/coding-agents-support`. Does **not** implement P2 write mode, `--force`/`--yolo`, `AuthorityScope.CODE_WRITE`, git worktrees, PR opening, or ACP.

Archon review: `.agents/artifacts/coding-agents-review.md` (run `df4f61e0e805eed3f0e94d5e27a50fa6`). Follow that report; do not reopen P2.

## Git / branch (read first)

This checkout **is already** `anvil/coding-agents-support`. The P0+P1 integration is **uncommitted in the working tree**. Stay on this branch. Do **not** `git reset`, `stash -u`, `clean`, or `checkout` another branch — that would delete the feature. Implement gaps **on top of** the existing dirty files, then commit the integration plus this gap-fill as the PR (Open Executive template: Problem / Approach / Checklist only).

## Context

P0 (Bearer MCP auth + client examples) and P1 core (allowlisted ask/plan jobs, Executive tools, `/coding-jobs` inspect/cancel, architecture section) are already in the working tree. Gaps below are what the design required and the code still lacks.

## In scope (do these)

### 1. Principal-visible job completion
When a job reaches `succeeded` / `failed` / `timed_out`, persist a principal-facing card on the same path as `draft_artifact` (alerts table + `["artifact"]` tag → `/today` "Needs you"). Do not DM. Do not name Cursor or OpenCode in title/body. Include `job_id`, workspace slug, status, and the artifact or error text. Call this from `_execute_job` after the store update (also on fail/timeout; skip `cancelled`). Audit stays. The addendum already promises follow-up; make the promise true.

### 2. Keep unattended loops from starting jobs
Add `start_coding_job` and `cancel_coding_job` to `executive_reflection._excluded_dm` (research synthesis already excludes them). Reflection must not spawn subprocesses overnight.

### 3. Eval scenario
Add `packages/core/openexecutive/evals/_scenarios/coding_jobs_001.yaml` (same shape as `mcp_tool_call_001.yaml`): a principal ask that should call `start_coding_job` with a slug `workspace_id` (not a path), mode `ask` or `plan`, and must not name Cursor/OpenCode in the user-facing reply. Prompt change (`CODING_AGENT_ADDENDUM`) requires an eval per `CLAUDE.md`.

### 4. Operator skill recipes
Add green GET / yellow cancel recipes for `/coding-jobs` to `.claude/skills/openexec-api/SKILL.md` and a pointer from `examples/clients/*/SKILL.md`. Never expand `BACKEND_SHARED_SECRET`.

### 5. Tests that match CI
Add/adjust unit tests for (1), (2), and (6). Run via `cd packages/core && env -u BACKEND_SHARED_SECRET uv run pytest tests/unit/test_coding_agents_*.py tests/unit/test_coding_jobs_routes.py tests/unit/test_coding_agent_addendum.py tests/unit/test_executive_reflection.py -q` (or the reflection file that covers `_excluded_dm`) — do not rely on a throwaway lite venv as the only proof.

### 6. Bind OpenCode HTTP jobs to the allowlisted workspace
`OpenCodeRuntime._run_http` currently takes only `job` and never sends `WorkspaceSpec.path`. CLI correctly uses `--dir`. HTTP is the preferred path when `serve_url` is set, so the allowlist is validated then ignored.

- Change `run()` / `_run_http` / `_create_session` to pass the resolved workspace directory into session create (OpenCode field is typically `directory`; confirm against current serve API).
- If serve cannot bind a directory, **fail closed** and use `_run_cli` with `--dir` instead of executing against the serve process cwd.
- Unit test: HTTP session POST JSON includes the allowlisted path (today’s HTTP tests would pass even if the path were unused).

## Out of scope (do not do)

- Write/agent mode, `--force`, `--dangerously-skip-permissions`, `CODE_WRITE`
- Sidecar/worker process or Docker service that actually runs `agent`/`opencode` (docs already say jobs need a host with repos)
- Injecting OE MCP into outbound Cursor/OpenCode (`--approve-mcps` / `--plugin-dir`)
- UI page for jobs, SSE streaming of runtime events into chat
- Cursor `--worktree` / git PR flow (P2)
- Putting runtimes in `SPECIALIST_REGISTRY`

## Invariants

- Tools stay always-on for prompt-cache stability; handlers still JSON-error when disabled
- `CODING_AGENT_ADDENDUM` remains a constant; no f-strings in cached system blocks
- Model never receives a filesystem path — only `workspace_id`
- User-facing text must not name Cursor or OpenCode
- Architecture `prebuilt/coding_agents.json` + facts YAML when completion-notify or OpenCode HTTP directory binding is documented
- Optional low: strip `runtime` from model-facing tool JSON if easy; do not spend the PR on one-voice polish beyond that
