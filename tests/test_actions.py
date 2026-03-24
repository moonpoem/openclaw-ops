from pathlib import Path

from actions import check_connection, check_latest_release, diagnose_environment, normalize_version_text
from config import AppConfig, HostConfig
from models import ActionStatus, CommandResult
import actions


class FakeRunner:
    def __init__(self, result):
        self.result = result

    def run(self, remote_command, timeout_seconds=None, stream_callback=None):
        if stream_callback:
            stream_callback("stdout", self.result.stdout)
        return self.result


def test_check_connection_returns_structured_result(monkeypatch, tmp_path):
    result = CommandResult(
        command="hostname",
        full_command=["ssh", "ops@example-host.local", "hostname"],
        exit_code=0,
        stdout="example-host\n",
        stderr="",
        duration_seconds=0.42,
    )
    monkeypatch.setattr(actions, "SSHRunner", lambda config: FakeRunner(result))
    config = AppConfig(logs_dir=tmp_path)
    action_result = check_connection(config)
    assert action_result.status == ActionStatus.SUCCESS
    assert action_result.summary["target_host"] == "example-host"
    assert Path(action_result.log_path).exists()


def test_fallback_source_build_expands_remote_workdir(monkeypatch, tmp_path):
    commands = []

    class RecordingRunner:
        def __init__(self, config):
            self.config = config

        def run(self, remote_command, timeout_seconds=None, stream_callback=None):
            commands.append(remote_command)
            return CommandResult(
                command=remote_command,
                full_command=["ssh", "ops@example-host.local", remote_command],
                exit_code=0,
                stdout="ok\n",
                stderr="",
                duration_seconds=0.1,
            )

    monkeypatch.setattr(actions, "SSHRunner", RecordingRunner)
    config = AppConfig(
        logs_dir=tmp_path,
        profiles={
            "default": HostConfig(remote_workdir="$HOME/openclaw-src"),
        },
    )
    action_result = actions.fallback_source_build(config)

    assert action_result.status == ActionStatus.SUCCESS
    assert commands
    assert 'mkdir -p "$HOME/openclaw-src"' in commands[0]
    assert "cd \"$HOME/openclaw-src\" && pnpm install" in commands[1]


def test_upgrade_openclaw_keeps_single_log_file(monkeypatch, tmp_path):
    class FakeRunner:
        def __init__(self, config):
            self.results = [
                CommandResult(
                    command="openclaw --version",
                    full_command=["ssh", "ops@example-host.local", "openclaw --version"],
                    exit_code=0,
                    stdout="1.2.2\n",
                    stderr="",
                    duration_seconds=0.1,
                ),
                CommandResult(
                    command="npm view openclaw version",
                    full_command=["ssh", "ops@example-host.local", "npm view openclaw version"],
                    exit_code=0,
                    stdout="1.2.3\n",
                    stderr="",
                    duration_seconds=0.1,
                ),
                CommandResult(
                    command="npm install -g openclaw@latest",
                    full_command=["ssh", "ops@example-host.local", "npm install -g openclaw@latest"],
                    exit_code=0,
                    stdout="installed\n",
                    stderr="",
                    duration_seconds=0.2,
                ),
                CommandResult(
                    command="node -v",
                    full_command=["ssh", "ops@example-host.local", "node -v"],
                    exit_code=0,
                    stdout="v20.0.0\n",
                    stderr="",
                    duration_seconds=0.1,
                ),
                CommandResult(
                    command="npm -v",
                    full_command=["ssh", "ops@example-host.local", "npm -v"],
                    exit_code=0,
                    stdout="10.0.0\n",
                    stderr="",
                    duration_seconds=0.1,
                ),
                CommandResult(
                    command="which openclaw",
                    full_command=["ssh", "ops@example-host.local", "which openclaw"],
                    exit_code=0,
                    stdout="/opt/homebrew/bin/openclaw\n",
                    stderr="",
                    duration_seconds=0.1,
                ),
                CommandResult(
                    command="openclaw --version",
                    full_command=["ssh", "ops@example-host.local", "openclaw --version"],
                    exit_code=0,
                    stdout="1.2.3\n",
                    stderr="",
                    duration_seconds=0.1,
                ),
                CommandResult(
                    command="openclaw gateway",
                    full_command=["ssh", "ops@example-host.local", "openclaw gateway"],
                    exit_code=0,
                    stdout="gateway ok\n",
                    stderr="",
                    duration_seconds=0.1,
                ),
            ]

        def run(self, remote_command, timeout_seconds=None, stream_callback=None):
            result = self.results.pop(0)
            if stream_callback:
                stream_callback("stdout", result.stdout)
            return result

    monkeypatch.setattr(actions, "SSHRunner", FakeRunner)
    config = AppConfig(logs_dir=tmp_path)
    action_result = actions.upgrade_openclaw(config)

    assert action_result.status == ActionStatus.SUCCESS
    assert len(list(tmp_path.glob("*.log"))) == 1


def test_upgrade_openclaw_skips_when_already_latest(monkeypatch, tmp_path):
    class FakeRunner:
        def __init__(self, config):
            self.results = [
                CommandResult(
                    command="openclaw --version",
                    full_command=["ssh", "ops@example-host.local", "openclaw --version"],
                    exit_code=0,
                    stdout="v1.2.3\n",
                    stderr="",
                    duration_seconds=0.1,
                ),
                CommandResult(
                    command="npm view openclaw version",
                    full_command=["ssh", "ops@example-host.local", "npm view openclaw version"],
                    exit_code=0,
                    stdout="1.2.3\n",
                    stderr="",
                    duration_seconds=0.1,
                ),
            ]

        def run(self, remote_command, timeout_seconds=None, stream_callback=None):
            result = self.results.pop(0)
            if stream_callback:
                stream_callback("stdout", result.stdout)
            return result

    monkeypatch.setattr(actions, "SSHRunner", FakeRunner)
    config = AppConfig(logs_dir=tmp_path)
    action_result = actions.upgrade_openclaw(config)

    assert action_result.status == ActionStatus.SUCCESS
    assert action_result.summary["skipped_upgrade"] is True
    assert action_result.summary["current_version"] == "1.2.3"
    assert action_result.message == "当前已是最新版，无需升级（1.2.3）"
    assert len(action_result.steps) == 2


def test_check_latest_release_reports_up_to_date(monkeypatch, tmp_path):
    class FakeRunner:
        def __init__(self, config):
            self.results = [
                CommandResult(
                    command="openclaw --version",
                    full_command=["ssh", "ops@example-host.local", "openclaw --version"],
                    exit_code=0,
                    stdout="v1.2.3\n",
                    stderr="",
                    duration_seconds=0.1,
                ),
                CommandResult(
                    command="npm view openclaw version",
                    full_command=["ssh", "ops@example-host.local", "npm view openclaw version"],
                    exit_code=0,
                    stdout="1.2.3\n",
                    stderr="",
                    duration_seconds=0.1,
                ),
            ]

        def run(self, remote_command, timeout_seconds=None, stream_callback=None):
            result = self.results.pop(0)
            if stream_callback:
                stream_callback("stdout", result.stdout)
            return result

    monkeypatch.setattr(actions, "SSHRunner", FakeRunner)
    result = check_latest_release(AppConfig(logs_dir=tmp_path))

    assert result.status == ActionStatus.SUCCESS
    assert result.summary["up_to_date"] is True
    assert result.message == "当前已是最新版（1.2.3）"


def test_check_latest_release_reports_update_available(monkeypatch, tmp_path):
    class FakeRunner:
        def __init__(self, config):
            self.results = [
                CommandResult(
                    command="openclaw --version",
                    full_command=["ssh", "ops@example-host.local", "openclaw --version"],
                    exit_code=0,
                    stdout="1.2.2\n",
                    stderr="",
                    duration_seconds=0.1,
                ),
                CommandResult(
                    command="npm view openclaw version",
                    full_command=["ssh", "ops@example-host.local", "npm view openclaw version"],
                    exit_code=0,
                    stdout="1.2.3\n",
                    stderr="",
                    duration_seconds=0.1,
                ),
            ]

        def run(self, remote_command, timeout_seconds=None, stream_callback=None):
            result = self.results.pop(0)
            if stream_callback:
                stream_callback("stdout", result.stdout)
            return result

    monkeypatch.setattr(actions, "SSHRunner", FakeRunner)
    result = check_latest_release(AppConfig(logs_dir=tmp_path))

    assert result.status == ActionStatus.WARNING
    assert result.summary["up_to_date"] is False
    assert result.message == "发现新版本：1.2.3，当前版本：1.2.2"


def test_normalize_version_text_extracts_release_from_banner():
    assert normalize_version_text("OpenClaw 2026.3.23-2 (7ffe7e4)") == "2026.3.23-2"


def test_check_latest_release_accepts_banner_style_current_version(monkeypatch, tmp_path):
    class FakeRunner:
        def __init__(self, config):
            self.results = [
                CommandResult(
                    command="openclaw --version",
                    full_command=["ssh", "ops@example-host.local", "openclaw --version"],
                    exit_code=0,
                    stdout="OpenClaw 2026.3.23-2 (7ffe7e4)\n",
                    stderr="",
                    duration_seconds=0.1,
                ),
                CommandResult(
                    command="npm view openclaw version",
                    full_command=["ssh", "ops@example-host.local", "npm view openclaw version"],
                    exit_code=0,
                    stdout="2026.3.23-2\n",
                    stderr="",
                    duration_seconds=0.1,
                ),
            ]

        def run(self, remote_command, timeout_seconds=None, stream_callback=None):
            result = self.results.pop(0)
            if stream_callback:
                stream_callback("stdout", result.stdout)
            return result

    monkeypatch.setattr(actions, "SSHRunner", FakeRunner)
    result = check_latest_release(AppConfig(logs_dir=tmp_path))

    assert result.status == ActionStatus.SUCCESS
    assert result.summary["current_version"] == "2026.3.23-2"
    assert result.summary["latest_version"] == "2026.3.23-2"
    assert result.summary["up_to_date"] is True


def test_diagnose_environment_warns_when_upgrade_is_needed(monkeypatch, tmp_path):
    class FakeRunner:
        def __init__(self, config):
            self.results = [
                CommandResult("printf '%s\n' \"$PATH\"", [], 0, "/usr/bin\n", "", 0.1),
                CommandResult("node -v", [], 0, "v20.0.0\n", "", 0.1),
                CommandResult("npm -v", [], 0, "10.0.0\n", "", 0.1),
                CommandResult("which openclaw", [], 0, "/opt/homebrew/bin/openclaw\n", "", 0.1),
                CommandResult("openclaw --version", [], 0, "OpenClaw 2026.3.23-2 (7ffe7e4)\n", "", 0.1),
                CommandResult("npm view openclaw version", [], 0, "2026.3.24-1\n", "", 0.1),
                CommandResult("npm_home_exists", [], 0, "exists\n", "", 0.1),
                CommandResult("npm_home_stat", [], 0, "drwx------ moon staff ~/.npm\n", "", 0.1),
                CommandResult("global_openclaw_exists", [], 0, "exists\n", "", 0.1),
                CommandResult("global_openclaw_residue", [], 0, "\n", "", 0.1),
            ]

        def run(self, remote_command, timeout_seconds=None, stream_callback=None):
            result = self.results.pop(0)
            if stream_callback:
                stream_callback("stdout", result.stdout)
            return result

    monkeypatch.setattr(actions, "SSHRunner", FakeRunner)
    result = diagnose_environment(AppConfig(logs_dir=tmp_path))

    assert result.status == ActionStatus.WARNING
    assert result.message == "需升级"
    assert result.summary["current_version_normalized"] == "2026.3.23-2"
    assert result.summary["latest_version_normalized"] == "2026.3.24-1"
    assert result.summary["up_to_date"] is False
