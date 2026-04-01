from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_METADATA_SCHEMA_VERSION = 1
_METADATA_FILENAME = "hermes-fork.json"
_DEFAULT_WORKTREE_DIRNAME = "hermes-agent"
_BRANCH_SLUG_RE = re.compile(r"[^a-z0-9._/-]+")
_GITHUB_REMOTE_PATTERNS = (
    re.compile(r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"),
    re.compile(r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"),
)


@dataclass
class ProfileForkInfo:
    schema_version: int
    profile_name: str
    profile_dir: str
    source_repo: str
    worktree_path: str
    base_ref: str
    base_branch: str
    profile_branch: str
    default_pr_remote: str
    default_pr_base: str
    last_known_good_ref: Optional[str] = None
    last_healthcheck: Optional[str] = None
    last_healthcheck_ok: Optional[bool] = None

    @property
    def profile_dir_path(self) -> Path:
        return Path(self.profile_dir)

    @property
    def source_repo_path(self) -> Path:
        return Path(self.source_repo)

    @property
    def worktree_dir(self) -> Path:
        return Path(self.worktree_path)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resolve_profile_target(profile_name: str | None = None) -> tuple[str, Path]:
    from hermes_constants import get_hermes_home
    from hermes_cli.profiles import get_active_profile_name, get_profile_dir

    resolved_name = profile_name or get_active_profile_name()
    if resolved_name == "custom":
        return resolved_name, get_hermes_home()
    return resolved_name, get_profile_dir(resolved_name)


def _metadata_path_for_profile(profile_dir: Path) -> Path:
    return profile_dir / "workspace" / _METADATA_FILENAME


def _default_worktree_path(profile_dir: Path) -> Path:
    return profile_dir / "workspace" / _DEFAULT_WORKTREE_DIRNAME


def _short_ref(ref: str | None) -> str:
    if not ref:
        return "—"
    return ref[:12]


def _tail(text: str, max_lines: int = 20) -> str:
    lines = (text or "").strip().splitlines()
    if not lines:
        return ""
    return "\n".join(lines[-max_lines:])


def _run(args: list[str], cwd: Path, *, env: dict[str, str] | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _git(cwd: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return _run(["git", *args], cwd, timeout=timeout)


def _git_ok(cwd: Path, *args: str, timeout: int = 60) -> str:
    result = _git(cwd, *args, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _branch_exists(repo_root: Path, branch_name: str) -> bool:
    result = _git(repo_root, "show-ref", "--verify", f"refs/heads/{branch_name}")
    return result.returncode == 0


def _current_branch(repo_root: Path) -> str:
    branch = _git_ok(repo_root, "branch", "--show-current")
    if branch:
        return branch
    fallback = _git_ok(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    return fallback or "HEAD"


def _current_head(repo_root: Path) -> str:
    return _git_ok(repo_root, "rev-parse", "HEAD")


def _current_head_short(repo_root: Path) -> str:
    return _git_ok(repo_root, "rev-parse", "--short", "HEAD")


def _is_dirty(repo_root: Path) -> bool:
    result = _git(repo_root, "status", "--porcelain")
    return bool((result.stdout or "").strip())


def _slugify_branch_label(label: str) -> str:
    slug = label.strip().lower().replace(" ", "-")
    slug = _BRANCH_SLUG_RE.sub("-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-./")
    if not slug:
        raise ValueError("Branch label must contain at least one alphanumeric character.")
    return slug


def _parse_github_remote(url: str) -> tuple[str, str] | None:
    cleaned = (url or "").strip()
    for pattern in _GITHUB_REMOTE_PATTERNS:
        match = pattern.match(cleaned)
        if match:
            return match.group("owner"), match.group("repo")
    return None


def _choose_pr_remote(repo_root: Path) -> str:
    remotes_result = _git(repo_root, "remote")
    remotes = [line.strip() for line in remotes_result.stdout.splitlines() if line.strip()]
    for preferred in ("fork", "origin"):
        if preferred in remotes:
            return preferred
    if remotes:
        return remotes[0]
    return "origin"


def _candidate_branch_name(profile_name: str, label: str) -> str:
    return f"profiles/{profile_name}/{_slugify_branch_label(label)}"


def detect_current_repo_root(repo_path: str | None = None) -> Path:
    if repo_path:
        candidate = Path(repo_path).expanduser().resolve()
    else:
        candidate = Path(__file__).resolve().parent.parent
    result = _git(candidate, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise FileNotFoundError(f"Not inside a git repository: {candidate}")
    return Path(result.stdout.strip()).resolve()


def load_profile_fork_info(profile_name: str | None = None) -> ProfileForkInfo | None:
    _, profile_dir = _resolve_profile_target(profile_name)
    metadata_path = _metadata_path_for_profile(profile_dir)
    if not metadata_path.exists():
        return None
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    return ProfileForkInfo(**data)


def save_profile_fork_info(info: ProfileForkInfo) -> ProfileForkInfo:
    metadata_path = _metadata_path_for_profile(info.profile_dir_path)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(asdict(info), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return info


def resolve_profile_attached_runtime_root(profile_name: str | None = None) -> Path | None:
    """Return the attached worktree root if it is a valid runtime source tree."""
    info = load_profile_fork_info(profile_name)
    if not info:
        return None

    root = info.worktree_dir.resolve()
    if not root.exists():
        return None
    if not (root / "hermes_cli" / "main.py").exists():
        return None
    return root


def _update_health_metadata(info: ProfileForkInfo, *, ok: bool, ref: str | None = None) -> ProfileForkInfo:
    info.last_healthcheck = _utcnow_iso()
    info.last_healthcheck_ok = ok
    if ok and ref:
        info.last_known_good_ref = ref
    return save_profile_fork_info(info)


def get_profile_fork_status(profile_name: str | None = None) -> dict[str, Any]:
    resolved_name, profile_dir = _resolve_profile_target(profile_name)
    info = load_profile_fork_info(resolved_name)
    if not info:
        return {
            "attached": False,
            "profile_name": resolved_name,
            "profile_dir": str(profile_dir),
        }

    worktree_path = info.worktree_dir
    status = {
        "attached": True,
        "profile_name": resolved_name,
        "profile_dir": str(profile_dir),
        "info": info,
        "worktree_exists": worktree_path.exists(),
    }
    if worktree_path.exists():
        status.update(
            {
                "current_branch": _current_branch(worktree_path),
                "head": _current_head(worktree_path),
                "head_short": _current_head_short(worktree_path),
                "dirty": _is_dirty(worktree_path),
            }
        )
    else:
        status.update(
            {
                "current_branch": None,
                "head": None,
                "head_short": None,
                "dirty": False,
            }
        )
    return status


def _healthcheck_commands_for_repo(info: ProfileForkInfo) -> list[tuple[str, list[str]]]:
    worktree = info.worktree_dir
    commands: list[tuple[str, list[str]]] = []

    compile_targets = [
        "cli.py",
        "gateway/run.py",
        "hermes_cli/commands.py",
        "hermes_cli/main.py",
        "hermes_cli/profiles.py",
        "hermes_cli/self_improve.py",
    ]
    existing_targets = [target for target in compile_targets if (worktree / target).exists()]
    if existing_targets:
        syntax_script = (
            "from pathlib import Path; import sys; "
            "[compile(Path(path).read_text(encoding='utf-8'), path, 'exec') for path in sys.argv[1:]]"
        )
        commands.append(("py_compile", [sys.executable, "-c", syntax_script, *existing_targets]))

    main_entry = worktree / "hermes_cli" / "main.py"
    if main_entry.exists():
        commands.append(("profile_launcher", [sys.executable, str(main_entry), "-p", info.profile_name, "profile"]))
        commands.append(("profile_launcher_env", [sys.executable, str(main_entry), "profile"]))

    if not commands:
        python_files = sorted(str(path.relative_to(worktree)) for path in worktree.rglob("*.py"))
        if python_files:
            syntax_script = (
                "from pathlib import Path; import sys; "
                "[compile(Path(path).read_text(encoding='utf-8'), path, 'exec') for path in sys.argv[1:]]"
            )
            commands.append(("py_compile", [sys.executable, "-c", syntax_script, *python_files]))

    return commands


def _run_healthcheck_once(info: ProfileForkInfo) -> dict[str, Any]:
    env = {
        **os.environ,
        "HERMES_HOME": str(info.profile_dir_path),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    worktree = info.worktree_dir
    steps: list[dict[str, Any]] = []
    ok = True
    for name, command in _healthcheck_commands_for_repo(info):
        result = _run(command, worktree, env=env, timeout=120)
        step = {
            "name": name,
            "command": shlex.join(command),
            "exit_code": result.returncode,
            "stdout_tail": _tail(result.stdout),
            "stderr_tail": _tail(result.stderr),
        }
        steps.append(step)
        if result.returncode != 0:
            ok = False
            break
    return {
        "ok": ok,
        "steps": steps,
        "current_ref": _current_head(worktree),
        "current_ref_short": _current_head_short(worktree),
        "current_branch": _current_branch(worktree),
    }


def initialize_profile_fork(profile_name: str | None = None, *, repo_path: str | None = None, base_ref: str = "HEAD") -> dict[str, Any]:
    resolved_name, profile_dir = _resolve_profile_target(profile_name)
    if not profile_dir.exists():
        raise FileNotFoundError(f"Profile home does not exist: {profile_dir}")

    repo_root = detect_current_repo_root(repo_path)
    metadata_path = _metadata_path_for_profile(profile_dir)
    worktree_path = _default_worktree_path(profile_dir)
    if metadata_path.exists() or worktree_path.exists():
        raise FileExistsError(
            f"Profile '{resolved_name}' already has a fork/worktree at {worktree_path}."
        )

    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    base_branch = _current_branch(repo_root)
    profile_branch = f"profiles/{resolved_name}/main"
    if _branch_exists(repo_root, profile_branch):
        result = _git(repo_root, "worktree", "add", str(worktree_path), profile_branch)
    else:
        result = _git(repo_root, "worktree", "add", str(worktree_path), "-b", profile_branch, base_ref)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Failed to create worktree")

    info = ProfileForkInfo(
        schema_version=_METADATA_SCHEMA_VERSION,
        profile_name=resolved_name,
        profile_dir=str(profile_dir),
        source_repo=str(repo_root),
        worktree_path=str(worktree_path),
        base_ref=base_ref,
        base_branch=base_branch,
        profile_branch=profile_branch,
        default_pr_remote=_choose_pr_remote(worktree_path),
        default_pr_base=base_branch or "main",
    )
    save_profile_fork_info(info)
    health = run_profile_fork_healthcheck(resolved_name, rollback_on_failure=False)
    return {
        "profile_name": resolved_name,
        "profile_dir": str(profile_dir),
        "source_repo": str(repo_root),
        "worktree_path": str(worktree_path),
        "profile_branch": profile_branch,
        "base_branch": base_branch,
        "healthcheck": health,
    }


def cleanup_profile_fork(profile_name: str | None = None, *, delete_branch: bool = True) -> dict[str, Any]:
    status = get_profile_fork_status(profile_name)
    if not status["attached"]:
        return {"attached": False}

    info: ProfileForkInfo = status["info"]
    metadata_path = _metadata_path_for_profile(info.profile_dir_path)
    source_repo = info.source_repo_path

    removed_worktree = False
    removed_branch = False

    if source_repo.exists() and info.worktree_dir.exists():
        result = _git(source_repo, "worktree", "remove", str(info.worktree_dir), "--force")
        removed_worktree = result.returncode == 0
    elif not info.worktree_dir.exists():
        removed_worktree = True

    if delete_branch and source_repo.exists() and _branch_exists(source_repo, info.profile_branch):
        branch_result = _git(source_repo, "branch", "-D", info.profile_branch)
        removed_branch = branch_result.returncode == 0

    metadata_path.unlink(missing_ok=True)
    return {
        "attached": True,
        "removed_worktree": removed_worktree,
        "removed_branch": removed_branch,
        "metadata_removed": not metadata_path.exists(),
    }


def create_profile_fork_branch(profile_name: str | None, label: str) -> dict[str, Any]:
    status = get_profile_fork_status(profile_name)
    if not status["attached"]:
        raise FileNotFoundError("No Hermes fork attached to this profile. Run /self-improve init first.")
    info: ProfileForkInfo = status["info"]
    branch_name = _candidate_branch_name(status["profile_name"], label)
    result = _git(info.worktree_dir, "checkout", "-B", branch_name)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Failed to create branch")
    return {
        "profile_name": status["profile_name"],
        "branch": branch_name,
        "head": _current_head(info.worktree_dir),
        "head_short": _current_head_short(info.worktree_dir),
        "worktree_path": str(info.worktree_dir),
    }


def run_profile_fork_healthcheck(
    profile_name: str | None = None,
    *,
    rollback_on_failure: bool = False,
    rollback_depth: int = 6,
) -> dict[str, Any]:
    status = get_profile_fork_status(profile_name)
    if not status["attached"]:
        raise FileNotFoundError("No Hermes fork attached to this profile. Run /self-improve init first.")

    info: ProfileForkInfo = status["info"]
    current_ref = _current_head(info.worktree_dir)
    initial = _run_healthcheck_once(info)
    if initial["ok"]:
        _update_health_metadata(info, ok=True, ref=initial["current_ref"])
        initial.update({"rolled_back": False, "healthy_ref": initial["current_ref"], "tested_refs": [initial["current_ref"]]})
        return initial

    tested_refs = [current_ref]
    if not rollback_on_failure:
        _update_health_metadata(info, ok=False)
        initial.update({"rolled_back": False, "healthy_ref": None, "tested_refs": tested_refs})
        return initial

    candidate_refs: list[str] = []
    if info.last_known_good_ref and info.last_known_good_ref != current_ref:
        candidate_refs.append(info.last_known_good_ref)

    rev_list = _git(info.worktree_dir, "rev-list", "--max-count", str(max(rollback_depth + 1, 2)), "HEAD")
    if rev_list.returncode == 0:
        for ref in [line.strip() for line in rev_list.stdout.splitlines() if line.strip()]:
            if ref not in candidate_refs and ref != current_ref:
                candidate_refs.append(ref)

    for ref in candidate_refs[: rollback_depth + 1]:
        tested_refs.append(ref)
        reset = _git(info.worktree_dir, "reset", "--hard", ref)
        if reset.returncode != 0:
            continue
        rerun = _run_healthcheck_once(info)
        if rerun["ok"]:
            _update_health_metadata(info, ok=True, ref=rerun["current_ref"])
            rerun.update(
                {
                    "rolled_back": rerun["current_ref"] != current_ref,
                    "healthy_ref": rerun["current_ref"],
                    "failed_ref": current_ref,
                    "tested_refs": tested_refs,
                }
            )
            return rerun

    _update_health_metadata(info, ok=False)
    initial.update({"rolled_back": False, "healthy_ref": None, "tested_refs": tested_refs})
    return initial


def _write_local_pr_fallback(info: ProfileForkInfo, title: str, body: str, branch: str) -> Path:
    diff_stat = _git(info.worktree_dir, "diff", "--stat", f"{info.default_pr_base}..HEAD")
    path = info.profile_dir_path / "workspace" / "self-improve-pr.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = [
        f"# {title}",
        "",
        f"Profile: {info.profile_name}",
        f"Base: {info.default_pr_base}",
        f"Head branch: {branch}",
        f"Remote: {info.default_pr_remote}",
        "",
        "## Body",
        body,
        "",
        "## Diffstat",
        "```",
        (diff_stat.stdout or diff_stat.stderr or "(no diffstat available)").strip(),
        "```",
    ]
    path.write_text("\n".join(content) + "\n", encoding="utf-8")
    return path


def submit_profile_fork_pr(profile_name: str | None, title: str, body: str | None = None, *, draft: bool = True) -> dict[str, Any]:
    status = get_profile_fork_status(profile_name)
    if not status["attached"]:
        raise FileNotFoundError("No Hermes fork attached to this profile. Run /self-improve init first.")
    info: ProfileForkInfo = status["info"]

    if status["dirty"]:
        raise ValueError("Worktree has uncommitted changes. Commit or stash before submitting a PR.")

    branch = status["current_branch"] or ""
    if branch == info.profile_branch:
        raise ValueError(
            f"Current branch is the profile trunk `{branch}`. Run `/self-improve branch <label>` before submitting."
        )

    body_text = body or (
        f"Experimental self-improvement change from Hermes profile `{status['profile_name']}`.\n\n"
        f"Validated with `/self-improve check` before submission."
    )

    gh_path = shutil.which("gh")
    if not gh_path:
        fallback = _write_local_pr_fallback(info, title, body_text, branch)
        return {"ok": False, "local_only": True, "fallback_path": str(fallback)}

    push = _git(info.worktree_dir, "push", "-u", info.default_pr_remote, branch)
    if push.returncode != 0:
        raise RuntimeError(push.stderr.strip() or push.stdout.strip() or "Failed to push branch")

    remote_url_result = _git(info.worktree_dir, "remote", "get-url", info.default_pr_remote)
    origin_url_result = _git(info.worktree_dir, "remote", "get-url", "origin")
    head_remote = _parse_github_remote(remote_url_result.stdout.strip()) if remote_url_result.returncode == 0 else None
    origin_remote = _parse_github_remote(origin_url_result.stdout.strip()) if origin_url_result.returncode == 0 else None
    if not head_remote or not origin_remote:
        fallback = _write_local_pr_fallback(info, title, body_text, branch)
        return {"ok": False, "local_only": True, "fallback_path": str(fallback)}

    head_owner, _ = head_remote
    upstream_owner, upstream_repo = origin_remote
    command = [
        gh_path,
        "pr",
        "create",
        "--repo",
        f"{upstream_owner}/{upstream_repo}",
        "--base",
        info.default_pr_base,
        "--head",
        f"{head_owner}:{branch}",
        "--title",
        title,
        "--body",
        body_text,
    ]
    if draft:
        command.append("--draft")

    created = _run(command, info.worktree_dir, timeout=120)
    if created.returncode != 0:
        fallback = _write_local_pr_fallback(info, title, body_text, branch)
        return {
            "ok": False,
            "local_only": True,
            "fallback_path": str(fallback),
            "error": created.stderr.strip() or created.stdout.strip(),
        }

    pr_url = (created.stdout.strip().splitlines() or [""])[-1].strip()
    return {"ok": True, "pr_url": pr_url, "branch": branch, "remote": info.default_pr_remote}


def profile_fork_summary_lines(profile_name: str | None = None, *, markdown: bool = False) -> list[str]:
    status = get_profile_fork_status(profile_name)
    prefix = "**Fork:**" if markdown else "Fork:"
    if not status["attached"]:
        return [f"{prefix} not attached"]

    info: ProfileForkInfo = status["info"]
    branch = status["current_branch"] or info.profile_branch
    head = status["head_short"] or _short_ref(info.last_known_good_ref)
    dirty = "dirty" if status["dirty"] else "clean"
    path_label = "**Worktree:**" if markdown else "Worktree:"
    branch_label = "**Branch:**" if markdown else "Branch:"
    head_label = "**HEAD:**" if markdown else "HEAD:"
    last_good_label = "**Last good:**" if markdown else "Last good:"
    return [
        f"{path_label} `{info.worktree_path}`" if markdown else f"{path_label} {info.worktree_path}",
        f"{branch_label} `{branch}` ({dirty})" if markdown else f"{branch_label} {branch} ({dirty})",
        f"{head_label} `{head}`" if markdown else f"{head_label} {head}",
        f"{last_good_label} `{_short_ref(info.last_known_good_ref)}`" if markdown else f"{last_good_label} {_short_ref(info.last_known_good_ref)}",
    ]


def format_profile_fork_status(profile_name: str | None = None, *, markdown: bool = False) -> str:
    status = get_profile_fork_status(profile_name)
    resolved_name = status["profile_name"]
    if not status["attached"]:
        if markdown:
            return (
                f"🧪 **Self-improve status for `{resolved_name}`**\n\n"
                "No Hermes fork attached yet.\n"
                "Run `/self-improve init` to create a profile-scoped worktree."
            )
        return (
            f"Self-improve status for {resolved_name}\n\n"
            "No Hermes fork attached yet.\n"
            "Run /self-improve init to create a profile-scoped worktree."
        )

    info: ProfileForkInfo = status["info"]
    lines = []
    if markdown:
        lines.append(f"🧪 **Self-improve status for `{resolved_name}`**")
        lines.append("")
        lines.append(f"**Source repo:** `{info.source_repo}`")
        lines.extend(profile_fork_summary_lines(resolved_name, markdown=True))
        lines.append(f"**Base branch:** `{info.base_branch}`")
        lines.append(f"**PR target:** `{info.default_pr_remote}` → `{info.default_pr_base}`")
        if info.last_healthcheck:
            lines.append(f"**Last check:** `{info.last_healthcheck}` ({'ok' if info.last_healthcheck_ok else 'failed'})")
    else:
        lines.append(f"Self-improve status for {resolved_name}")
        lines.append("")
        lines.append(f"Source repo: {info.source_repo}")
        lines.extend(profile_fork_summary_lines(resolved_name, markdown=False))
        lines.append(f"Base branch: {info.base_branch}")
        lines.append(f"PR target: {info.default_pr_remote} -> {info.default_pr_base}")
        if info.last_healthcheck:
            lines.append(f"Last check: {info.last_healthcheck} ({'ok' if info.last_healthcheck_ok else 'failed'})")
    return "\n".join(lines)


def _format_healthcheck_result(result: dict[str, Any], *, markdown: bool = False) -> str:
    ok = result["ok"]
    rolled_back = result.get("rolled_back", False)
    status_word = "passed" if ok else "failed"
    if markdown:
        lines = [f"🩺 **Self-improve check {status_word}**"]
        lines.append(f"**Branch:** `{result.get('current_branch') or 'HEAD'}`")
        lines.append(f"**Ref:** `{_short_ref(result.get('current_ref'))}`")
        if rolled_back:
            lines.append(f"**Rollback:** recovered from `{_short_ref(result.get('failed_ref'))}`")
        lines.append("")
    else:
        lines = [f"Self-improve check {status_word}"]
        lines.append(f"Branch: {result.get('current_branch') or 'HEAD'}")
        lines.append(f"Ref: {_short_ref(result.get('current_ref'))}")
        if rolled_back:
            lines.append(f"Rollback: recovered from {_short_ref(result.get('failed_ref'))}")
        lines.append("")

    for step in result.get("steps", []):
        marker = "✓" if step["exit_code"] == 0 else "✗"
        lines.append(f"{marker} {step['name']}: {step['command']}")
        if step["exit_code"] != 0:
            if step.get("stdout_tail"):
                lines.append(step["stdout_tail"])
            if step.get("stderr_tail"):
                lines.append(step["stderr_tail"])
    return "\n".join(lines).strip()


def _usage() -> str:
    return (
        "Usage: /self-improve [status|init|branch|check|rollback|submit] ...\n"
        "  /self-improve status\n"
        "  /self-improve init [repo_path]\n"
        "  /self-improve branch <label>\n"
        "  /self-improve check\n"
        "  /self-improve rollback\n"
        "  /self-improve submit <title>"
    )


def handle_self_improve_command(command_text: str, profile_name: str | None = None, *, markdown: bool = False) -> str:
    try:
        parts = shlex.split(command_text.strip())
    except ValueError as exc:
        return f"Could not parse command: {exc}"

    if not parts:
        return _usage()

    first = parts[0].lstrip("/")
    args = parts[1:] if first in {"self-improve", "self_improve"} else parts
    subcommand = args[0].lower() if args else "status"

    try:
        if subcommand in {"help", "-h", "--help"}:
            return _usage()
        if subcommand == "status":
            return format_profile_fork_status(profile_name, markdown=markdown)
        if subcommand == "init":
            repo_path = args[1] if len(args) > 1 else None
            result = initialize_profile_fork(profile_name, repo_path=repo_path)
            lines = [
                f"Initialized self-improve worktree for {result['profile_name']}",
                f"Worktree: {result['worktree_path']}",
                f"Branch:   {result['profile_branch']}",
                f"Base:     {result['base_branch']}",
                "",
                _format_healthcheck_result(result["healthcheck"], markdown=False),
            ]
            return "\n".join(lines)
        if subcommand == "branch":
            if len(args) < 2:
                return "Usage: /self-improve branch <label>"
            result = create_profile_fork_branch(profile_name, args[1])
            return (
                f"Created/switched to {result['branch']}\n"
                f"Worktree: {result['worktree_path']}\n"
                f"HEAD: {_short_ref(result['head'])}"
            )
        if subcommand == "check":
            result = run_profile_fork_healthcheck(profile_name, rollback_on_failure=False)
            return _format_healthcheck_result(result, markdown=markdown)
        if subcommand == "rollback":
            result = run_profile_fork_healthcheck(profile_name, rollback_on_failure=True)
            return _format_healthcheck_result(result, markdown=markdown)
        if subcommand == "submit":
            if len(args) < 2:
                return "Usage: /self-improve submit <title>"
            title = args[1]
            body = None
            if len(args) > 2:
                body = " ".join(args[2:])
            result = submit_profile_fork_pr(profile_name, title, body)
            if result.get("ok"):
                return f"PR created: {result['pr_url']}"
            if result.get("local_only"):
                if result.get("error"):
                    return (
                        f"PR submission fell back to a local draft: {result['fallback_path']}\n"
                        f"Reason: {result['error']}"
                    )
                return f"PR draft written locally: {result['fallback_path']}"
            return "PR submission failed."
        return _usage()
    except Exception as exc:
        return f"/self-improve error: {exc}"
