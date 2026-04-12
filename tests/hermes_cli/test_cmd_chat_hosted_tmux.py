from types import SimpleNamespace
from unittest.mock import patch

from hermes_cli.main import cmd_chat


def _args():
    return SimpleNamespace(
        query=None,
        resume=None,
        continue_last=None,
        model="gpt-test",
        provider="openai",
        toolsets="core",
        skills=["obsidian"],
        verbose=False,
        quiet=False,
        image=None,
        worktree=False,
        checkpoints=False,
        pass_session_id=False,
        max_turns=None,
        yolo=False,
        source=None,
    )


def test_cmd_chat_uses_hosted_tmux_mode_for_interactive_gateway_sessions():
    args = _args()
    with patch.dict("os.environ", {"HERMES_GATEWAY_SESSION_MODE": "1", "HERMES_HOSTED_TMUX_MODE": "1"}, clear=False), \
         patch("sys.stdin.isatty", return_value=True), \
         patch("hermes_cli.main._has_any_provider_configured", return_value=True), \
         patch("hermes_cli.hosted_tmux_client.run_hosted_tmux_chat") as mock_tmux_chat:
        cmd_chat(args)

    mock_tmux_chat.assert_called_once_with(args)
