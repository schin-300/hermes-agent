import threading
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionEntry, SessionSource, build_session_key


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
    )


def _make_event(text: str) -> MessageEvent:
    return MessageEvent(text=text, source=_make_source(), message_id="m1")


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")})
    adapter = MagicMock()
    adapter.send = AsyncMock()
    adapter.resume_typing_for_chat = MagicMock()
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._background_tasks = set()
    runner._session_db = None
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._show_reasoning = False
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    return runner


def _clear_clarify_state():
    from tools import clarify_tool as mod
    mod._gateway_queues.clear()
    mod._gateway_notify_cbs.clear()


class TestGatewayClarifyState:
    def setup_method(self):
        _clear_clarify_state()

    def test_wait_for_gateway_clarify_unblocks(self):
        from tools.clarify_tool import (
            register_gateway_clarify_notify,
            unregister_gateway_clarify_notify,
            resolve_gateway_clarify,
            wait_for_gateway_clarify,
            has_blocking_gateway_clarify,
        )

        session_key = "clarify-session"
        register_gateway_clarify_notify(session_key, lambda data: None)
        result = {}

        def _run_wait():
            result["value"] = wait_for_gateway_clarify(
                question="Pick one",
                choices=["a", "b"],
                session_key=session_key,
            )

        thread = threading.Thread(target=_run_wait, daemon=True)
        thread.start()

        deadline = time.time() + 2
        while not has_blocking_gateway_clarify(session_key) and time.time() < deadline:
            time.sleep(0.01)

        assert has_blocking_gateway_clarify(session_key) is True
        assert resolve_gateway_clarify(session_key, "2") == 1
        thread.join(timeout=2)
        assert result["value"] == "b"
        unregister_gateway_clarify_notify(session_key)

    @pytest.mark.asyncio
    async def test_answer_command_resolves_pending_clarify(self):
        from tools.clarify_tool import _ClarifyEntry, _gateway_queues

        runner = _make_runner()
        session_key = build_session_key(_make_source())
        _gateway_queues[session_key] = [_ClarifyEntry("Pick one", ["a", "b"])]

        result = await runner._handle_answer_command(_make_event("/answer 2"))

        assert "resuming" in result.lower()
        assert runner.adapters[Platform.TELEGRAM].resume_typing_for_chat.called
        assert _gateway_queues.get(session_key) in (None, [])

    @pytest.mark.asyncio
    async def test_running_agent_answer_bypasses_interrupt_path(self):
        from tools.clarify_tool import _ClarifyEntry, _gateway_queues

        runner = _make_runner()
        source = _make_source()
        session_key = build_session_key(source)
        session_entry = SessionEntry(
            session_key=session_key,
            session_id="sess-1",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            platform=Platform.TELEGRAM,
            chat_type="dm",
        )
        runner.session_store.get_or_create_session.return_value = session_entry

        running_agent = MagicMock()
        runner._running_agents[session_key] = running_agent
        _gateway_queues[session_key] = [_ClarifyEntry("Pick one", ["a", "b"])]

        result = await runner._handle_message(_make_event("/answer 1"))

        assert "clarify response received" in result.lower()
        running_agent.interrupt.assert_not_called()
