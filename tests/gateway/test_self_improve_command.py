from unittest.mock import AsyncMock, MagicMock

import pytest

import gateway.run as gateway_run
from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _make_event(text="/self-improve", platform=Platform.TELEGRAM, user_id="12345", chat_id="67890"):
    source = SessionSource(
        platform=platform,
        user_id=user_id,
        chat_id=chat_id,
        user_name="testuser",
    )
    return MessageEvent(text=text, source=source)


def _make_runner():
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {}
    runner._ephemeral_system_prompt = ""
    runner._prefill_messages = []
    runner._reasoning_config = None
    runner._show_reasoning = False
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._running_agents = {}
    runner.hooks = MagicMock()
    runner.hooks.emit = AsyncMock()
    runner.hooks.loaded_hooks = []
    runner._session_db = None
    runner._get_or_create_gateway_honcho = lambda session_key: (None, None)
    return runner


class TestSelfImproveCommand:
    @pytest.mark.asyncio
    async def test_self_improve_appears_in_help_output(self):
        runner = _make_runner()
        result = await runner._handle_help_command(_make_event("/help"))
        assert "/self-improve [status|init|branch|check|rollback|submit]" in result

    @pytest.mark.asyncio
    async def test_handle_self_improve_command_delegates_to_shared_helper(self, monkeypatch):
        runner = _make_runner()
        monkeypatch.setattr(
            "hermes_cli.self_improve.handle_self_improve_command",
            lambda text, markdown=True: f"handled {text} markdown={markdown}",
        )

        result = await runner._handle_self_improve_command(_make_event("/self-improve status"))

        assert result == "handled /self-improve status markdown=True"
