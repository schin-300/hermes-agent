from __future__ import annotations

import json
import time
from typing import Any, Mapping

EVENT_TYPES = frozenset({
    "message.delta",
    "tool.started",
    "tool.completed",
    "reasoning.available",
    "subagent.heartbeat",
    "subagent.warning",
    "agent.activity",
    "clarify.request",
    "run.completed",
    "run.failed",
    "run.cancelled",
})

TERMINAL_EVENT_TYPES = frozenset({
    "run.completed",
    "run.failed",
    "run.cancelled",
})

_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "message.delta": frozenset({"delta"}),
    "tool.started": frozenset(),
    "tool.completed": frozenset(),
    "reasoning.available": frozenset({"text"}),
    "subagent.heartbeat": frozenset(),
    "subagent.warning": frozenset(),
    "agent.activity": frozenset({"activity"}),
    "clarify.request": frozenset({"question", "choices"}),
    "run.completed": frozenset({"output", "usage"}),
    "run.failed": frozenset({"error"}),
    "run.cancelled": frozenset({"reason"}),
}


def is_terminal_event(event_type: str) -> bool:
    return str(event_type) in TERMINAL_EVENT_TYPES



def make_event(event_type: str, *, run_id: str, timestamp: float | None = None, **payload: Any) -> dict[str, Any]:
    event_type = str(event_type)
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unknown worker event type: {event_type}")
    if not str(run_id).strip():
        raise ValueError("run_id is required")

    event: dict[str, Any] = {
        "event": event_type,
        "run_id": str(run_id),
        "timestamp": float(timestamp if timestamp is not None else time.time()),
    }
    event.update(payload)

    missing = [key for key in _REQUIRED_FIELDS[event_type] if key not in event]
    if missing:
        raise ValueError(f"Missing required fields for {event_type}: {', '.join(sorted(missing))}")

    if event_type == "clarify.request":
        choices = event.get("choices")
        if not isinstance(choices, list):
            raise ValueError("clarify.request choices must be a list")
    if event_type == "agent.activity":
        activity = event.get("activity")
        if not isinstance(activity, Mapping):
            raise ValueError("agent.activity activity must be a mapping")
    if event_type == "run.completed":
        usage = event.get("usage")
        if not isinstance(usage, Mapping):
            raise ValueError("run.completed usage must be a mapping")

    return event



def validate_event(event: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise ValueError("Worker event must be a mapping")
    payload = dict(event)
    event_type = payload.pop("event", None)
    run_id = payload.pop("run_id", None)
    timestamp = payload.pop("timestamp", None)
    return make_event(str(event_type), run_id=str(run_id or ""), timestamp=timestamp, **payload)



def encode_event_line(event: Mapping[str, Any]) -> str:
    validated = validate_event(event)
    return json.dumps(validated, ensure_ascii=False)



def decode_event_line(line: str) -> dict[str, Any]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid worker event JSON: {exc}") from exc
    return validate_event(payload)
