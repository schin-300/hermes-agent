import json

from hermes_cli.hosted_session_client import (
    HostedSessionAgentProxy,
    HostedSessionEndpoint,
)


class _FakeResponse:
    def __init__(self, json_payload=None, lines=None):
        self._json_payload = json_payload or {}
        self._lines = list(lines or [])

    def raise_for_status(self):
        return None

    def json(self):
        return self._json_payload

    def iter_lines(self, decode_unicode=True):
        del decode_unicode
        for line in self._lines:
            yield line

    def close(self):
        return None


class _FakeSession:
    def __init__(self, run_response, event_response):
        self.run_response = run_response
        self.event_response = event_response
        self.posts = []
        self.gets = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return self.run_response

    def get(self, url, headers=None, stream=False, timeout=None):
        self.gets.append({"url": url, "headers": headers, "stream": stream, "timeout": timeout})
        return self.event_response


def test_hosted_session_proxy_maps_canonical_events_to_cli_callbacks():
    streamed = []
    reasoning = []
    tool_events = []
    tool_gen = []

    events = [
        'data: ' + json.dumps({"event": "session.created", "session_id": "sess_1", "run_id": "run_1", "timestamp": 1, "payload": {}}),
        'data: ' + json.dumps({"event": "run.started", "session_id": "sess_1", "run_id": "run_1", "timestamp": 2, "payload": {"user_message": "hi"}}),
        'data: ' + json.dumps({"event": "tool.generating", "session_id": "sess_1", "run_id": "run_1", "timestamp": 3, "payload": {"tool": "write_file"}}),
        'data: ' + json.dumps({"event": "tool.started", "session_id": "sess_1", "run_id": "run_1", "timestamp": 4, "payload": {"tool": "write_file", "preview": "notes.txt", "args": {"path": "notes.txt"}}}),
        'data: ' + json.dumps({"event": "reasoning.delta", "session_id": "sess_1", "run_id": "run_1", "timestamp": 5, "payload": {"text": "thinking..."}}),
        'data: ' + json.dumps({"event": "message.delta", "session_id": "sess_1", "run_id": "run_1", "timestamp": 6, "payload": {"delta": "hello"}}),
        'data: ' + json.dumps({"event": "subagent.progress", "session_id": "sess_1", "run_id": "run_1", "timestamp": 7, "payload": {"tool": "delegate_task", "text": "child 1/2"}}),
        'data: ' + json.dumps({"event": "tool.completed", "session_id": "sess_1", "run_id": "run_1", "timestamp": 8, "payload": {"tool": "write_file", "duration": 0.2, "error": False}}),
        'data: ' + json.dumps({"event": "message.completed", "session_id": "sess_1", "run_id": "run_1", "timestamp": 9, "payload": {"content": "hello world"}}),
        'data: ' + json.dumps({"event": "run.completed", "session_id": "sess_1", "run_id": "run_1", "timestamp": 10, "payload": {"output": "hello world", "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}}}),
        ': stream closed',
    ]

    fake_session = _FakeSession(
        run_response=_FakeResponse({"run_id": "run_1", "session_id": "sess_1", "status": "started"}),
        event_response=_FakeResponse(lines=events),
    )

    proxy = HostedSessionAgentProxy(
        endpoint=HostedSessionEndpoint(base_url="http://127.0.0.1:8642", api_key=None),
        session_id="sess_1",
        model="gpt-test",
        provider="openai",
        tool_progress_callback=lambda *args, **kwargs: tool_events.append((args, kwargs)),
        reasoning_callback=lambda text: reasoning.append(text),
        tool_gen_callback=lambda tool: tool_gen.append(tool),
        http_session=fake_session,
    )

    result = proxy.run_conversation(
        user_message="hi",
        conversation_history=[{"role": "assistant", "content": "previous"}],
        stream_callback=lambda delta: streamed.append(delta),
    )

    assert streamed == ["hello"]
    assert reasoning == ["thinking..."]
    assert tool_gen == ["write_file"]
    assert result["final_response"] == "hello world"
    assert result["last_reasoning"] == "thinking..."
    assert result["response_previewed"] is True
    assert proxy.session_total_tokens == 3
    assert any(args[0] == "tool.started" for args, _ in tool_events)
    assert any(args[0] == "tool.completed" for args, _ in tool_events)
    assert any(args[0] == "subagent.progress" for args, _ in tool_events)


def test_hosted_session_proxy_interrupt_posts_cancel_and_close_session_posts_close():
    fake_session = _FakeSession(
        run_response=_FakeResponse({"run_id": "run_1", "session_id": "sess_1", "status": "started"}),
        event_response=_FakeResponse(lines=[]),
    )
    proxy = HostedSessionAgentProxy(
        endpoint=HostedSessionEndpoint(base_url="http://127.0.0.1:8642", api_key=None),
        session_id="sess_1",
        http_session=fake_session,
    )
    proxy._active_run_id = "run_1"
    proxy.interrupt("stop")
    assert fake_session.posts[-1]["url"].endswith("/v1/runs/run_1/cancel")

    proxy.close_session()
    assert fake_session.posts[-1]["url"].endswith("/v1/sessions/sess_1/close")
