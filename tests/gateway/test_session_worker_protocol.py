import pytest

from gateway.session_worker_protocol import (
    decode_event_line,
    encode_event_line,
    is_terminal_event,
    make_event,
    validate_event,
)


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        ("message.delta", {"delta": "hi"}),
        ("tool.started", {"tool": "search_files", "preview": "searching"}),
        ("tool.completed", {"tool": "search_files", "duration": 0.1, "error": False}),
        ("reasoning.available", {"text": "thinking"}),
        ("subagent.heartbeat", {"preview": "child active"}),
        ("subagent.warning", {"preview": "child maybe looping"}),
        ("agent.activity", {"activity": {"wait_state": {"kind": "clarify"}}}),
        ("clarify.request", {"question": "continue?", "choices": ["yes", "no"]}),
        ("run.completed", {"output": "done", "usage": {"input_tokens": 1}}),
        ("run.failed", {"error": "boom"}),
        ("run.cancelled", {"reason": "cancelled"}),
    ],
)
def test_make_event_accepts_known_event_shapes(event_type, payload):
    event = make_event(event_type, run_id="run_123", **payload)
    assert event["event"] == event_type
    assert event["run_id"] == "run_123"
    assert isinstance(event["timestamp"], float)


@pytest.mark.parametrize("event_type", ["run.completed", "run.failed", "run.cancelled"])
def test_terminal_events_are_marked(event_type):
    assert is_terminal_event(event_type) is True


@pytest.mark.parametrize("event_type", ["message.delta", "tool.started", "clarify.request"])
def test_non_terminal_events_are_not_marked(event_type):
    assert is_terminal_event(event_type) is False


def test_validate_event_rejects_unknown_type():
    with pytest.raises(ValueError, match="Unknown worker event type"):
        validate_event({"event": "weird.event", "run_id": "run_123", "timestamp": 1.0})


def test_validate_event_rejects_bad_clarify_choices_shape():
    with pytest.raises(ValueError, match="choices must be a list"):
        validate_event(
            {
                "event": "clarify.request",
                "run_id": "run_123",
                "timestamp": 1.0,
                "question": "continue?",
                "choices": "yes",
            }
        )


def test_encode_decode_round_trip_preserves_event_shape():
    original = make_event(
        "agent.activity",
        run_id="run_123",
        activity={"wait_state": {"kind": "delegate", "mode": "wait"}},
    )
    encoded = encode_event_line(original)
    decoded = decode_event_line(encoded)
    assert decoded == original
