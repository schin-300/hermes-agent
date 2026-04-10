from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import Platform, PlatformConfig
from gateway.platforms.api_server import (
    APIServerAdapter,
    API_SERVER_APP_KEY,
    cors_middleware,
    security_headers_middleware,
)
from gateway.session import SessionEntry, SessionSource


class _FakeSessionStore:
    def __init__(self, entries):
        self._entries = list(entries)

    def list_sessions(self, active_minutes=None):
        return list(self._entries)


class _FakeSessionDB:
    def __init__(self, rows):
        self._rows = list(rows)

    def list_sessions_rich(self, **kwargs):
        return list(self._rows)


def _make_adapter() -> APIServerAdapter:
    return APIServerAdapter(PlatformConfig(enabled=True))


def _create_app(adapter: APIServerAdapter) -> web.Application:
    app = web.Application(
        middlewares=[
            mw for mw in (cors_middleware, security_headers_middleware) if mw is not None
        ]
    )
    app[API_SERVER_APP_KEY] = adapter
    app.router.add_get("/viewer", adapter._handle_viewer)
    app.router.add_get("/api/viewer/agents", adapter._handle_viewer_agents)
    app.router.add_get("/api/viewer/events", adapter._handle_viewer_events)
    return app


def _gateway_entry(*, session_key: str, session_id: str, label: str) -> SessionEntry:
    now = datetime.now(timezone.utc)
    return SessionEntry(
        session_key=session_key,
        session_id=session_id,
        created_at=now - timedelta(minutes=10),
        updated_at=now - timedelta(seconds=15),
        origin=SessionSource.local_cli(),
        display_name=label,
        platform=Platform.LOCAL,
        chat_type="dm",
        total_tokens=321,
    )


@pytest.mark.asyncio
async def test_viewer_page_serves_html_shell():
    adapter = _make_adapter()
    app = _create_app(adapter)

    async with TestClient(TestServer(app)) as cli:
        resp = await cli.get("/viewer")
        text = await resp.text()

    assert resp.status == 200
    assert "Hermes Visibility Layer" in text
    assert "/api/viewer/agents" in text
    assert "/api/viewer/events" in text


@pytest.mark.asyncio
async def test_viewer_agents_endpoint_combines_gateway_and_api_server_sessions():
    adapter = _make_adapter()
    adapter._session_store = _FakeSessionStore(
        [
            _gateway_entry(
                session_key="local:dm:builder",
                session_id="gateway-session-1",
                label="Builder",
            )
        ]
    )
    adapter._session_db = _FakeSessionDB(
        [
            {
                "id": "api-session-1",
                "source": "api_server",
                "model": "openai/gpt-test",
                "title": "Browser Worker",
                "started_at": 1000.0,
                "ended_at": None,
                "message_count": 6,
                "preview": "working on the website",
                "last_active": 1012.5,
                "tool_call_count": 2,
                "input_tokens": 11,
                "output_tokens": 17,
            }
        ]
    )
    adapter.set_viewer_runtime_provider(
        lambda: {
            "gateway_sessions": {
                "local:dm:builder": {
                    "status": "running",
                    "activity": "using terminal",
                    "current_tool": "terminal",
                    "seconds_since_activity": 2.5,
                    "model": "anthropic/test-gateway",
                }
            },
            "api_sessions": {
                "api-session-1": {
                    "status": "running",
                    "activity": "reading files",
                    "current_tool": "read_file",
                    "seconds_since_activity": 1.0,
                }
            },
        }
    )
    app = _create_app(adapter)

    async with TestClient(TestServer(app)) as cli:
        resp = await cli.get("/api/viewer/agents")
        data = await resp.json()

    assert resp.status == 200
    assert data["counts"]["total"] == 2
    agents = {agent["id"]: agent for agent in data["agents"]}

    assert agents["gateway:local:dm:builder"]["status"] == "running"
    assert agents["gateway:local:dm:builder"]["current_tool"] == "terminal"
    assert agents["gateway:local:dm:builder"]["display_name"] == "Builder"

    assert agents["api:api-session-1"]["status"] == "running"
    assert agents["api:api-session-1"]["title"] == "Browser Worker"
    assert agents["api:api-session-1"]["current_tool"] == "read_file"


@pytest.mark.asyncio
async def test_viewer_events_endpoint_streams_initial_snapshot():
    adapter = _make_adapter()
    adapter._session_store = _FakeSessionStore(
        [
            _gateway_entry(
                session_key="local:dm:viewer",
                session_id="gateway-session-viewer",
                label="Viewer",
            )
        ]
    )
    adapter._session_db = _FakeSessionDB([])
    adapter.set_viewer_runtime_provider(lambda: {"gateway_sessions": {}, "api_sessions": {}})
    app = _create_app(adapter)

    async with TestClient(TestServer(app)) as cli:
        resp = await cli.get("/api/viewer/events")
        payload = await resp.content.readline()

    assert resp.status == 200
    assert resp.headers["Content-Type"].startswith("text/event-stream")
    assert payload.decode().startswith("data: {")
