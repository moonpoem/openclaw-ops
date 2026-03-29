from pathlib import Path

from actions import (
    check_connection,
    check_latest_release,
    diagnose_environment,
    get_localhost_access_url,
    normalize_version_text,
    restart_openclaw,
    self_repair_openclaw,
    start_localhost_access,
    start_openclaw,
    stop_localhost_access,
    stop_openclaw,
)
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


def test_start_openclaw_starts_when_not_running(monkeypatch, tmp_path):
    class FakeRunner:
        def __init__(self, config):
            self.results = [
                CommandResult("pgrep -af 'openclaw gateway' || true", [], 0, "", "", 0.1),
                CommandResult("start_openclaw", [], 0, "__STARTED__:123:/tmp/openclaw-gateway.log\n", "", 0.2),
                CommandResult(
                    "pgrep -af 'openclaw gateway' || true",
                    [],
                    0,
                    "123 openclaw gateway\n",
                    "",
                    0.1,
                ),
            ]

        def run(self, remote_command, timeout_seconds=None, stream_callback=None):
            result = self.results.pop(0)
            if stream_callback:
                stream_callback("stdout", result.stdout)
            return result

    monkeypatch.setattr(actions, "SSHRunner", FakeRunner)
    result = start_openclaw(AppConfig(logs_dir=tmp_path))

    assert result.status == ActionStatus.SUCCESS
    assert result.summary["started"] is True
    assert result.message == "OpenClaw 启动成功"


def test_start_openclaw_is_idempotent(monkeypatch, tmp_path):
    class FakeRunner:
        def __init__(self, config):
            self.results = [
                CommandResult(
                    "pgrep -af 'openclaw gateway' || true",
                    [],
                    0,
                    "123 openclaw gateway\n",
                    "",
                    0.1,
                ),
            ]

        def run(self, remote_command, timeout_seconds=None, stream_callback=None):
            result = self.results.pop(0)
            if stream_callback:
                stream_callback("stdout", result.stdout)
            return result

    monkeypatch.setattr(actions, "SSHRunner", FakeRunner)
    result = start_openclaw(AppConfig(logs_dir=tmp_path))

    assert result.status == ActionStatus.SUCCESS
    assert result.summary["already_running"] is True
    assert result.message == "OpenClaw 已在运行"
    assert len(result.steps) == 1


def test_stop_openclaw_stops_running_process(monkeypatch, tmp_path):
    class FakeRunner:
        def __init__(self, config):
            self.results = [
                CommandResult(
                    "pgrep -af 'openclaw gateway' || true",
                    [],
                    0,
                    "123 openclaw gateway\n",
                    "",
                    0.1,
                ),
                CommandResult("stop_openclaw", [], 0, "", "", 0.2),
                CommandResult("pgrep -af 'openclaw gateway' || true", [], 0, "", "", 0.1),
            ]

        def run(self, remote_command, timeout_seconds=None, stream_callback=None):
            result = self.results.pop(0)
            if stream_callback:
                stream_callback("stdout", result.stdout)
            return result

    monkeypatch.setattr(actions, "SSHRunner", FakeRunner)
    result = stop_openclaw(AppConfig(logs_dir=tmp_path))

    assert result.status == ActionStatus.SUCCESS
    assert result.summary["stopped"] is True
    assert result.message == "OpenClaw 已停止"


def test_restart_openclaw_restarts_process(monkeypatch, tmp_path):
    class FakeRunner:
        def __init__(self, config):
            self.results = [
                CommandResult(
                    "pgrep -af 'openclaw gateway' || true",
                    [],
                    0,
                    "123 openclaw gateway\n",
                    "",
                    0.1,
                ),
                CommandResult("stop_before_restart", [], 0, "", "", 0.2),
                CommandResult("pgrep -af 'openclaw gateway' || true", [], 0, "", "", 0.1),
                CommandResult("start_after_restart", [], 0, "__STARTED__:456:/tmp/openclaw-gateway.log\n", "", 0.2),
                CommandResult(
                    "pgrep -af 'openclaw gateway' || true",
                    [],
                    0,
                    "456 openclaw gateway\n",
                    "",
                    0.1,
                ),
            ]

        def run(self, remote_command, timeout_seconds=None, stream_callback=None):
            result = self.results.pop(0)
            if stream_callback:
                stream_callback("stdout", result.stdout)
            return result

    monkeypatch.setattr(actions, "SSHRunner", FakeRunner)
    result = restart_openclaw(AppConfig(logs_dir=tmp_path))

    assert result.status == ActionStatus.SUCCESS
    assert result.summary["was_running"] is True
    assert result.summary["restarted"] is True
    assert result.message == "OpenClaw 重启成功"


def test_self_repair_openclaw_runs_doctor_then_verify(monkeypatch, tmp_path):
    class FakeRunner:
        def __init__(self, config):
            self.results = [
                CommandResult("openclaw doctor --repair", [], 0, "repair ok\n", "", 0.2),
                CommandResult("node -v", [], 0, "v20.0.0\n", "", 0.1),
                CommandResult("npm -v", [], 0, "10.0.0\n", "", 0.1),
                CommandResult("which openclaw", [], 0, "/opt/homebrew/bin/openclaw\n", "", 0.1),
                CommandResult("openclaw --version", [], 0, "2026.3.29\n", "", 0.1),
                CommandResult("openclaw gateway", [], 0, "gateway ok\n", "", 0.2),
            ]

        def run(self, remote_command, timeout_seconds=None, stream_callback=None):
            result = self.results.pop(0)
            if stream_callback:
                stream_callback("stdout", result.stdout)
            return result

    monkeypatch.setattr(actions, "SSHRunner", FakeRunner)
    result = self_repair_openclaw(AppConfig(logs_dir=tmp_path))

    assert result.status == ActionStatus.SUCCESS
    assert result.summary["repair_exit_code"] == 0
    assert result.summary["openclaw_version"] == "2026.3.29"
    assert result.message == "OpenClaw 自我修复完成"


def test_start_localhost_access_creates_tunnel_state(monkeypatch, tmp_path):
    launched = {}

    class FakeRunner:
        def __init__(self, config):
            self.config = config

        def build_tunnel_command(self, *, local_port: int, remote_port: int):
            return ["ssh", "-N", "-L", f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}", self.config.remote_host]

    class FakeProcess:
        pid = 43210

        def poll(self):
            return None

        def terminate(self):
            launched["terminated"] = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            launched["killed"] = True

    def fake_popen(command, **kwargs):
        launched["command"] = command
        return FakeProcess()

    monkeypatch.setattr(actions, "SSHRunner", FakeRunner)
    monkeypatch.setattr(actions, "_can_bind_local_port", lambda port: True)
    monkeypatch.setattr(actions, "_wait_for_local_port", lambda port, timeout_seconds=4.0: True)
    monkeypatch.setattr(actions, "_process_alive", lambda pid: True)
    monkeypatch.setattr(actions.subprocess, "Popen", fake_popen)

    result = start_localhost_access(AppConfig(logs_dir=tmp_path))

    assert result.status == ActionStatus.SUCCESS
    assert result.summary["localhost_url"] == "http://127.0.0.1:18789"
    assert launched["command"][-1] == "ops@example-host.local"
    assert get_localhost_access_url(AppConfig(logs_dir=tmp_path)) == "http://127.0.0.1:18789"


def test_stop_localhost_access_removes_tunnel_state(monkeypatch, tmp_path):
    config = AppConfig(logs_dir=tmp_path)
    state_file = tmp_path / ".localhost_tunnel_default.json"
    state_file.write_text(
        '{"pid": 43210, "localhost_url": "http://127.0.0.1:18789", "local_forward_port": 18789}',
        encoding="utf-8",
    )
    signals = []

    monkeypatch.setattr(actions, "_process_alive", lambda pid: False if signals else True)
    monkeypatch.setattr(actions.os, "kill", lambda pid, sig: signals.append((pid, sig)))

    result = stop_localhost_access(config)

    assert result.status == ActionStatus.SUCCESS
    assert result.message == "localhost 访问已关闭"
    assert signals == [(43210, actions.signal.SIGTERM)]
    assert not state_file.exists()


def test_start_localhost_access_fails_when_port_is_in_use(monkeypatch, tmp_path):
    class FakeRunner:
        def __init__(self, config):
            self.config = config

        def build_tunnel_command(self, *, local_port: int, remote_port: int):
            return ["ssh", self.config.remote_host]

    monkeypatch.setattr(actions, "SSHRunner", FakeRunner)
    monkeypatch.setattr(actions, "_can_bind_local_port", lambda port: False)

    result = start_localhost_access(AppConfig(logs_dir=tmp_path))

    assert result.status == ActionStatus.FAILED
    assert result.message == "本地端口已被占用：18789"


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
