import subprocess
from types import SimpleNamespace

from gateway.hosted_tmux import HostedTmuxManager


class _FakeRunner:
    def __init__(self):
        self.calls = []
        self.sessions = set()

    def __call__(self, cmd, check=True, capture_output=True, text=True):
        self.calls.append(cmd)
        if "has-session" in cmd:
            target = cmd[-1]
            if target in self.sessions:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            raise subprocess.CalledProcessError(1, cmd, "", "")
        if "new-session" in cmd:
            target = cmd[cmd.index("-s") + 1]
            self.sessions.add(target)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if "list-sessions" in cmd:
            rows = [f"{name}\t0\t10\t1" for name in sorted(self.sessions)]
            return subprocess.CompletedProcess(cmd, 0, "\n".join(rows), "")
        if "capture-pane" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "ready\n", "")
        if "kill-session" in cmd:
            target = cmd[-1]
            self.sessions.discard(target)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


class _FakeDB:
    def __init__(self, existing=None):
        self._existing = dict(existing or {})

    def get_session(self, session_id):
        return self._existing.get(session_id)


def test_build_launch_command_uses_real_chat_child_env(monkeypatch):
    runner = _FakeRunner()
    manager = HostedTmuxManager(runner=runner)
    monkeypatch.setattr(manager, "is_available", lambda: True)
    cmd = manager._build_launch_command(
        session_id="sess_1",
        resume_existing=False,
        model="gpt-test",
        provider="openai",
        toolsets=["core", "web"],
        skills=["obsidian"],
        pass_session_id=True,
        max_turns=55,
        checkpoints=True,
    )

    assert "HERMES_GATEWAY_SESSION_MODE=0" in cmd
    assert "HERMES_HOSTED_TMUX_CHILD=1" in cmd
    assert "HERMES_HOSTED_TMUX_SOCKET=" in cmd
    assert "HERMES_HOSTED_TMUX_TARGET=hermes-hosted-sess_1" in cmd
    assert "HERMES_HOSTED_TMUX_SESSION_ID=sess_1" in cmd
    assert "HERMES_FORCED_SESSION_ID=sess_1" in cmd
    assert "python" in cmd
    assert "hermes_cli.main chat" in cmd
    assert "--toolsets core,web" in cmd
    assert "--skills obsidian" in cmd
    assert "--max-turns 55" in cmd


def test_ensure_session_creates_and_reattaches_current_tmux_session(monkeypatch):
    runner = _FakeRunner()
    manager = HostedTmuxManager(runner=runner)
    monkeypatch.setattr(manager, "is_available", lambda: True)
    monkeypatch.setattr(manager, "_db", lambda: _FakeDB({}))

    session = manager.ensure_session(model="gpt-test")
    assert session.created is True
    assert session.session_id
    assert manager.current_session_id() == session.session_id

    reused = manager.ensure_session(model="gpt-test")
    assert reused.session_id == session.session_id
    assert reused.created is False

    rows = manager.list_sessions(limit=10)
    assert [row.session_id for row in rows] == [session.session_id]
    assert rows[0].preview == "ready"


def test_ensure_session_resume_requires_existing_db_session(monkeypatch):
    runner = _FakeRunner()
    manager = HostedTmuxManager(runner=runner)
    monkeypatch.setattr(manager, "is_available", lambda: True)
    monkeypatch.setattr(manager, "_db", lambda: _FakeDB({}))

    try:
        manager.ensure_session(requested_session_id="missing-session")
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "not found" in str(exc).lower()


def test_close_session_kills_tmux_session(monkeypatch):
    runner = _FakeRunner()
    manager = HostedTmuxManager(runner=runner)
    monkeypatch.setattr(manager, "is_available", lambda: True)
    runner.sessions.add(manager._session_target("sess_close"))

    assert manager.close_session("sess_close") is True
    assert manager.list_sessions(limit=10) == []


def test_close_session_promotes_next_live_session(monkeypatch):
    runner = _FakeRunner()
    manager = HostedTmuxManager(runner=runner)
    monkeypatch.setattr(manager, "is_available", lambda: True)
    runner.sessions.add(manager._session_target("sess_a"))
    runner.sessions.add(manager._session_target("sess_b"))
    manager.mark_current_session("sess_a")

    assert manager.close_session("sess_a") is True
    assert manager.current_session_id() == "sess_b"
