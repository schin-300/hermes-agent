import asyncio
import json
from pathlib import Path

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter


_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "hosted_sessions"

_CURRENT_BRIDGE_EVENT_NAMES = {
    "tool.started",
    "tool.completed",
    "reasoning.available",
    "message.delta",
    "run.completed",
    "run.failed",
    "run.cancelled",
}

_TARGET_EVENT_NAMES = {
    "session.created",
    "session.attached",
    "session.detached",
    "run.started",
    "message.delta",
    "message.completed",
    "reasoning.delta",
    "reasoning.completed",
    "tool.generating",
    "tool.started",
    "tool.completed",
    "subagent.progress",
    "run.completed",
    "run.failed",
    "run.cancelled",
}


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))


class TestHostedSessionFixtures:
    def test_fixture_directory_contains_expected_stage0_scenarios(self):
        files = sorted(path.name for path in _FIXTURE_DIR.glob("*.json"))
        assert files == ["basic_tool_run.json", "reasoning_and_subagent.json"]

    @pytest.mark.parametrize("fixture_name", ["basic_tool_run.json", "reasoning_and_subagent.json"])
    def test_target_event_names_are_from_canonical_vocabulary(self, fixture_name):
        fixture = _load_fixture(fixture_name)
        assert set(fixture["target_events"]).issubset(_TARGET_EVENT_NAMES)

    @pytest.mark.parametrize("fixture_name", ["basic_tool_run.json", "reasoning_and_subagent.json"])
    def test_current_bridge_event_names_match_known_subset(self, fixture_name):
        fixture = _load_fixture(fixture_name)
        assert set(fixture["current_bridge_events"]) == _CURRENT_BRIDGE_EVENT_NAMES


class TestCurrentApiServerBridgeBaseline:
    @pytest.mark.asyncio
    async def test_make_run_event_callback_emits_current_subset_only(self):
        adapter = APIServerAdapter(PlatformConfig(enabled=True))
        run_id = "run_test"
        queue = asyncio.Queue()
        adapter._run_streams[run_id] = queue
        loop = asyncio.get_running_loop()
        callback = adapter._make_run_event_callback(run_id, loop)

        callback("tool.started", "terminal", "pwd", {"command": "pwd"})
        callback("tool.completed", "terminal", None, None, duration=0.42, is_error=False)
        callback("reasoning.available", "_thinking", "step by step", None)
        callback("_thinking", "internal scratch")
        callback("subagent_progress", "delegate_task", "child 1/2")

        tool_started = await asyncio.wait_for(queue.get(), timeout=1)
        tool_completed = await asyncio.wait_for(queue.get(), timeout=1)
        reasoning = await asyncio.wait_for(queue.get(), timeout=1)
        await asyncio.sleep(0)

        assert tool_started["event"] == "tool.started"
        assert tool_started["tool"] == "terminal"
        assert tool_started["preview"] == "pwd"

        assert tool_completed["event"] == "tool.completed"
        assert tool_completed["tool"] == "terminal"
        assert tool_completed["duration"] == 0.42
        assert tool_completed["error"] is False

        assert reasoning["event"] == "reasoning.available"
        assert reasoning["text"] == "step by step"

        assert queue.empty(), "Current bridge should drop _thinking and subagent_progress in Stage 0"

        adapter._run_streams.pop(run_id, None)
