"""YAML allowlist + Settings inference for coding agents."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from openexecutive.coding_agents.file_config import (
    CodingAgentsConfigError,
    get_workspace,
    load_coding_agents_file,
    resolve_runtime_choice,
    validate_workspace_path,
)
from openexecutive.config import Settings, coding_agents_config_file_present


@pytest.fixture(autouse=True)
def _isolate_audit_and_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = tmp_path / "episodic.db"
    monkeypatch.setattr("openexecutive.memory.episodic.DB_PATH", db)
    monkeypatch.setattr("openexecutive.coding_agents.store.DB_PATH", db)
    monkeypatch.setattr("openexecutive.audit.log_event", lambda *a, **k: None)
    monkeypatch.setattr("openexecutive.coding_agents.audit.log_event", lambda *a, **k: None)


def _yaml(tmp_path: Path, workspace: Path, **extra: object) -> Path:
    doc = {
        "runtimes": {
            "cursor": {"enabled": True, "binary": "agent"},
            "opencode": {"enabled": True, "binary": "opencode", "serve_url": ""},
        },
        "workspaces": [
            {
                "id": "product",
                "path": str(workspace),
                "default_runtime": "cursor",
            }
        ],
    }
    doc.update(extra)
    path = tmp_path / "coding_agents.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    monkeypatch.delenv("CODING_AGENTS_ENABLED", raising=False)
    monkeypatch.delenv("CODING_AGENTS_CONFIG_PATH", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_load_valid_yaml(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    path = _yaml(tmp_path, workspace)
    config = load_coding_agents_file(path)
    assert config is not None
    assert config.workspace_by_id("product") is not None
    assert config.workspace_by_id("product").path == workspace


def test_missing_file_returns_none(tmp_path: Path) -> None:
    assert load_coding_agents_file(tmp_path / "absent.yaml") is None


def test_empty_file_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    assert load_coding_agents_file(path) is None
    assert coding_agents_config_file_present(path) is False


def test_duplicate_workspace_ids_fail_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    path = tmp_path / "dup.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "runtimes": {
                    "cursor": {"enabled": True, "binary": "agent"},
                    "opencode": {"enabled": True, "binary": "opencode"},
                },
                "workspaces": [
                    {"id": "product", "path": str(workspace)},
                    {"id": "product", "path": str(workspace)},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert load_coding_agents_file(path) is None


def test_path_like_workspace_ids_in_yaml_fail_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    for bad_id in ("../x", "foo/bar"):
        path = tmp_path / "bad_id.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "runtimes": {
                        "cursor": {"enabled": True, "binary": "agent"},
                        "opencode": {"enabled": True, "binary": "opencode"},
                    },
                    "workspaces": [{"id": bad_id, "path": str(workspace)}],
                }
            ),
            encoding="utf-8",
        )
        assert load_coding_agents_file(path) is None, bad_id


def test_invalid_yaml_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("runtimes: [unterminated", encoding="utf-8")
    assert load_coding_agents_file(path) is None


def test_unknown_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    config = load_coding_agents_file(_yaml(tmp_path, workspace))
    assert config is not None
    with pytest.raises(CodingAgentsConfigError) as exc:
        get_workspace(config, "nope")
    assert exc.value.code == "unknown_workspace"


def test_workspace_id_must_not_look_like_a_path(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    config = load_coding_agents_file(_yaml(tmp_path, workspace))
    assert config is not None
    with pytest.raises(CodingAgentsConfigError) as exc:
        get_workspace(config, "/repos/product")
    assert exc.value.code == "unknown_workspace"


def test_path_must_be_a_directory(tmp_path: Path) -> None:
    file_path = tmp_path / "not-a-dir"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(CodingAgentsConfigError) as exc:
        validate_workspace_path(file_path)
    assert exc.value.code == "invalid_workspace_path"


def test_missing_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(CodingAgentsConfigError) as exc:
        validate_workspace_path(tmp_path / "absent")
    assert exc.value.code == "invalid_workspace_path"


def test_dotdot_path_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / "repo"
    real.mkdir()
    sneaky = tmp_path / "repo" / ".." / "repo"
    with pytest.raises(CodingAgentsConfigError) as exc:
        validate_workspace_path(sneaky)
    assert ".." in str(exc.value)


def test_symlink_workspace_itself_is_ok(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    resolved = validate_workspace_path(link)
    assert resolved == real.resolve()
    assert resolved.is_dir()


def test_disabled_runtime(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    path = _yaml(
        tmp_path,
        workspace,
        runtimes={
            "cursor": {"enabled": False, "binary": "agent"},
            "opencode": {"enabled": True, "binary": "opencode"},
        },
    )
    config = load_coding_agents_file(path)
    assert config is not None
    ws = get_workspace(config, "product")
    with pytest.raises(CodingAgentsConfigError) as exc:
        resolve_runtime_choice(config, ws, "cursor")
    assert exc.value.code == "runtime_disabled"


def test_unset_is_inferred_from_nonempty_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    cfg = _yaml(tmp_path, workspace)
    settings = _settings(monkeypatch, CODING_AGENTS_CONFIG_PATH=str(cfg))
    assert settings.coding_agents_enabled is True
    assert settings.coding_agents_auto_enabled is True


def test_explicit_false_survives_existing_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    cfg = _yaml(tmp_path, workspace)
    settings = _settings(
        monkeypatch,
        CODING_AGENTS_ENABLED="false",
        CODING_AGENTS_CONFIG_PATH=str(cfg),
    )
    assert settings.coding_agents_enabled is False
    assert settings.coding_agents_auto_enabled is False


def test_missing_yaml_stays_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, CODING_AGENTS_CONFIG_PATH=str(tmp_path / "absent.yaml"))
    assert settings.coding_agents_enabled is False


def test_empty_yaml_does_not_enable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "coding_agents.yaml"
    cfg.write_text("", encoding="utf-8")
    settings = _settings(monkeypatch, CODING_AGENTS_CONFIG_PATH=str(cfg))
    assert settings.coding_agents_enabled is False


def test_directory_at_config_path_does_not_enable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "coding_agents.yaml"
    cfg.mkdir()
    settings = _settings(monkeypatch, CODING_AGENTS_CONFIG_PATH=str(cfg))
    assert settings.coding_agents_enabled is False


def test_explicit_true_survives_missing_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(
        monkeypatch,
        CODING_AGENTS_ENABLED="true",
        CODING_AGENTS_CONFIG_PATH=str(tmp_path / "absent.yaml"),
    )
    assert settings.coding_agents_enabled is True


def test_relative_config_path_resolves_against_cwd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(monkeypatch, CODING_AGENTS_CONFIG_PATH="company/coding_agents.yaml")
    assert settings.coding_agents_config_path.is_absolute()
    assert settings.coding_agents_config_path == Path.cwd() / "company/coding_agents.yaml"
