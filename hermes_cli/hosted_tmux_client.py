from __future__ import annotations

import os
import subprocess
from typing import Any, Optional

import requests

from hermes_cli.hosted_session_client import HostedSessionClientError, ensure_hosted_session_bridge


class HostedTmuxAttachError(RuntimeError):
    """Raised when a tmux-backed hosted Hermes session cannot be created or attached."""


def _headers(api_key: Optional[str]) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def ensure_terminal_session(
    *,
    session_id: Optional[str] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    toolsets: Optional[str] = None,
    skills: Optional[list[str]] = None,
    pass_session_id: bool = False,
    max_turns: Optional[int] = None,
    checkpoints: bool = False,
) -> dict[str, Any]:
    endpoint = ensure_hosted_session_bridge(timeout=15.0, autostart=True)
    payload: dict[str, Any] = {
        "session_id": session_id,
        "model": model,
        "provider": provider,
        "toolsets": [part.strip() for part in str(toolsets or "").split(",") if part.strip()] if toolsets else None,
        "skills": list(skills or []),
        "pass_session_id": pass_session_id,
        "max_turns": max_turns,
        "checkpoints": checkpoints,
    }
    payload = {key: value for key, value in payload.items() if value not in (None, [], "")}
    try:
        response = requests.post(
            f"{endpoint.base_url}/v1/terminal-sessions/ensure",
            json=payload,
            headers=_headers(endpoint.api_key),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        raise HostedTmuxAttachError(str(exc)) from exc
    if not isinstance(data, dict) or not data.get("session_id") or not data.get("tmux_target") or not data.get("socket_path"):
        raise HostedTmuxAttachError("Gateway did not return tmux session attachment info")
    return data


def attach_terminal_session(*, socket_path: str, tmux_target: str) -> int:
    env = os.environ.copy()
    env.pop("TMUX", None)
    proc = subprocess.run(
        ["tmux", "-S", socket_path, "attach-session", "-t", tmux_target],
        env=env,
        check=False,
    )
    return int(proc.returncode or 0)


def run_hosted_tmux_chat(args) -> None:
    session_info = ensure_terminal_session(
        session_id=getattr(args, "resume", None),
        model=getattr(args, "model", None),
        provider=getattr(args, "provider", None),
        toolsets=getattr(args, "toolsets", None),
        skills=getattr(args, "skills", None),
        pass_session_id=bool(getattr(args, "pass_session_id", False)),
        max_turns=getattr(args, "max_turns", None),
        checkpoints=bool(getattr(args, "checkpoints", False)),
    )
    code = attach_terminal_session(socket_path=str(session_info["socket_path"]), tmux_target=str(session_info["tmux_target"]))
    if code != 0:
        raise HostedTmuxAttachError(f"tmux attach failed with exit code {code}")
