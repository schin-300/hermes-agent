from agent.session_events import (
    CANONICAL_SESSION_EVENT_TYPES,
    SessionEvent,
    build_session_event,
    is_valid_session_event_type,
)


def test_canonical_event_vocabulary_contains_hosted_runtime_core_events():
    assert CANONICAL_SESSION_EVENT_TYPES == (
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
    )


def test_session_event_to_dict_preserves_required_fields():
    event = build_session_event(
        "tool.started",
        session_id="sess_123",
        run_id="run_456",
        tool="terminal",
        preview="pwd",
    )

    payload = event.to_dict()

    assert payload["event"] == "tool.started"
    assert payload["session_id"] == "sess_123"
    assert payload["run_id"] == "run_456"
    assert payload["payload"]["tool"] == "terminal"
    assert payload["payload"]["preview"] == "pwd"


def test_invalid_event_type_raises_value_error():
    try:
        SessionEvent(event="reasoning.available", session_id="sess_123")
    except ValueError as exc:
        assert "Unknown session event type" in str(exc)
    else:
        raise AssertionError("Expected invalid event type to raise ValueError")


def test_is_valid_session_event_type_matches_canonical_vocabulary():
    assert is_valid_session_event_type("message.delta") is True
    assert is_valid_session_event_type("reasoning.available") is False
