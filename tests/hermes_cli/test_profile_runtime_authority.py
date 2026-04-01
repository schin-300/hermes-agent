from pathlib import Path

import pytest

import hermes_cli.main as hermes_main


class _ExecCalled(RuntimeError):
    pass


def _make_worktree(path: Path) -> Path:
    (path / "hermes_cli").mkdir(parents=True)
    (path / "hermes_cli" / "main.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    return path


def test_discover_profile_from_env_prefers_profile_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    default_home = tmp_path / ".hermes"
    profile_home = default_home / "profiles" / "coder"
    profile_home.mkdir(parents=True)
    (default_home / "active_profile").write_text("other\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(profile_home))

    assert hermes_main._discover_profile_from_env_or_active() == "coder"


def test_discover_profile_from_env_falls_back_to_active_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    default_home = tmp_path / ".hermes"
    default_home.mkdir(parents=True)
    (default_home / "active_profile").write_text("coder\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(default_home))

    assert hermes_main._discover_profile_from_env_or_active() == "coder"


def test_maybe_reexec_attached_profile_source_execs_from_worktree(tmp_path, monkeypatch):
    shared_root = tmp_path / "shared"
    shared_root.mkdir()
    worktree = _make_worktree(tmp_path / "worktree")

    monkeypatch.setattr(hermes_main, "PROJECT_ROOT", shared_root)
    monkeypatch.setattr(hermes_main.sys, "argv", ["hermes", "profile"])
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.delenv(hermes_main._PROFILE_SOURCE_REEXEC_GUARD_ENV, raising=False)
    monkeypatch.setattr(
        "hermes_cli.self_improve.resolve_profile_attached_runtime_root",
        lambda profile_name: worktree,
    )

    captured = {}

    def _fake_execvpe(executable, args, env):
        captured["executable"] = executable
        captured["args"] = args
        captured["env"] = env
        raise _ExecCalled()

    monkeypatch.setattr(hermes_main.os, "execvpe", _fake_execvpe)

    with pytest.raises(_ExecCalled):
        hermes_main._maybe_reexec_attached_profile_source("coder")

    assert captured["executable"] == hermes_main.sys.executable
    assert captured["args"] == [
        hermes_main.sys.executable,
        str(worktree / "hermes_cli" / "main.py"),
        "profile",
    ]
    assert captured["env"][hermes_main._PROFILE_SOURCE_ROOT_ENV] == str(worktree)
    assert captured["env"][hermes_main._PROFILE_SOURCE_REEXEC_GUARD_ENV] == str(worktree)
    assert captured["env"]["PYTHONPATH"].split(hermes_main.os.pathsep)[0] == str(worktree)


def test_maybe_reexec_attached_profile_source_sets_source_env_when_already_authoritative(tmp_path, monkeypatch):
    worktree = _make_worktree(tmp_path / "worktree")
    monkeypatch.setattr(hermes_main, "PROJECT_ROOT", worktree)
    monkeypatch.setattr(hermes_main.sys, "argv", ["hermes", "profile"])
    monkeypatch.delenv(hermes_main._PROFILE_SOURCE_REEXEC_GUARD_ENV, raising=False)
    monkeypatch.setattr(
        "hermes_cli.self_improve.resolve_profile_attached_runtime_root",
        lambda profile_name: worktree,
    )
    monkeypatch.setattr(
        hermes_main.os,
        "execvpe",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("execvpe should not be called")),
    )

    hermes_main._maybe_reexec_attached_profile_source("coder")

    assert hermes_main.os.environ[hermes_main._PROFILE_SOURCE_ROOT_ENV] == str(worktree)


def test_maybe_reexec_attached_profile_source_honors_loop_guard(tmp_path, monkeypatch, capsys):
    shared_root = tmp_path / "shared"
    shared_root.mkdir()
    worktree = _make_worktree(tmp_path / "worktree")

    monkeypatch.setattr(hermes_main, "PROJECT_ROOT", shared_root)
    monkeypatch.setattr(hermes_main.sys, "argv", ["hermes", "profile"])
    monkeypatch.setenv(hermes_main._PROFILE_SOURCE_REEXEC_GUARD_ENV, str(worktree))
    monkeypatch.setattr(
        "hermes_cli.self_improve.resolve_profile_attached_runtime_root",
        lambda profile_name: worktree,
    )
    monkeypatch.setattr(
        hermes_main.os,
        "execvpe",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("execvpe should not be called")),
    )

    hermes_main._maybe_reexec_attached_profile_source("coder")

    stderr = capsys.readouterr().err
    assert "did not switch the source root" in stderr
