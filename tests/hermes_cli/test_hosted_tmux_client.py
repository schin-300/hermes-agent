from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hermes_cli.hosted_tmux_client import (
    attach_terminal_session,
    ensure_terminal_session,
    run_hosted_tmux_chat,
)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_ensure_terminal_session_posts_to_gateway():
    fake_endpoint = SimpleNamespace(base_url="http://127.0.0.1:8642", api_key=None)
    fake_post = MagicMock(return_value=_FakeResponse({
        "session_id": "sess_1",
        "tmux_target": "hermes-hosted-sess_1",
        "socket_path": "/tmp/hermes.sock",
        "created": True,
    }))

    with patch("hermes_cli.hosted_tmux_client.ensure_hosted_session_bridge", return_value=fake_endpoint), \
         patch("hermes_cli.hosted_tmux_client.requests.post", fake_post):
        payload = ensure_terminal_session(
            session_id="sess_1",
            model="gpt-test",
            provider="openai",
            toolsets="core,web",
            skills=["obsidian"],
            pass_session_id=True,
            max_turns=55,
            checkpoints=True,
        )

    assert payload["session_id"] == "sess_1"
    fake_post.assert_called_once()
    sent = fake_post.call_args.kwargs["json"]
    assert sent["session_id"] == "sess_1"
    assert sent["toolsets"] == ["core", "web"]
    assert sent["skills"] == ["obsidian"]
    assert sent["pass_session_id"] is True
    assert sent["max_turns"] == 55
    assert sent["checkpoints"] is True


def test_attach_terminal_session_uses_tmux_socket_and_unsets_nested_tmux():
    fake_run = MagicMock(return_value=SimpleNamespace(returncode=0))
    with patch("hermes_cli.hosted_tmux_client.subprocess.run", fake_run), \
         patch.dict("os.environ", {"TMUX": "/tmp/tmux-123/default,123,0"}, clear=False):
        code = attach_terminal_session(socket_path="/tmp/hermes.sock", tmux_target="hermes-hosted-sess_1")

    assert code == 0
    args = fake_run.call_args.args[0]
    assert args == ["tmux", "-S", "/tmp/hermes.sock", "attach-session", "-t", "hermes-hosted-sess_1"]
    env = fake_run.call_args.kwargs["env"]
    assert "TMUX" not in env


def test_run_hosted_tmux_chat_ensures_and_attaches():
    args = SimpleNamespace(
        resume="sess_1",
        model="gpt-test",
        provider="openai",
        toolsets="core",
        skills=["obsidian"],
        pass_session_id=False,
        max_turns=42,
        checkpoints=False,
    )
    with patch("hermes_cli.hosted_tmux_client.ensure_terminal_session", return_value={
        "session_id": "sess_1",
        "tmux_target": "hermes-hosted-sess_1",
        "socket_path": "/tmp/hermes.sock",
    }) as mock_ensure, \
         patch("hermes_cli.hosted_tmux_client.attach_terminal_session", return_value=0) as mock_attach:
        run_hosted_tmux_chat(args)

    mock_ensure.assert_called_once()
    mock_attach.assert_called_once_with(socket_path="/tmp/hermes.sock", tmux_target="hermes-hosted-sess_1")
