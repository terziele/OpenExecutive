"""SQLite persistence for coding-agent jobs.

Reuses the episodic-memory DB file (same backup unit as workflows). Tests
monkeypatch ``DB_PATH`` on this module and/or ``openexecutive.memory.episodic``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openexecutive.coding_agents.models import ACTIVE_STATUSES
from openexecutive.memory.episodic import DB_PATH, _get_conn

# Cap persisted event strings so a chatty runtime cannot balloon the row.
_MAX_EVENTS = 40
_MAX_EVENT_CHARS = 200


def _resolve(db_path: Path | None) -> Path:
    """Dynamic DB_PATH resolution — lets tests monkeypatch store.DB_PATH."""
    return db_path if db_path is not None else DB_PATH


def _now() -> str:
    return datetime.now(UTC).isoformat()


def initialize_coding_jobs_db(db_path: Path | None = None) -> None:
    """Create ``coding_jobs`` and its indexes. Idempotent."""
    with _get_conn(_resolve(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS coding_jobs (
                job_id       TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                runtime      TEXT NOT NULL,
                mode         TEXT NOT NULL,
                task         TEXT NOT NULL,
                status       TEXT NOT NULL,
                artifact     TEXT,
                error        TEXT,
                events_json  TEXT,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS coding_jobs_status_updated_idx "
            "ON coding_jobs (status, updated_at)"
        )


def _row_to_dict(row: Any) -> dict[str, Any]:
    out = dict(row)
    raw_events = out.pop("events_json", None)
    events: list[str] = []
    if raw_events:
        try:
            parsed = json.loads(raw_events)
            if isinstance(parsed, list):
                events = [str(item) for item in parsed]
        except json.JSONDecodeError:
            events = []
    out["events"] = events
    return out


def insert_job(
    job_id: str,
    workspace_id: str,
    runtime: str,
    mode: str,
    task: str,
    *,
    status: str = "queued",
    db_path: Path | None = None,
) -> dict[str, Any]:
    initialize_coding_jobs_db(db_path)
    now = _now()
    with _get_conn(_resolve(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO coding_jobs (
                job_id, workspace_id, runtime, mode, task, status,
                artifact, error, events_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)
            """,
            (job_id, workspace_id, runtime, mode, task, status, "[]", now, now),
        )
    return {
        "job_id": job_id,
        "workspace_id": workspace_id,
        "runtime": runtime,
        "mode": mode,
        "task": task,
        "status": status,
        "artifact": None,
        "error": None,
        "events": [],
        "created_at": now,
        "updated_at": now,
    }


def get_job(job_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    path = _resolve(db_path)
    if not path.exists():
        return None
    with _get_conn(path) as conn:
        row = conn.execute("SELECT * FROM coding_jobs WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def list_jobs(
    *,
    status: str | None = None,
    limit: int = 100,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    path = _resolve(db_path)
    if not path.exists():
        return []
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
    params.append(limit)
    with _get_conn(path) as conn:
        rows = conn.execute(
            f"SELECT * FROM coding_jobs {where}ORDER BY updated_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_active_jobs(db_path: Path | None = None) -> int:
    path = _resolve(db_path)
    if not path.exists():
        return 0
    placeholders = ", ".join("?" for _ in ACTIVE_STATUSES)
    with _get_conn(path) as conn:
        row = conn.execute(
            f"SELECT COUNT(*) FROM coding_jobs WHERE status IN ({placeholders})",
            tuple(sorted(ACTIVE_STATUSES)),
        ).fetchone()
    return int(row[0]) if row is not None else 0


def update_job(
    job_id: str,
    *,
    status: str | None = None,
    artifact: str | None = None,
    error: str | None = None,
    events: list[str] | None = None,
    expected_statuses: tuple[str, ...] | None = None,
    db_path: Path | None = None,
) -> bool:
    """Patch a job. Returns True when a row was written.

    ``expected_statuses`` is a compare-and-swap guard so a late success
    cannot overwrite a cancel/timeout.
    """
    path = _resolve(db_path)
    if not path.exists():
        return False
    assignments = ["updated_at = ?"]
    params: list[Any] = [_now()]
    if status is not None:
        assignments.append("status = ?")
        params.append(status)
    if artifact is not None:
        assignments.append("artifact = ?")
        params.append(artifact)
    if error is not None:
        assignments.append("error = ?")
        params.append(error)
    if events is not None:
        assignments.append("events_json = ?")
        params.append(json.dumps(_cap_events(events)))
    sql = f"UPDATE coding_jobs SET {', '.join(assignments)} WHERE job_id = ?"
    params.append(job_id)
    if expected_statuses:
        placeholders = ", ".join("?" for _ in expected_statuses)
        sql += f" AND status IN ({placeholders})"
        params.extend(expected_statuses)
    with _get_conn(path) as conn:
        cur = conn.execute(sql, params)
        return cur.rowcount > 0


def append_event(
    job_id: str,
    event: str,
    db_path: Path | None = None,
) -> None:
    row = get_job(job_id, db_path=db_path)
    if row is None:
        return
    events = list(row.get("events") or [])
    events.append(event)
    update_job(job_id, events=events, db_path=db_path)


def _cap_events(events: list[str]) -> list[str]:
    capped = [str(item)[:_MAX_EVENT_CHARS] for item in events]
    if len(capped) > _MAX_EVENTS:
        return capped[-_MAX_EVENTS:]
    return capped
