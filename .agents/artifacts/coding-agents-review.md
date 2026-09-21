# Coding agents review (working tree)

**Scope:** REVIEW ONLY of the uncommitted Cursor CLI + OpenCode integration on `anvil/coding-agents-support`. Reviewed the dirty worktree, not `HEAD` vs `main`.

**Date:** 2026-09-21  
**Artifacts path:** `.agents/artifacts/coding-agents-review.md` (`$ARTIFACTS_DIR` was unset; used `.agents/artifacts`).

---

## 1. What landed and matches the design

### P0 — inbound MCP auth + client examples

- Shared-secret gate accepts `x-api-key` **or** `Authorization: Bearer` (case-insensitive scheme), requires identical values when both are set, constant-time compare, no query-string auth (`packages/core/openexecutive/api/main.py`, `test_shared_secret_gate.py`).
- `/mcp` is covered by the same middleware; docs (`docs/auth.md`, `docs/coding_agents.md`) and examples (`examples/clients/cursor`, `examples/clients/opencode`) show Bearer + env indirection.
- Architecture `prebuilt/mcp_server.json` / `api.json` document Bearer alias.

### P1 — outbound coding runtimes (hands, not specialists)

- New package `openexecutive.coding_agents/` with YAML allowlist, SQLite `coding_jobs`, ask/plan-only service, Cursor CLI + OpenCode runners, Executive tools, HTTP inspect/cancel.
- **Not** in `SPECIALIST_REGISTRY` (package docstring + architecture facts/section explicitly forbid it).
- Tools `cancel_coding_job` / `get_coding_job` / `start_coding_job` always on `_ALL_SKILL_TOOLS` / handlers (cache-stable); disabled feature returns JSON `code: disabled`.
- `CODING_AGENT_ADDENDUM` is a non-f-stringed constant; appended in `cache_manager` only when `coding_agents_enabled` (tested).
- Write/agent modes refused with `write_mode_deferred` even if `has_user_consent=True`.
- Model-facing input is `workspace_id` slug only; path come from operator YAML; `..` / path-like ids rejected.
- HTTP: `GET /coding-jobs`, `GET /coding-jobs/{id}`, `POST /coding-jobs/{id}/cancel`; **no** `POST /coding-jobs` (route test asserts 405).
- Config: `_resolve_coding_agents` mirrors MCP (file presence auto-enable, explicit env wins, empty file ≠ consent).
- Docker compose documents that the API image is not the job host; optional coding-worker sketch stays commented.
- Architecture: new `coding_agents` section + UI nav entry; `api` / `caching` / `schemas` / `mcp_server` / facts YAML updated.
- Subprocess env allowlist (drops Slack/Google/Anthropic/`BACKEND_SHARED_SECRET`); forbidden argv flags (`--force`, `--yolo`, `--api-key`, `--dangerously-skip-permissions`, `--auto`); OpenCode CLI forces `--agent plan`; Cursor uses `--sandbox enabled` and no `--force`.
- Research synthesis excludes all three coding tools; unit coverage is substantial (~1.1k lines across coding + Bearer tests).

---

## 2. Gaps vs P0/P1 design

| Gap | Severity | Notes |
|-----|----------|--------|
| No principal-visible completion (`draft_artifact` / `propose_via_alert`); only audit + poll | **high** | Addendum tells the model to “follow up when it finishes,” but nothing closes the loop after `succeeded` / `failed` / `timed_out`. Matches gap-fill item 1. |
| `executive_reflection._excluded_dm` does **not** exclude `start_coding_job` / `cancel_coding_job` | **high** | Research synthesis excludes them; overnight reflection can still spawn/cancel subprocess jobs. Gap-fill item 2. |
| OpenCode **HTTP** (`serve_url`) ignores `WorkspaceSpec.path` | **high** | `_run_http` never binds the allowlisted directory; job runs against whatever the serve instance was started with. Docs prefer serve when configured. CLI path is fine. |
| Local path must exist on the **API** host even for HTTP/serve jobs | **medium** | `validate_workspace_path` runs before runtime dispatch; Docker-only API + remote OpenCode serve is awkward vs docs’ “jobs need a host with repos.” |
| No eval scenario for `CODING_AGENT_ADDENDUM` / `start_coding_job` | **medium** | `CLAUDE.md` requires evals for prompt changes; gap-fill item 3. Path should be `packages/core/openexecutive/evals/_scenarios/coding_jobs_001.yaml`. |
| `openexec-api` skill has no GET/cancel recipes for `/coding-jobs`; client skills don’t point at them | **medium** | Gap-fill item 4. Client MCP skills still say Bearer “is landing” (stale once this tree ships). |
| Job tool/API payloads include `runtime: cursor\|opencode` | **low** | One-voice rule is “don’t name vendors to the user”; model still sees vendor ids in tool JSON and may echo them. |
| Docs YAML example shows `description:`; model ignores it (`extra="ignore"`) | **low** | Harmless drift vs `coding_agents.yaml.example`. |
| No unit assert that coding runtimes are absent from `SPECIALIST_REGISTRY` | **low** | Invariant is documented; not pinned like other non-specialists. |

**P0 looks complete** for the stated contract (Bearer alias + examples + tests). Remaining gaps are almost all **P1 product/ops** leftovers, plus the OpenCode HTTP workspace binding issue.

**P2 correctly out of scope** in code: no write mode path, no `--force`/`--yolo` on default argv, no worktrees/PRs/ACP/`CODE_WRITE`.

---

## 3. Security issues

| Topic | Assessment |
|-------|------------|
| **Env isolation** | Solid for CLI: `isolated_subprocess_env` + tests that Slack / `BACKEND_SHARED_SECRET` / Anthropic stay out; Cursor gets `CURSOR_API_KEY`, OpenCode CLI gets `OPENCODE_SERVER_PASSWORD` only as needed. |
| **Path allowlist** | Strong for CLI: slug-only model input, YAML path resolve, `..` rejected, must be a directory. **Weakened for OpenCode HTTP** (allowlist path validated then unused). |
| **`--force` / dangerous flags** | Default argv clean; `assert_safe_argv` rejects forbidden flags including `=` forms. Cursor does use **`--trust`** (documented for headless); not `--force`, but it expands trust surface — acceptable for P1 ask/plan with `--sandbox enabled`, worth keeping explicit in ops docs. |
| **Secrets in argv/logs** | No `--api-key` on argv. OpenCode `safe_url_for_log` strips userinfo. HTTP errors avoid stringifying auth URLs. **Residual:** stderr snippets (≤200/300 chars) stored in events/errors could theoretically contain runtime-printed secrets. |
| **Query-string keys** | Gate does not read query params; tested. Docs say never put secret in URL. |
| **HTTP cancel/list when feature disabled** | Inspect/cancel do not re-check `coding_agents_enabled` (start does). Same shared-secret as rest of API — intentional ops surface, not an auth bypass. |
| **Concurrency** | `coding_max_concurrent` (default 1) under a start lock — good DoS bound for subprocess fan-out. |

No evidence of secrets committed in examples (env substitution only).

---

## 4. Prompt-cache / one-voice / specialist-registry invariants

| Invariant | Status |
|-----------|--------|
| Tools always registered (cache-stable list) | **OK** — always in `_ALL_SKILL_TOOLS`; client tools re-sorted by name at call site. |
| `CODING_AGENT_ADDENDUM` constant, no dynamic f-string in cached block | **OK** — gated by process-stable `coding_agents_enabled`; addendum tests forbid `{` / f-string assignment / paths. |
| One-voice (don’t name Cursor/OpenCode to principal) | **Mostly OK** — tools + addendum instruct the model; **risk** if tool results’ `runtime` field is echoed. |
| Not in `SPECIALIST_REGISTRY` | **OK** — no router registration; architecture section + package docs. |
| Promise vs behavior | **Mismatch** — addendum says follow up when finished; no completion notify (see §2). |

---

## 5. Test / eval / CI gaps

**Present and useful**

- Bearer on `/mcp`, mismatch, query rejection, OPTIONS/health exempt.
- Config allowlist, path/`..`, runtime disabled, write/agent deferred, concurrency, cancel, store CAS-ish updates.
- Runtime argv/env isolation, OpenCode HTTP session+message + basic auth (without asserting workspace binding).
- Addendum cache inclusion; tools sorted; routes list/get/cancel + no POST start.
- Research synthesis excludes coding tools; Executive always exposes the three tools (`test_department_tools`, form-patch tests).

**Missing / weak**

- No reflection exclusion test for `start_coding_job` / `cancel_coding_job`.
- No completion-notify tests (feature absent).
- No eval `coding_jobs_001.yaml`.
- No assert OpenCode HTTP associates the allowlisted workspace (today’s HTTP tests would pass even if path were deleted after validation).
- No registry drift test (“coding not a specialist”).
- `openexec-api` skill recipes untested by definition (docs-only).
- CI: unit tests exist; full-suite still subject to known env gotchas (`BACKEND_SHARED_SECRET`, `OE_PUBLIC_DEPLOYMENT`) — gap-fill’s `env -u BACKEND_SHARED_SECRET uv run pytest …` recipe is the right local gate.

---

## 6. Recommended Archon fill workflow

**Yes:** run `archon-plan-to-pr` against `.agents/plans/coding-agents-gap-fill.md`.

### Is that plan too wide or too narrow?

**Slightly narrow on one P1 correctness bug; otherwise well-scoped.**

| Plan item | Verdict |
|-----------|---------|
| 1. Principal-visible completion | Keep — real P1 product hole; also fixes addendum/promise mismatch. |
| 2. Reflection exclusions | Keep — high safety/ops risk. |
| 3. Eval scenario | Keep — required by repo norms for prompt addenda. |
| 4. Operator skill recipes | Keep — small, high leverage. |
| 5. Tests matching CI | Keep — especially for (1)–(2). |
| Out-of-scope list (P2, sidecar worker, UI/SSE, SPECIALIST_REGISTRY) | Correct — do not widen into those. |

**Recommend adding one item to the plan (or a tiny follow-up plan), not dropping anything:**

- **OpenCode HTTP workspace binding** (and/or document+enforce “CLI-only until serve can bind `directory`”): today preferred `serve_url` path does not enforce the allowlisted repo. That is a P1 design/security gap the current gap-fill file does **not** list.

Do **not** expand the gap-fill into: write mode, Docker coding-worker enablement, UI job page, SSE into chat, injecting OE MCP into outbound runtimes, or registry membership.

---

## Bottom line

P0 Bearer MCP + P1 core ask/plan job machinery **landed and largely match the design**, with good security hygiene on the CLI path and solid unit coverage. Ship-blockers for a “complete P1” narrative are not missing HTTP routes or specialist pollution — they are **unattended reflection can start jobs**, **no principal completion notify despite prompt language**, and **OpenCode serve ignoring the allowlisted workspace**. The existing gap-fill plan is the right Archon entry point; extend it slightly for the HTTP workspace issue, then stop before P2.
