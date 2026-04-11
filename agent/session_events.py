from __future__ import annotations

"""Canonical hosted-session event model.

This module defines the event vocabulary Hermes should use internally for
hosted session/runtime ownership. Transports such as the API server, ACP,
and local CLI should adapt *from* this vocabulary rather than inventing
separate partial event sets.
"""

from dataclasses import dataclass, field
from time import time
from typing import Any, Mapping


CANONICAL_SESSION_EVENT_TYPES: tuple[str, ...] = (
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

_CANONICAL_EVENT_TYPE_SET = frozenset(CANONICAL_SESSION_EVENT_TYPES)


@dataclass(frozen=True)
class SessionEvent:
    """Immutable canonical event emitted by the hosted session runtime."""

    event: str
    session_id: str
    run_id: str | None = None
    timestamp: float = field(default_factory=time)
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.event not in _CANONICAL_EVENT_TYPE_SET:
            raise ValueError(f"Unknown session event type: {self.event}")
        if not str(self.session_id or "").strip():
            raise ValueError("session_id is required")
        if self.run_id is not None and not str(self.run_id).strip():
            raise ValueError("run_id must be non-empty when provided")
        if not isinstance(self.payload, dict):
            raise TypeError("payload must be a dict")

    def to_dict(self) -> dict[str, Any]:
        data = {
            "event": self.event,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "payload": dict(self.payload),
        }
        if self.run_id is not None:
            data["run_id"] = self.run_id
        return data


def is_valid_session_event_type(event_type: str) -> bool:
    return event_type in _CANONICAL_EVENT_TYPE_SET


def build_session_event(
    event: str,
    *,
    session_id: str,
    run_id: str | None = None,
    timestamp: float | None = None,
    payload: Mapping[str, Any] | None = None,
    **extra_payload: Any,
) -> SessionEvent:
    merged_payload: dict[str, Any] = dict(payload or {})
    merged_payload.update(extra_payload)
    kwargs: dict[str, Any] = {
        "event": event,
        "session_id": session_id,
        "run_id": run_id,
        "payload": merged_payload,
    }
    if timestamp is not None:
        kwargs["timestamp"] = float(timestamp)
    return SessionEvent(**kwargs)
