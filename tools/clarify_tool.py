#!/usr/bin/env python3
"""
Clarify Tool Module - Interactive Clarifying Questions

Allows the agent to present structured multiple-choice questions or open-ended
prompts to the user. In CLI mode, choices are navigable with arrow keys. On
messaging platforms, questions can block on a per-session queue until the user
responds.

The actual user-interaction logic lives in the platform layer (cli.py for CLI,
gateway/run.py for messaging). This module defines the schema, validation, and
a thin dispatcher that delegates to a platform-provided callback.
"""

from __future__ import annotations

import json
import threading
from typing import Callable, List, Optional


# Maximum number of predefined choices the agent can offer.
# A 5th "Other (type your answer)" option is always appended by the UI.
MAX_CHOICES = 4


# =============================================================================
# Blocking gateway clarify infrastructure
# =============================================================================

_gateway_lock = threading.Lock()
_gateway_notify_cbs: dict[str, object] = {}
_gateway_queues: dict[str, list["_ClarifyEntry"]] = {}


class _ClarifyEntry:
    """One pending clarify question inside a gateway session."""

    __slots__ = ("event", "question", "choices", "result")

    def __init__(self, question: str, choices: Optional[List[str]]):
        self.event = threading.Event()
        self.question = question
        self.choices = list(choices or [])
        self.result: Optional[str] = None


def register_gateway_clarify_notify(session_key: str, cb) -> None:
    """Register a per-session callback for sending clarify prompts to the user."""
    with _gateway_lock:
        _gateway_notify_cbs[session_key] = cb


def unregister_gateway_clarify_notify(session_key: str) -> None:
    """Unregister a gateway clarify callback and unblock all waiters."""
    with _gateway_lock:
        _gateway_notify_cbs.pop(session_key, None)
        entries = _gateway_queues.pop(session_key, [])
        for entry in entries:
            entry.event.set()


def clear_gateway_clarify_session(session_key: str) -> None:
    """Clear all pending clarify waits for a session and unblock waiters."""
    unregister_gateway_clarify_notify(session_key)


def has_blocking_gateway_clarify(session_key: str) -> bool:
    """Return True when a session has at least one pending clarify wait."""
    with _gateway_lock:
        return bool(_gateway_queues.get(session_key))


def pending_gateway_clarify_count(session_key: str) -> int:
    """Return the number of pending clarify waits for a session."""
    with _gateway_lock:
        return len(_gateway_queues.get(session_key, []))


def peek_blocking_gateway_clarify(session_key: str) -> Optional[dict]:
    """Return a preview of the oldest pending clarify question for a session."""
    with _gateway_lock:
        queue = _gateway_queues.get(session_key) or []
        if not queue:
            return None
        entry = queue[0]
        return {
            "question": entry.question,
            "choices": list(entry.choices),
            "choices_count": len(entry.choices),
        }


def _coerce_gateway_response(entry: _ClarifyEntry, response_text: str) -> str:
    text = str(response_text or "").strip()
    if entry.choices:
        if text.isdigit():
            idx = int(text) - 1
            if 0 <= idx < len(entry.choices):
                return entry.choices[idx]
        lowered = text.casefold()
        for choice in entry.choices:
            if lowered == choice.casefold():
                return choice
    return text


def resolve_gateway_clarify(session_key: str, response_text: str, *, resolve_all: bool = False) -> int:
    """Resolve one or more pending clarify waits for a session.

    Returns the number of waits resolved.
    """
    with _gateway_lock:
        queue = _gateway_queues.get(session_key)
        if not queue:
            return 0
        if resolve_all:
            targets = list(queue)
            queue.clear()
        else:
            targets = [queue.pop(0)]
        if not queue:
            _gateway_queues.pop(session_key, None)

    for entry in targets:
        entry.result = _coerce_gateway_response(entry, response_text)
        entry.event.set()
    return len(targets)


def wait_for_gateway_clarify(question: str, choices: Optional[List[str]], *, session_key: str, timeout_seconds: Optional[float] = None) -> str:
    """Block a gateway agent thread until the user answers a clarify question."""
    entry = _ClarifyEntry(question=question, choices=choices)
    notify_cb = None
    with _gateway_lock:
        _gateway_queues.setdefault(session_key, []).append(entry)
        notify_cb = _gateway_notify_cbs.get(session_key)

    if notify_cb is not None:
        notify_cb({
            "question": question,
            "choices": list(choices or []),
            "session_key": session_key,
        })

    resolved = entry.event.wait(timeout_seconds) if timeout_seconds and timeout_seconds > 0 else entry.event.wait()
    if not resolved:
        return (
            "The user did not provide a response within the time limit. "
            "Use your best judgement to make the choice and proceed."
        )
    if entry.result is not None:
        return entry.result
    return "The clarify request was cancelled before the user responded."


# =============================================================================
# Main clarify tool
# =============================================================================


def clarify_tool(
    question: str,
    choices: Optional[List[str]] = None,
    callback: Optional[Callable] = None,
) -> str:
    """
    Ask the user a question, optionally with multiple-choice options.

    Args:
        question: The question text to present.
        choices:  Up to 4 predefined answer choices. When omitted the
                  question is purely open-ended.
        callback: Platform-provided function that handles the actual UI
                  interaction. Signature: callback(question, choices) -> str.
                  Injected by the agent runner (cli.py / gateway).

    Returns:
        JSON string with the user's response.
    """
    if not question or not question.strip():
        return tool_error("Question text is required.")

    question = question.strip()

    # Validate and trim choices
    if choices is not None:
        if not isinstance(choices, list):
            return tool_error("choices must be a list of strings.")
        choices = [str(c).strip() for c in choices if str(c).strip()]
        if len(choices) > MAX_CHOICES:
            choices = choices[:MAX_CHOICES]
        if not choices:
            choices = None  # empty list → open-ended

    if callback is None:
        return json.dumps(
            {"error": "Clarify tool is not available in this execution context."},
            ensure_ascii=False,
        )

    try:
        user_response = callback(question, choices)
    except Exception as exc:
        return json.dumps(
            {"error": f"Failed to get user input: {exc}"},
            ensure_ascii=False,
        )

    return json.dumps({
        "question": question,
        "choices_offered": choices,
        "user_response": str(user_response).strip(),
    }, ensure_ascii=False)


def check_clarify_requirements() -> bool:
    """Clarify tool has no external requirements -- always available."""
    return True


# =============================================================================
# OpenAI Function-Calling Schema
# =============================================================================

CLARIFY_SCHEMA = {
    "name": "clarify",
    "description": (
        "Ask the user a question when you need clarification, feedback, or a "
        "decision before proceeding. Supports two modes:\n\n"
        "1. **Multiple choice** — provide up to 4 choices. The user picks one "
        "or types their own answer via a 5th 'Other' option.\n"
        "2. **Open-ended** — omit choices entirely. The user types a free-form "
        "response.\n\n"
        "Use this tool when:\n"
        "- The task is ambiguous and you need the user to choose an approach\n"
        "- You want post-task feedback ('How did that work out?')\n"
        "- You want to offer to save a skill or update memory\n"
        "- A decision has meaningful trade-offs the user should weigh in on\n\n"
        "Do NOT use this tool for simple yes/no confirmation of dangerous "
        "commands (the terminal tool handles that). Prefer making a reasonable "
        "default choice yourself when the decision is low-stakes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to present to the user.",
            },
            "choices": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": MAX_CHOICES,
                "description": (
                    "Up to 4 answer choices. Omit this parameter entirely to "
                    "ask an open-ended question. When provided, the UI "
                    "automatically appends an 'Other (type your answer)' option."
                ),
            },
        },
        "required": ["question"],
    },
}


# --- Registry ---
from tools.registry import registry, tool_error

registry.register(
    name="clarify",
    toolset="clarify",
    schema=CLARIFY_SCHEMA,
    handler=lambda args, **kw: clarify_tool(
        question=args.get("question", ""),
        choices=args.get("choices"),
        callback=kw.get("callback")),
    check_fn=check_clarify_requirements,
    emoji="❓",
)
