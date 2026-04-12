"""Tests for HermesCLI initialization -- catches configuration bugs
that only manifest at runtime (not in mocked unit tests)."""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.display import get_tool_emoji

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_cli(env_overrides=None, config_overrides=None, **kwargs):
    """Create a HermesCLI instance with minimal mocking."""
    import importlib

    _clean_config = {
        "model": {
            "default": "anthropic/claude-opus-4.6",
            "base_url": "https://openrouter.ai/api/v1",
            "provider": "auto",
        },
        "display": {"compact": False, "tool_progress": "all"},
        "agent": {},
        "terminal": {"env_type": "local"},
    }
    if config_overrides:
        _clean_config.update(config_overrides)
    clean_env = {"LLM_MODEL": "", "HERMES_MAX_ITERATIONS": ""}
    if env_overrides:
        clean_env.update(env_overrides)
    prompt_toolkit_stubs = {
        "prompt_toolkit": MagicMock(),
        "prompt_toolkit.history": MagicMock(),
        "prompt_toolkit.styles": MagicMock(),
        "prompt_toolkit.patch_stdout": MagicMock(),
        "prompt_toolkit.application": MagicMock(),
        "prompt_toolkit.layout": MagicMock(),
        "prompt_toolkit.layout.processors": MagicMock(),
        "prompt_toolkit.filters": MagicMock(),
        "prompt_toolkit.layout.dimension": MagicMock(),
        "prompt_toolkit.layout.menus": MagicMock(),
        "prompt_toolkit.widgets": MagicMock(),
        "prompt_toolkit.key_binding": MagicMock(),
        "prompt_toolkit.completion": MagicMock(),
        "prompt_toolkit.formatted_text": MagicMock(),
        "prompt_toolkit.auto_suggest": MagicMock(),
    }
    with patch.dict(sys.modules, prompt_toolkit_stubs), \
         patch.dict("os.environ", clean_env, clear=False):
        import cli as _cli_mod
        _cli_mod = importlib.reload(_cli_mod)
        with patch.object(_cli_mod, "get_tool_definitions", return_value=[]), \
             patch.dict(_cli_mod.__dict__, {"CLI_CONFIG": _clean_config}):
            return _cli_mod.HermesCLI(**kwargs)


class TestMaxTurnsResolution:
    """max_turns must always resolve to a positive integer, never None."""

    def test_default_max_turns_is_integer(self):
        cli = _make_cli()
        assert isinstance(cli.max_turns, int)
        assert cli.max_turns == 90

    def test_explicit_max_turns_honored(self):
        cli = _make_cli(max_turns=25)
        assert cli.max_turns == 25

    def test_none_max_turns_gets_default(self):
        cli = _make_cli(max_turns=None)
        assert isinstance(cli.max_turns, int)
        assert cli.max_turns == 90

    def test_env_var_max_turns(self):
        """Env var is used when config file doesn't set max_turns."""
        cli_obj = _make_cli(env_overrides={"HERMES_MAX_ITERATIONS": "42"})
        assert cli_obj.max_turns == 42

    def test_legacy_root_max_turns_is_used_when_agent_key_exists_without_value(self):
        cli_obj = _make_cli(config_overrides={"agent": {}, "max_turns": 77})
        assert cli_obj.max_turns == 77

    def test_max_turns_never_none_for_agent(self):
        """The value passed to AIAgent must never be None (causes TypeError in run_conversation)."""
        cli = _make_cli()
        assert isinstance(cli.max_turns, int) and cli.max_turns == 90


class TestVerboseAndToolProgress:
    def test_default_verbose_is_bool(self):
        cli = _make_cli()
        assert isinstance(cli.verbose, bool)

    def test_tool_progress_mode_is_string(self):
        cli = _make_cli()
        assert isinstance(cli.tool_progress_mode, str)
        assert cli.tool_progress_mode in ("off", "new", "all", "verbose")


class TestGatewayHostedMode:
    def test_gateway_session_mode_flag_is_stored(self):
        cli = _make_cli(gateway_session_mode=True)
        assert cli.gateway_session_mode is True

    def test_forced_session_id_env_is_used_for_new_session(self):
        cli = _make_cli(env_overrides={"HERMES_FORCED_SESSION_ID": "forced_session_123"})
        assert cli.session_id == "forced_session_123"
        assert cli._resumed is False

    def test_init_agent_uses_hosted_proxy_when_gateway_session_mode_enabled(self):
        from types import SimpleNamespace

        cli = _make_cli(gateway_session_mode=True)

        fake_endpoint = SimpleNamespace(base_url="http://127.0.0.1:8642", api_key=None)
        fake_proxy = SimpleNamespace(
            session_id=cli.session_id,
            model=cli.model,
            context_compressor=SimpleNamespace(context_length=None),
            verbose_logging=False,
            quiet_mode=True,
        )

        with patch("hermes_cli.hosted_session_client.ensure_hosted_session_bridge", return_value=fake_endpoint), \
             patch("hermes_cli.hosted_session_client.HostedSessionAgentProxy", return_value=fake_proxy) as mock_proxy:
            assert cli._init_agent() is True

        assert cli.agent is fake_proxy
        mock_proxy.assert_called_once()


class TestToolProgressRendering:
    def test_tool_progress_new_mode_prints_new_tools_once(self):
        cli = _make_cli(config_overrides={"display": {"tool_progress": "new"}})
        printed = []
        cli._invalidate = lambda *args, **kwargs: None
        globals_dict = cli._on_tool_progress.__globals__
        original_cprint = globals_dict["_cprint"]
        globals_dict["_cprint"] = printed.append
        try:
            cli._on_tool_progress("tool.started", "read_file", "README.md", {"path": "README.md"})
            cli._on_tool_progress("tool.started", "read_file", "README.md", {"path": "README.md"})
            cli._on_tool_progress("tool.completed", "read_file", None, None, duration=0.2, is_error=False)
        finally:
            globals_dict["_cprint"] = original_cprint

        assert printed == [f"  ┊ {get_tool_emoji('read_file')} README.md"]

    def test_tool_progress_all_mode_prints_completion_and_subagent_updates(self):
        cli = _make_cli(config_overrides={"display": {"tool_progress": "all"}})
        printed = []
        cli._invalidate = lambda *args, **kwargs: None
        globals_dict = cli._on_tool_progress.__globals__
        original_cprint = globals_dict["_cprint"]
        globals_dict["_cprint"] = printed.append
        try:
            cli._on_tool_progress("tool.started", "write_file", "notes.txt", {"path": "notes.txt"})
            cli._on_tool_progress("subagent.progress", "delegate_task", "child 1/2")
            cli._on_tool_progress("tool.completed", "write_file", None, None, duration=0.2, is_error=False)
        finally:
            globals_dict["_cprint"] = original_cprint

        assert printed == [
            f"  ┊ {get_tool_emoji('write_file')} notes.txt",
            "  ┊ 🤖 child 1/2",
            "  ┊ ✅ write_file (0.2s)",
        ]


class TestSessionSwitcher:
    def test_list_switchable_sessions_marks_current_session(self):
        cli = _make_cli()
        cli.session_id = "current"
        cli._session_db = MagicMock()
        cli._session_db.list_sessions_rich.return_value = [
            {"id": "current", "title": "Current title", "preview": "", "source": "api_server"},
            {"id": "other", "title": "Other title", "preview": "", "source": "api_server"},
        ]

        sessions = cli._list_switchable_sessions(limit=50)

        assert sessions[0]["title"].startswith("● ")
        assert sessions[1]["title"] == "Other title"

    def test_list_switchable_sessions_uses_live_gateway_sessions_when_hosted(self):
        cli = _make_cli(gateway_session_mode=True)
        cli.session_id = "current"
        cli.agent = MagicMock()
        cli.agent.list_live_sessions.return_value = [
            {"id": "current", "title": "Current live", "preview": "", "source": "live"},
            {"id": "other", "title": "Other live", "preview": "", "source": "live"},
        ]
        cli._session_db = MagicMock()

        sessions = cli._list_switchable_sessions(limit=25)

        cli.agent.list_live_sessions.assert_called_once_with(limit=25)
        cli._session_db.list_sessions_rich.assert_not_called()
        assert sessions[0]["title"].startswith("● ")
        assert sessions[1]["title"] == "Other live"

    def test_browse_and_swap_session_uses_picker_and_resume(self):
        cli = _make_cli()
        cli._agent_running = False
        cli._session_db = MagicMock()
        cli._session_db.list_sessions_rich.return_value = [
            {"id": "current", "title": "Current title", "preview": "", "source": "api_server"},
            {"id": "other", "title": "Other title", "preview": "", "source": "api_server"},
        ]
        cli._handle_resume_command = MagicMock()

        with patch("hermes_cli.main._session_browse_picker", return_value="other"):
            assert cli._browse_and_swap_session() is True

        cli._handle_resume_command.assert_called_once_with("/resume other")

    def test_browse_and_swap_session_requires_idle_agent(self):
        cli = _make_cli()
        cli._agent_running = True
        cli._session_db = MagicMock()
        cli._session_db.list_sessions_rich.return_value = [{"id": "other", "title": "Other", "preview": "", "source": "api_server"}]
        printed = []
        globals_dict = cli._browse_and_swap_session.__globals__
        original_cprint = globals_dict["_cprint"]
        globals_dict["_cprint"] = printed.append
        try:
            assert cli._browse_and_swap_session() is False
        finally:
            globals_dict["_cprint"] = original_cprint

        assert any("Interrupt current work first" in msg for msg in printed)

    def test_list_switchable_sessions_uses_live_tmux_sessions_when_hosted_tmux_child(self):
        cli = _make_cli()
        cli.session_id = "stale-local"
        cli._session_db = MagicMock()
        fake_manager = MagicMock()
        fake_manager.list_sessions.return_value = [
            SimpleNamespace(session_id="sess_a", title="Session A", preview="ready", status="running", attached_clients=0, last_active=10.0),
            SimpleNamespace(session_id="sess_b", title="Session B", preview="idle", status="running", attached_clients=0, last_active=5.0),
        ]
        fake_manager.current_session_id.return_value = "sess_a"

        with patch.dict("os.environ", {"HERMES_HOSTED_TMUX_CHILD": "1"}, clear=False), \
             patch("gateway.hosted_tmux.HostedTmuxManager", return_value=fake_manager):
            sessions = cli._list_switchable_sessions(limit=12)

        cli._session_db.list_sessions_rich.assert_not_called()
        assert [row["id"] for row in sessions] == ["sess_a", "sess_b"]
        assert sessions[0]["title"].startswith("● ")

    def test_browse_and_swap_session_switches_tmux_target_when_hosted_tmux_child(self):
        cli = _make_cli()
        cli._agent_running = False
        cli._switch_hosted_tmux_session = MagicMock(return_value=True)
        fake_manager = MagicMock()
        fake_manager.current_session_id.return_value = "sess_a"
        with patch.dict("os.environ", {"HERMES_HOSTED_TMUX_CHILD": "1"}, clear=False), \
             patch.object(cli, "_list_hosted_tmux_sessions", return_value=[
                 {"id": "sess_a", "title": "Session A", "preview": "ready", "source": "hosted_tmux"},
                 {"id": "sess_b", "title": "Session B", "preview": "idle", "source": "hosted_tmux"},
             ]), patch("gateway.hosted_tmux.HostedTmuxManager", return_value=fake_manager), \
             patch("hermes_cli.main._session_browse_picker", return_value="sess_b"):
            assert cli._browse_and_swap_session() is True

        cli._switch_hosted_tmux_session.assert_called_once_with("sess_b")

    def test_process_command_new_spawns_new_hosted_tmux_session_when_child(self):
        cli = _make_cli()
        cli._spawn_new_hosted_tmux_session = MagicMock(return_value=True)

        with patch.dict("os.environ", {"HERMES_HOSTED_TMUX_CHILD": "1"}, clear=False):
            cli.process_command("/new")

        cli._spawn_new_hosted_tmux_session.assert_called_once_with()

    def test_process_command_resume_switches_tmux_session_when_child(self):
        cli = _make_cli()
        cli._switch_hosted_tmux_session = MagicMock(return_value=True)

        with patch.dict("os.environ", {"HERMES_HOSTED_TMUX_CHILD": "1"}, clear=False), \
             patch("hermes_cli.main._resolve_session_by_name_or_id", return_value="sess_b"):
            cli.process_command("/resume named session")

        cli._switch_hosted_tmux_session.assert_called_once_with("sess_b")


class TestBusyInputMode:
    def test_default_busy_input_mode_is_interrupt(self):
        cli = _make_cli()
        assert cli.busy_input_mode == "interrupt"

    def test_busy_input_mode_queue_is_honored(self):
        cli = _make_cli(config_overrides={"display": {"busy_input_mode": "queue"}})
        assert cli.busy_input_mode == "queue"

    def test_unknown_busy_input_mode_falls_back_to_interrupt(self):
        cli = _make_cli(config_overrides={"display": {"busy_input_mode": "bogus"}})
        assert cli.busy_input_mode == "interrupt"

    def test_queue_command_works_while_busy(self):
        """When agent is running, /queue should stage the prompt for the next tool boundary."""
        cli = _make_cli()
        cli._agent_running = True
        cli.process_command("/queue follow up")
        assert cli._tool_boundary_input_queue.get_nowait() == "follow up"

    def test_queue_command_works_while_idle(self):
        """When agent is idle, /queue should still queue (not reject)."""
        cli = _make_cli()
        cli._agent_running = False
        cli.process_command("/queue follow up")
        assert cli._pending_input.get_nowait() == "follow up"

    def test_queue_mode_routes_busy_enter_to_pending(self):
        """In queue mode, Enter while busy should go to _pending_input, not _interrupt_queue."""
        cli = _make_cli(config_overrides={"display": {"busy_input_mode": "queue"}})
        cli._agent_running = True
        # Simulate what handle_enter does for non-command input while busy
        text = "follow up"
        if cli.busy_input_mode == "queue":
            cli._pending_input.put(text)
        else:
            cli._interrupt_queue.put(text)
        assert cli._pending_input.get_nowait() == "follow up"
        assert cli._interrupt_queue.empty()

    def test_interrupt_mode_routes_busy_enter_to_tool_boundary_queue(self):
        """In interrupt mode (default), the first busy Enter stages a follow-up at the next tool boundary."""
        cli = _make_cli()
        cli._agent_running = True
        cli._submit_busy_input("redirect")
        assert cli._tool_boundary_input_queue.get_nowait() == "redirect"
        assert cli._interrupt_queue.empty()
        assert cli._pending_input.empty()


class TestSingleQueryState:
    def test_voice_and_interrupt_state_initialized_before_run(self):
        """Single-query mode calls chat() without going through run()."""
        cli = _make_cli()
        assert cli._voice_tts is False
        assert cli._voice_mode is False
        assert cli._voice_tts_done.is_set()
        assert hasattr(cli, "_interrupt_queue")
        assert hasattr(cli, "_pending_input")


class TestHistoryDisplay:
    def test_history_numbers_only_visible_messages_and_summarizes_tools(self, capsys):
        cli = _make_cli()
        cli.conversation_history = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "Hello"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call_1"}, {"id": "call_2"}],
            },
            {"role": "tool", "content": "tool output 1"},
            {"role": "tool", "content": "tool output 2"},
            {"role": "assistant", "content": "All set."},
            {"role": "user", "content": "A" * 250},
        ]

        cli.show_history()
        output = capsys.readouterr().out

        assert "[You #1]" in output
        assert "[Hermes #2]" in output
        assert "(requested 2 tool calls)" in output
        assert "[Tools]" in output
        assert "(2 tool messages hidden)" in output
        assert "[Hermes #3]" in output
        assert "[You #4]" in output
        assert "[You #5]" not in output
        assert "A" * 250 in output
        assert "A" * 250 + "..." not in output

    def test_history_shows_recent_sessions_when_current_chat_is_empty(self, capsys):
        cli = _make_cli()
        cli.session_id = "current"
        cli._session_db = MagicMock()
        cli._session_db.list_sessions_rich.return_value = [
            {
                "id": "current",
                "title": "Current",
                "preview": "Current preview",
                "last_active": 0,
            },
            {
                "id": "20260401_201329_d85961",
                "title": "Checking Running Hermes Agent",
                "preview": "check running gateways for hermes agent",
                "last_active": 0,
            },
        ]

        cli.show_history()
        output = capsys.readouterr().out

        assert "No messages in the current chat yet" in output
        assert "Checking Running Hermes Agent" in output
        assert "20260401_201329_d85961" in output
        assert "/resume" in output
        assert "Current preview" not in output

    def test_resume_without_target_lists_recent_sessions(self, capsys):
        cli = _make_cli()
        cli.session_id = "current"
        cli._session_db = MagicMock()
        cli._session_db.list_sessions_rich.return_value = [
            {
                "id": "current",
                "title": "Current",
                "preview": "Current preview",
                "last_active": 0,
            },
            {
                "id": "20260401_201329_d85961",
                "title": "Checking Running Hermes Agent",
                "preview": "check running gateways for hermes agent",
                "last_active": 0,
            },
        ]

        cli._handle_resume_command("/resume")
        output = capsys.readouterr().out

        assert "Recent sessions" in output
        assert "Checking Running Hermes Agent" in output
        assert "Use /resume <session id or title> to continue" in output

    def test_resume_can_materialize_live_gateway_session_missing_from_db(self):
        cli = _make_cli(gateway_session_mode=True)
        cli.session_id = "current"
        cli._session_db = MagicMock()
        cli._session_db.get_session.side_effect = [
            None,
            {"id": "live_other", "title": None, "source": "live"},
        ]
        cli._session_db.get_messages_as_conversation.return_value = []
        cli.agent = MagicMock()
        cli.agent.list_live_sessions.return_value = [
            {"id": "live_other", "title": "Other live", "source": "live", "model": "gpt-test"},
        ]

        with patch("hermes_cli.main._resolve_session_by_name_or_id", return_value="live_other"):
            cli._handle_resume_command("/resume live_other")

        cli._session_db.ensure_session.assert_called_once_with("live_other", source="live", model="gpt-test")
        cli.agent.switch_session.assert_called_once_with("live_other")


class TestRootLevelProviderOverride:
    """Root-level provider/base_url in config.yaml must NOT override model.provider."""

    def test_model_provider_wins_over_root_provider(self, tmp_path, monkeypatch):
        """model.provider takes priority — root-level provider is only a fallback."""
        import yaml

        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))

        config_path = hermes_home / "config.yaml"
        config_path.write_text(yaml.safe_dump({
            "provider": "opencode-go",  # stale root-level key
            "model": {
                "default": "google/gemini-3-flash-preview",
                "provider": "openrouter",  # correct canonical key
            },
        }))

        import cli
        monkeypatch.setattr(cli, "_hermes_home", hermes_home)
        cfg = cli.load_cli_config()

        assert cfg["model"]["provider"] == "openrouter"

    def test_root_provider_ignored_when_default_model_provider_exists(self, tmp_path, monkeypatch):
        """Even when model.provider is the default 'auto', root-level provider is ignored."""
        import yaml

        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))

        config_path = hermes_home / "config.yaml"
        config_path.write_text(yaml.safe_dump({
            "provider": "opencode-go",  # stale root key
            "model": {
                "default": "google/gemini-3-flash-preview",
                # no explicit model.provider — defaults provide "auto"
            },
        }))

        import cli
        monkeypatch.setattr(cli, "_hermes_home", hermes_home)
        cfg = cli.load_cli_config()

        # Root-level "opencode-go" must NOT leak through
        assert cfg["model"]["provider"] != "opencode-go"

    def test_normalize_root_model_keys_moves_to_model(self):
        """_normalize_root_model_keys migrates root keys into model section."""
        from hermes_cli.config import _normalize_root_model_keys

        config = {
            "provider": "opencode-go",
            "base_url": "https://example.com/v1",
            "model": {
                "default": "some-model",
            },
        }
        result = _normalize_root_model_keys(config)
        # Root keys removed
        assert "provider" not in result
        assert "base_url" not in result
        # Migrated into model section
        assert result["model"]["provider"] == "opencode-go"
        assert result["model"]["base_url"] == "https://example.com/v1"

    def test_normalize_root_model_keys_does_not_override_existing(self):
        """Existing model.provider is never overridden by root-level key."""
        from hermes_cli.config import _normalize_root_model_keys

        config = {
            "provider": "stale-provider",
            "model": {
                "default": "some-model",
                "provider": "correct-provider",
            },
        }
        result = _normalize_root_model_keys(config)
        assert result["model"]["provider"] == "correct-provider"
        assert "provider" not in result  # root key still cleaned up


class TestProviderResolution:
    def test_api_key_is_string_or_none(self):
        cli = _make_cli()
        assert cli.api_key is None or isinstance(cli.api_key, str)

    def test_base_url_is_string(self):
        cli = _make_cli()
        assert isinstance(cli.base_url, str)
        assert cli.base_url.startswith("http")

    def test_model_is_string(self):
        cli = _make_cli()
        assert isinstance(cli.model, str)
        assert isinstance(cli.model, str) and '/' in cli.model
