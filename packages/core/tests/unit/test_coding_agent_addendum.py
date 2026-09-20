"""CODING_AGENT_ADDENDUM is a constant, included only when the flag is on."""

from __future__ import annotations

import inspect

from openexecutive.prompts import executive_persona
from openexecutive.prompts.cache_manager import build_system_blocks
from openexecutive.prompts.executive_persona import CODING_AGENT_ADDENDUM


def test_addendum_is_a_plain_constant() -> None:
    source = inspect.getsource(executive_persona)
    assert "CODING_AGENT_ADDENDUM = f" not in source
    assert "CODING_AGENT_ADDENDUM = " + 'f"""' not in source
    assert "{" not in CODING_AGENT_ADDENDUM
    assert "/repos/" not in CODING_AGENT_ADDENDUM
    assert "workspace_id" in CODING_AGENT_ADDENDUM
    assert "start_coding_job" in CODING_AGENT_ADDENDUM
    assert "get_coding_job" in CODING_AGENT_ADDENDUM
    assert "cancel_coding_job" in CODING_AGENT_ADDENDUM
    assert "do not name Cursor" in CODING_AGENT_ADDENDUM
    assert "do not block" in CODING_AGENT_ADDENDUM.lower()


def test_addendum_present_only_when_enabled() -> None:
    off = build_system_blocks(company_profile=None, mcp_enabled=False, coding_agents_enabled=False)
    on = build_system_blocks(company_profile=None, mcp_enabled=False, coding_agents_enabled=True)
    assert CODING_AGENT_ADDENDUM not in off[0]["text"]
    assert "## Coding Jobs" not in off[0]["text"]
    assert CODING_AGENT_ADDENDUM in on[0]["text"]
    assert "start_coding_job" in on[0]["text"]


def test_coding_jobs_eval_scenario_exists() -> None:
    from pathlib import Path

    import yaml

    from openexecutive.evals import scenarios as scenarios_mod

    path = Path(scenarios_mod.__file__).parent / "_scenarios" / "coding_jobs_001.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["id"] == "coding_jobs_001"
    assert raw["expected_tool_calls"] == ["start_coding_job"]
    assert "workspace_id_is_slug_not_path" in raw["quality_criteria"]
    assert "does_not_name_cursor_or_opencode" in raw["quality_criteria"]
