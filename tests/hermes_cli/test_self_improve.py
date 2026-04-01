import argparse
import subprocess
from pathlib import Path

import pytest

from hermes_cli.main import cmd_profile
from hermes_cli.profiles import create_profile, delete_profile, rename_profile
from hermes_cli.self_improve import (
    _candidate_branch_name,
    _healthcheck_commands_for_repo,
    create_profile_fork_branch,
    get_profile_fork_status,
    handle_self_improve_command,
    initialize_profile_fork,
    load_profile_fork_info,
    run_profile_fork_healthcheck,
    submit_profile_fork_pr,
)


@pytest.fixture()
def profile_env(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    default_home = tmp_path / ".hermes"
    default_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    return tmp_path


@pytest.fixture()
def hermes_like_repo(tmp_path):
    repo = tmp_path / "hermes-repo"
    (repo / "hermes_cli").mkdir(parents=True)
    (repo / "gateway").mkdir(parents=True)

    (repo / "hermes_cli" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "cli.py").write_text(
        "import sys\n"
        "from hermes_cli.main import main\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n",
        encoding="utf-8",
    )
    (repo / "gateway" / "run.py").write_text("print('gateway import ok')\n", encoding="utf-8")
    (repo / "hermes_cli" / "commands.py").write_text("COMMANDS = {}\n", encoding="utf-8")
    (repo / "hermes_cli" / "profiles.py").write_text("PROFILE = 'ok'\n", encoding="utf-8")
    (repo / "hermes_cli" / "main.py").write_text(
        "import argparse\n"
        "\n"
        "def main():\n"
        "    parser = argparse.ArgumentParser(prog='hermes')\n"
        "    parser.add_argument('-p', '--profile')\n"
        "    sub = parser.add_subparsers(dest='cmd')\n"
        "    sub.add_parser('profile')\n"
        "    args = parser.parse_args()\n"
        "    if args.cmd == 'profile':\n"
        "        profile_label = args.profile or 'env'\n"
        "        print(f'PROFILE_LAUNCH_OK={profile_label}')\n"
        "    return 0\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n",
        encoding="utf-8",
    )

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
    return repo


class TestSelfImproveHelpers:
    def test_initialize_profile_fork_creates_worktree_and_metadata(self, profile_env, hermes_like_repo):
        create_profile("coder", no_alias=True)

        result = initialize_profile_fork("coder", repo_path=str(hermes_like_repo))

        assert Path(result["worktree_path"]).is_dir()
        assert result["profile_branch"] == "profiles/coder/main"
        assert result["healthcheck"]["ok"] is True

        info = load_profile_fork_info("coder")
        assert info is not None
        assert Path(info.worktree_path).is_dir()
        assert info.last_known_good_ref is not None

    def test_healthcheck_uses_real_profile_launcher_command(self, profile_env, hermes_like_repo):
        create_profile("coder", no_alias=True)
        initialize_profile_fork("coder", repo_path=str(hermes_like_repo))
        info = load_profile_fork_info("coder")
        assert info is not None

        commands = _healthcheck_commands_for_repo(info)

        assert any(
            name == "profile_launcher" and command[1].endswith("hermes_cli/main.py") and command[2:] == ["-p", "coder", "profile"]
            for name, command in commands
        )
        assert not any(
            name in {"cli_help", "cli_profile"} and command[1:3] == ["-m", "hermes_cli.main"]
            for name, command in commands
        )

    def test_create_profile_fork_branch_uses_profile_namespace(self, profile_env, hermes_like_repo):
        create_profile("coder", no_alias=True)
        initialize_profile_fork("coder", repo_path=str(hermes_like_repo))

        result = create_profile_fork_branch("coder", "Improve Help Output")

        assert result["branch"] == _candidate_branch_name("coder", "Improve Help Output")
        status = get_profile_fork_status("coder")
        assert status["current_branch"] == result["branch"]

    def test_healthcheck_rollback_restores_last_known_good_ref(self, profile_env, hermes_like_repo):
        create_profile("coder", no_alias=True)
        initialize_profile_fork("coder", repo_path=str(hermes_like_repo))
        info = load_profile_fork_info("coder")
        assert info is not None

        broken_file = Path(info.worktree_path) / "hermes_cli" / "main.py"
        broken_file.write_text("def broken(:\n", encoding="utf-8")
        subprocess.run(["git", "add", "hermes_cli/main.py"], cwd=info.worktree_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "break startup"], cwd=info.worktree_dir, check=True, capture_output=True)

        result = run_profile_fork_healthcheck("coder", rollback_on_failure=True)

        assert result["ok"] is True
        assert result["rolled_back"] is True
        assert result["healthy_ref"] == info.last_known_good_ref
        assert load_profile_fork_info("coder").last_known_good_ref == info.last_known_good_ref

    def test_submit_pr_falls_back_to_local_draft_without_gh(self, profile_env, hermes_like_repo, monkeypatch):
        create_profile("coder", no_alias=True)
        initialize_profile_fork("coder", repo_path=str(hermes_like_repo))
        create_profile_fork_branch("coder", "draft-pr")

        monkeypatch.setattr("hermes_cli.self_improve.shutil.which", lambda _: None)

        result = submit_profile_fork_pr("coder", "Test PR")

        assert result["local_only"] is True
        assert Path(result["fallback_path"]).exists()

    def test_delete_profile_cleans_up_attached_worktree(self, profile_env, hermes_like_repo):
        create_profile("coder", no_alias=True)
        init = initialize_profile_fork("coder", repo_path=str(hermes_like_repo))
        worktree_path = Path(init["worktree_path"])
        assert worktree_path.exists()

        delete_profile("coder", yes=True)

        assert not worktree_path.exists()

    def test_rename_profile_rejected_when_fork_attached(self, profile_env, hermes_like_repo):
        create_profile("coder", no_alias=True)
        initialize_profile_fork("coder", repo_path=str(hermes_like_repo))

        with pytest.raises(ValueError, match="attached self-improve worktree"):
            rename_profile("coder", "renamed")

    def test_handle_self_improve_status_reports_unattached_profile(self, profile_env):
        create_profile("coder", no_alias=True)

        message = handle_self_improve_command("/self-improve status", profile_name="coder")

        assert "No Hermes fork attached yet" in message


class TestProfileCreateIntegration:
    def test_cmd_profile_create_bootstraps_current_repo_fork(self, profile_env, monkeypatch, capsys):
        calls = {}

        def fake_initialize(profile_name, base_ref="HEAD"):
            calls["profile_name"] = profile_name
            calls["base_ref"] = base_ref
            return {
                "worktree_path": "/tmp/profile-worktree",
                "profile_branch": f"profiles/{profile_name}/main",
                "healthcheck": {"ok": True, "current_ref_short": "abc1234"},
            }

        monkeypatch.setattr("hermes_cli.profiles.seed_profile_skills", lambda profile_dir: {"copied": []})
        monkeypatch.setattr("hermes_cli.self_improve.initialize_profile_fork", fake_initialize)

        args = argparse.Namespace(
            profile_action="create",
            profile_name="coder",
            clone=False,
            clone_all=False,
            clone_from=None,
            no_alias=True,
            fork_current_repo=True,
            fork_base="HEAD~1",
        )

        cmd_profile(args)
        output = capsys.readouterr().out

        assert calls == {"profile_name": "coder", "base_ref": "HEAD~1"}
        assert "Attached Hermes fork" in output
        assert "Healthcheck:" in output
