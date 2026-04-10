import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import cli as cli_module
from cli import HermesCLI


class _FakeBuffer:
    def __init__(self, text=""):
        self.text = text
        self.cursor_position = len(text)

    def reset(self, append_to_history=False):
        self.text = ""
        self.cursor_position = 0


def _make_cli_stub():
    cli = HermesCLI.__new__(HermesCLI)
    cli._clarify_state = None
    cli._clarify_freetext = False
    cli._clarify_deadline = 0
    cli._clarify_last_typing_at = 0.0
    cli._clarify_wait_mode_default = "wait"
    cli._clarify_auto_timeout_seconds = 1
    cli._invalidate = MagicMock()
    cli._app = SimpleNamespace(invalidate=MagicMock(), current_buffer=_FakeBuffer())
    cli.agent = MagicMock()
    cli.agent.set_wait_state = MagicMock()
    cli.agent.clear_wait_state = MagicMock()
    return cli


class TestCliClarifyUi:
    def test_wait_mode_blocks_until_response(self):
        cli = _make_cli_stub()
        cli._clarify_wait_mode_default = "wait"
        cli._clarify_auto_timeout_seconds = 1
        result = {}

        def _run_callback():
            result["value"] = cli._clarify_callback("Pick one", ["a", "b"])

        thread = threading.Thread(target=_run_callback, daemon=True)
        thread.start()

        deadline = time.time() + 2
        while cli._clarify_state is None and time.time() < deadline:
            time.sleep(0.01)

        assert cli._clarify_state is not None
        time.sleep(1.2)
        assert thread.is_alive(), "WAIT mode should not auto-time-out"

        cli._clarify_state["response_queue"].put("a")
        thread.join(timeout=2)
        assert result["value"] == "a"

    def test_auto_mode_times_out_without_response(self):
        cli = _make_cli_stub()
        cli._clarify_wait_mode_default = "auto"
        cli._clarify_auto_timeout_seconds = 1
        result = {}

        def _run_callback():
            result["value"] = cli._clarify_callback("Pick one", ["a", "b"])

        with patch.object(cli_module, "_cprint"):
            thread = threading.Thread(target=_run_callback, daemon=True)
            thread.start()
            thread.join(timeout=3)

        assert not thread.is_alive()
        assert "best judgement" in result["value"]

    def test_set_clarify_wait_mode_persists_and_updates_active_prompt(self):
        cli = _make_cli_stub()
        cli._clarify_state = {
            "question": "What now?",
            "choices": ["a"],
            "selected": 0,
            "response_queue": None,
            "wait_mode": "wait",
        }

        with patch.object(cli_module, "save_config_value", return_value=True) as mock_save:
            mode = cli._set_clarify_wait_mode("auto")

        assert mode == "auto"
        assert cli._clarify_wait_mode_default == "auto"
        assert cli._clarify_state["wait_mode"] == "auto"
        mock_save.assert_called_once_with("clarify.default_wait_mode", "auto")
        cli.agent.set_wait_state.assert_called()
