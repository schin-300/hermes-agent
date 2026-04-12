from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from hermes_cli.config import get_project_root
from hermes_constants import get_hermes_home
from hermes_state import SessionDB


@dataclass(frozen=True)
class HostedTmuxSession:
    session_id: str
    tmux_target: str
    socket_path: str
    created: bool
    status: str = "running"
    title: Optional[str] = None
    preview: str = ""
    attached_clients: int = 0
    last_active: float = 0.0


class HostedTmuxManager:
    """Manage real hosted Hermes interactive sessions via a dedicated tmux server."""

    SESSION_PREFIX = "hermes-hosted-"

    def __init__(self, *, runner=None) -> None:
        self._runner = runner or subprocess.run
        self._project_root = get_project_root()
        self._hermes_home = get_hermes_home()
        self._tmux_dir = self._hermes_home / "tmux"
        self._tmux_dir.mkdir(parents=True, exist_ok=True)
        self._socket_path = self._tmux_dir / "hosted-hermes.sock"
        self._config_path = self._tmux_dir / "hosted-hermes.conf"
        self._current_session_path = self._tmux_dir / "current-session.txt"
        self._ensure_tmux_config()

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    def is_available(self) -> bool:
        return shutil.which("tmux") is not None

    def _ensure_tmux_config(self) -> None:
        config = "\n".join(
            [
                "set-option -g prefix C-a",
                "unbind-key C-b",
                "bind-key C-a send-prefix",
                "set-option -g status off",
                "set-option -g mouse on",
                "set-option -g history-limit 200000",
                "setw -g aggressive-resize on",
                "set-option -g detach-on-destroy off",
                "",
            ]
        )
        self._config_path.write_text(config, encoding="utf-8")

    def _session_target(self, session_id: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(session_id))
        return f"{self.SESSION_PREFIX}{safe}"

    def _base_cmd(self, *, include_config: bool = False) -> list[str]:
        cmd = ["tmux"]
        if include_config:
            cmd.extend(["-f", str(self._config_path)])
        cmd.extend(["-S", str(self._socket_path)])
        return cmd

    def _run(self, args: list[str], *, include_config: bool = False, check: bool = True) -> subprocess.CompletedProcess:
        if not self.is_available():
            raise RuntimeError("tmux is not installed")
        cmd = self._base_cmd(include_config=include_config) + args
        return self._runner(cmd, check=check, capture_output=True, text=True)

    def _session_exists(self, target: str) -> bool:
        try:
            self._run(["has-session", "-t", target], check=True)
            return True
        except Exception:
            return False

    @staticmethod
    def _generate_session_id() -> str:
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        short_uuid = uuid.uuid4().hex[:6]
        return f"{timestamp_str}_{short_uuid}"

    def _db(self) -> SessionDB:
        return SessionDB()

    def _read_current_session_id(self) -> Optional[str]:
        try:
            value = self._current_session_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except Exception:
            return None
        return value or None

    def _write_current_session_id(self, session_id: str) -> None:
        try:
            self._current_session_path.write_text(str(session_id).strip() + "\n", encoding="utf-8")
        except Exception:
            pass

    def _clear_current_session_id(self, *, expected: Optional[str] = None) -> None:
        current = self._read_current_session_id()
        if expected and current and current != expected:
            return
        try:
            self._current_session_path.unlink(missing_ok=True)
        except Exception:
            pass

    def current_session_id(self) -> Optional[str]:
        session_id = self._read_current_session_id()
        if not session_id:
            return None
        if not self._session_exists(self._session_target(session_id)):
            self._clear_current_session_id(expected=session_id)
            return None
        return session_id

    def mark_current_session(self, session_id: str) -> None:
        if not session_id:
            return
        self._write_current_session_id(session_id)

    def _build_launch_command(
        self,
        *,
        session_id: str,
        resume_existing: bool,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        toolsets: Optional[list[str]] = None,
        skills: Optional[list[str]] = None,
        pass_session_id: bool = False,
        max_turns: Optional[int] = None,
        checkpoints: bool = False,
    ) -> str:
        env_parts = {
            "HERMES_GATEWAY_SESSION_MODE": "0",
            "HERMES_HOSTED_TMUX_CHILD": "1",
            "HERMES_HOSTED_TMUX_SOCKET": str(self._socket_path),
            "HERMES_HOSTED_TMUX_TARGET": self._session_target(session_id),
            "HERMES_HOSTED_TMUX_SESSION_ID": session_id,
        }
        if not resume_existing:
            env_parts["HERMES_FORCED_SESSION_ID"] = session_id

        argv: list[str] = [sys.executable, "-m", "hermes_cli.main", "chat"]
        if resume_existing:
            argv.extend(["--resume", session_id])
        if model:
            argv.extend(["--model", model])
        if provider:
            argv.extend(["--provider", provider])
        if toolsets:
            argv.extend(["--toolsets", ",".join(toolsets)])
        if skills:
            for skill in skills:
                if skill:
                    argv.extend(["--skills", skill])
        if pass_session_id:
            argv.append("--pass-session-id")
        if max_turns is not None:
            argv.extend(["--max-turns", str(max_turns)])
        if checkpoints:
            argv.append("--checkpoints")

        export_str = " ".join(f"export {key}={shlex.quote(value)};" for key, value in env_parts.items())
        exec_str = shlex.join(argv)
        inner = f"cd {shlex.quote(str(self._project_root))} && {export_str} exec {exec_str}"
        return f"bash -lc {shlex.quote(inner)}"

    def ensure_session(
        self,
        *,
        requested_session_id: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        toolsets: Optional[list[str]] = None,
        skills: Optional[list[str]] = None,
        pass_session_id: bool = False,
        max_turns: Optional[int] = None,
        checkpoints: bool = False,
        prefer_current: bool = True,
    ) -> HostedTmuxSession:
        current_session_id = self.current_session_id() if (prefer_current and not requested_session_id) else None
        resume_existing = bool(requested_session_id or current_session_id)
        session_id = requested_session_id or current_session_id or self._generate_session_id()
        target = self._session_target(session_id)

        if self._session_exists(target):
            self._write_current_session_id(session_id)
            return self.describe_session(session_id) or HostedTmuxSession(
                session_id=session_id,
                tmux_target=target,
                socket_path=str(self._socket_path),
                created=False,
            )

        if requested_session_id:
            db = self._db()
            if not db.get_session(session_id):
                raise RuntimeError(f"Hosted tmux session not found: {session_id}")

        launch_cmd = self._build_launch_command(
            session_id=session_id,
            resume_existing=resume_existing,
            model=model,
            provider=provider,
            toolsets=toolsets,
            skills=skills,
            pass_session_id=pass_session_id,
            max_turns=max_turns,
            checkpoints=checkpoints,
        )
        self._run(["new-session", "-d", "-s", target, launch_cmd], include_config=True, check=True)
        self._write_current_session_id(session_id)
        time.sleep(0.2)
        described = self.describe_session(session_id)
        if described is not None:
            return HostedTmuxSession(
                session_id=described.session_id,
                tmux_target=described.tmux_target,
                socket_path=described.socket_path,
                created=True,
                status=described.status,
                title=described.title,
                preview=described.preview,
                attached_clients=described.attached_clients,
                last_active=described.last_active,
            )
        return HostedTmuxSession(
            session_id=session_id,
            tmux_target=target,
            socket_path=str(self._socket_path),
            created=True,
        )

    def _capture_preview(self, target: str) -> str:
        try:
            proc = self._run(["capture-pane", "-p", "-t", target, "-S", "-20"], check=True)
        except Exception:
            return ""
        lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
        if not lines:
            return ""
        return lines[-1][:160]

    def describe_session(self, session_id: str) -> Optional[HostedTmuxSession]:
        target = self._session_target(session_id)
        if not self._session_exists(target):
            return None
        proc = self._run(
            [
                "list-sessions",
                "-F",
                "#{session_name}\t#{session_attached}\t#{session_activity}\t#{session_created}",
            ],
            check=True,
        )
        rows = [line for line in (proc.stdout or "").splitlines() if line.startswith(target + "\t")]
        if not rows:
            return None
        name, attached, activity, created = (rows[0].split("\t") + ["", "", "", ""])[:4]
        last_active = float(activity or created or 0)
        title = None
        try:
            title = self._db().get_session(session_id).get("title")  # type: ignore[union-attr]
        except Exception:
            title = None
        return HostedTmuxSession(
            session_id=session_id,
            tmux_target=name,
            socket_path=str(self._socket_path),
            created=False,
            status="running",
            title=title,
            preview=self._capture_preview(name),
            attached_clients=int(attached or 0),
            last_active=last_active,
        )

    def list_sessions(self, *, limit: int = 50) -> list[HostedTmuxSession]:
        if not self.is_available():
            return []
        try:
            proc = self._run(
                [
                    "list-sessions",
                    "-F",
                    "#{session_name}\t#{session_attached}\t#{session_activity}\t#{session_created}",
                ],
                check=True,
            )
        except Exception:
            return []
        rows: list[HostedTmuxSession] = []
        for line in (proc.stdout or "").splitlines():
            if not line.startswith(self.SESSION_PREFIX):
                continue
            name, attached, activity, created = (line.split("\t") + ["", "", "", ""])[:4]
            session_id = name[len(self.SESSION_PREFIX):]
            title = None
            try:
                meta = self._db().get_session(session_id)
                if meta:
                    title = meta.get("title")
            except Exception:
                title = None
            rows.append(
                HostedTmuxSession(
                    session_id=session_id,
                    tmux_target=name,
                    socket_path=str(self._socket_path),
                    created=False,
                    status="running",
                    title=title,
                    preview=self._capture_preview(name),
                    attached_clients=int(attached or 0),
                    last_active=float(activity or created or 0),
                )
            )
        rows.sort(key=lambda row: (-row.last_active, row.session_id))
        return rows[: max(int(limit or 0), 0)] if limit is not None else rows

    def close_session(self, session_id: str) -> bool:
        target = self._session_target(session_id)
        if not self._session_exists(target):
            return False
        self._run(["kill-session", "-t", target], check=True)
        if self._read_current_session_id() == session_id:
            remaining = self.list_sessions(limit=1)
            if remaining:
                self._write_current_session_id(remaining[0].session_id)
            else:
                self._clear_current_session_id(expected=session_id)
        return True
