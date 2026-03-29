from pathlib import Path

from actions import (
    check_connection,
    check_latest_release,
    diagnose_environment,
    get_localhost_access_url,
    normalize_version_text,
    prepare_localhost_webui,
    repair_and_upgrade,
    restart_openclaw,
    self_repair_openclaw,
    start_localhost_access,
    start_openclaw,
    stop_localhost_access,
    stop_openclaw,
    verify_openclaw,
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
    results = [
        CommandResult("hostname", ["ssh", "ops@example-host.local", "hostname"], 0, "example-host\n", "", 0.1),
        CommandResult("printf '%s\n' \"$PATH\"", [], 0, "/usr/bin\n", "", 0.1),
        CommandResult("node -v", [], 0, "v20.0.0\n", "", 0.1),
        CommandResult("npm -v", [], 0, "10.0.0\n", "", 0.1),
        CommandResult("npm root -g", [], 0, "/opt/homebrew/lib/node_modules\n", "", 0.1),
        CommandResult("which openclaw", [], 0, "/opt/homebrew/bin/openclaw\n", "", 0.1),
        CommandResult("openclaw --version", [], 0, "2026.3.29\n", "", 0.1),
        CommandResult("npm view openclaw version", [], 0, "2026.3.29\n", "", 0.1),
        CommandResult("gateway_token_status", [], 0, "configured\n", "", 0.1),
        CommandResult("npm_home_exists", [], 0, "exists\n", "", 0.1),
        CommandResult("npm_home_stat", [], 0, "drwx------ moon staff ~/.npm\n", "", 0.1),
        CommandResult("global_openclaw_exists", [], 0, "exists\n", "", 0.1),
        CommandResult("global_openclaw_residue", [], 0, "\n", "", 0.1),
        CommandResult("node -v", [], 0, "v20.0.0\n", "", 0.1),
        CommandResult("npm -v", [], 0, "10.0.0\n", "", 0.1),
        CommandResult("which openclaw", [], 0, "/opt/homebrew/bin/openclaw\n", "", 0.1),
        CommandResult("openclaw --version", [], 0, "2026.3.29\n", "", 0.1),
        CommandResult("openclaw status --all", [], 0, "all services ok\n", "", 0.1),
        CommandResult("openclaw health --json", [], 0, '{"ok":true}\n', "", 0.1),
    ]

    class FakeRunner:
        def __init__(self, config):
            self.results = results

        def run(self, remote_command, timeout_seconds=None, stream_callback=None):
            result = self.results.pop(0)
            if stream_callback:
                stream_callback("stdout", result.stdout)
            return result

    monkeypatch.setattr(actions, "SSHRunner", FakeRunner)
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
                    command="openclaw status --all",
                    full_command=["ssh", "ops@example-host.local", "openclaw status --all"],
                    exit_code=0,
                    stdout="all services ok\n",
                    stderr="",
                    duration_seconds=0.1,
                ),
                CommandResult(
                    command="openclaw health --json",
                    full_command=["ssh", "ops@example-host.local", "openclaw health --json"],
                    exit_code=0,
                    stdout='{"ok":true}\n',
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
                CommandResult("openclaw gateway start", [], 0, "gateway started\n", "", 0.2),
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
                CommandResult("openclaw gateway stop", [], 0, "gateway stopped\n", "", 0.2),
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
                CommandResult("openclaw gateway stop", [], 0, "gateway stopped\n", "", 0.2),
                CommandResult("pgrep -af 'openclaw gateway' || true", [], 0, "", "", 0.1),
                CommandResult("openclaw gateway start", [], 0, "gateway started\n", "", 0.2),
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
                CommandResult("openclaw status --all", [], 0, "all services ok\n", "", 0.2),
                CommandResult("openclaw health --json", [], 0, '{"ok":true}\n', "", 0.2),
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


def test_verify_openclaw_ignores_historical_gateway_log_token_errors(monkeypatch, tmp_path):
    class FakeRunner:
        def __init__(self, config):
            self.results = [
                CommandResult("node -v", [], 0, "v23.9.0\n", "", 0.1),
                CommandResult("npm -v", [], 0, "10.9.2\n", "", 0.1),
                CommandResult("which openclaw", [], 0, "/opt/homebrew/bin/openclaw\n", "", 0.1),
                CommandResult("openclaw --version", [], 0, "OpenClaw 2026.3.28 (f9b1079)\n", "", 0.1),
                CommandResult(
                    "openclaw status --all",
                    [],
                    0,
                    "Gateway         | local · ws://127.0.0.1:18789 (local loopback) · reachable 40ms · auth token\n"
                    "\nGateway logs (tail, summarized): /Users/moon/.openclaw/logs\n"
                    "2026-03-29T21:04:14.279+08:00 [ws] unauthorized reason=token_mismatch\n",
                    "",
                    0.2,
                ),
                CommandResult("openclaw health --json", [], 0, '{"ok":true}\n', "", 0.2),
            ]

        def run(self, remote_command, timeout_seconds=None, stream_callback=None):
            result = self.results.pop(0)
            if stream_callback:
                stream_callback("stdout", result.stdout)
            return result

    monkeypatch.setattr(actions, "SSHRunner", FakeRunner)
    result = verify_openclaw(AppConfig(logs_dir=tmp_path))

    assert result.status == ActionStatus.SUCCESS
    assert result.summary["verdict"] == "Healthy"
    assert result.summary["reasons"] == []


def test_verify_openclaw_warns_when_gateway_token_missing_in_current_status(monkeypatch, tmp_path):
    class FakeRunner:
        def __init__(self, config):
            self.results = [
                CommandResult("node -v", [], 0, "v23.9.0\n", "", 0.1),
                CommandResult("npm -v", [], 0, "10.9.2\n", "", 0.1),
                CommandResult("which openclaw", [], 0, "/opt/homebrew/bin/openclaw\n", "", 0.1),
                CommandResult("openclaw --version", [], 0, "OpenClaw 2026.3.28 (f9b1079)\n", "", 0.1),
                CommandResult(
                    "openclaw status --all",
                    [],
                    0,
                    "Gateway         | local · ws://127.0.0.1:18789 (local loopback)\n"
                    "unauthorized: gateway token missing (open the dashboard URL and paste the token in Control UI settings)\n",
                    "",
                    0.2,
                ),
                CommandResult("openclaw health --json", [], 0, '{"ok":true}\n', "", 0.2),
            ]

        def run(self, remote_command, timeout_seconds=None, stream_callback=None):
            result = self.results.pop(0)
            if stream_callback:
                stream_callback("stdout", result.stdout)
            return result

    monkeypatch.setattr(actions, "SSHRunner", FakeRunner)
    result = verify_openclaw(AppConfig(logs_dir=tmp_path))

    assert result.status == ActionStatus.WARNING
    assert result.summary["verdict"] == "Warning"
    assert result.summary["reasons"] == ["gateway token missing"]


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
    monkeypatch.setattr(actions, "_wait_for_local_port", lambda port, timeout_seconds=0.5: False)

    result = start_localhost_access(AppConfig(logs_dir=tmp_path))

    assert result.status == ActionStatus.FAILED
    assert result.message == "本地端口已被占用：18789"


def test_start_localhost_access_reuses_existing_listener(monkeypatch, tmp_path):
    class FakeRunner:
        def __init__(self, config):
            self.config = config

        def build_tunnel_command(self, *, local_port: int, remote_port: int):
            return ["ssh", self.config.remote_host]

    monkeypatch.setattr(actions, "SSHRunner", FakeRunner)
    monkeypatch.setattr(actions, "_can_bind_local_port", lambda port: False)
    monkeypatch.setattr(actions, "_wait_for_local_port", lambda port, timeout_seconds=0.5: True)

    result = start_localhost_access(AppConfig(logs_dir=tmp_path))

    assert result.status == ActionStatus.SUCCESS
    assert result.summary["reused_existing_listener"] is True


def test_prepare_localhost_webui_uses_gateway_token(monkeypatch, tmp_path):
    config = AppConfig(logs_dir=tmp_path)
    state_file = tmp_path / ".localhost_tunnel_default.json"
    state_file.write_text(
        '{"pid": 43210, "localhost_url": "http://127.0.0.1:18789", "local_forward_port": 18789}',
        encoding="utf-8",
    )

    class FakeRunner:
        def __init__(self, config):
            self.results = [
                CommandResult("gateway token", [], 0, "abc123\n", "", 0.1),
            ]

        def run(self, remote_command, timeout_seconds=None, stream_callback=None):
            return self.results.pop(0)

    monkeypatch.setattr(actions, "SSHRunner", FakeRunner)
    monkeypatch.setattr(actions, "_process_alive", lambda pid: True)

    result = prepare_localhost_webui(config)

    assert result.status == ActionStatus.SUCCESS
    assert result.summary["token_ready"] is True
    assert result.summary["launch_url"] == "http://127.0.0.1:18789#token=abc123"


def test_repair_and_upgrade_stops_without_cleanup_on_ssh_failure(monkeypatch, tmp_path):
    calls = []

    def fake_upgrade(config, ui_callback=None):
        calls.append("upgrade")
        return actions.ActionResult(
            action_name="升级 OpenClaw",
            status=ActionStatus.FAILED,
            started_at="2026-03-29T20:00:01",
            finished_at="2026-03-29T20:00:05",
            duration_seconds=4,
            summary={
                "upgrade_exit_code": 255,
                "upgrade_ssh_issue": "ssh transport failed",
                "upgrade_timed_out": False,
            },
            message="upgrade failed: ssh transport failed",
        )

    monkeypatch.setattr(actions, "upgrade_openclaw", fake_upgrade)

    result = repair_and_upgrade(AppConfig(logs_dir=tmp_path))

    assert result.status == ActionStatus.FAILED
    assert result.summary["failed_action"] == "upgrade_openclaw"
    assert calls == ["upgrade"]


def test_repair_and_upgrade_starts_after_successful_upgrade(monkeypatch, tmp_path):
    calls = []

    def fake_upgrade(config, ui_callback=None):
        calls.append("upgrade")
        return actions.ActionResult(
            action_name="升级 OpenClaw",
            status=ActionStatus.SUCCESS,
            started_at="2026-03-29T20:00:01",
            finished_at="2026-03-29T20:00:05",
            duration_seconds=4,
            summary={"skipped_upgrade": False},
            message="Healthy",
        )

    def fake_start(config, ui_callback=None):
        calls.append("start")
        return actions.ActionResult(
            action_name="启动 OpenClaw",
            status=ActionStatus.SUCCESS,
            started_at="2026-03-29T20:00:05",
            finished_at="2026-03-29T20:00:10",
            duration_seconds=5,
            summary={"started": True},
            message="OpenClaw 启动成功",
        )

    monkeypatch.setattr(actions, "upgrade_openclaw", fake_upgrade)
    monkeypatch.setattr(actions, "start_openclaw", fake_start)

    result = repair_and_upgrade(AppConfig(logs_dir=tmp_path))

    assert result.status == ActionStatus.SUCCESS
    assert result.summary["strategy"] == "upgrade_then_start"
    assert result.message == "升级并启动完成"
    assert calls == ["upgrade", "start"]


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
                CommandResult("npm root -g", [], 0, "/opt/homebrew/lib/node_modules\n", "", 0.1),
                CommandResult("which openclaw", [], 0, "/opt/homebrew/bin/openclaw\n", "", 0.1),
                CommandResult("openclaw --version", [], 0, "OpenClaw 2026.3.23-2 (7ffe7e4)\n", "", 0.1),
                CommandResult("npm view openclaw version", [], 0, "2026.3.24-1\n", "", 0.1),
                CommandResult("gateway_token_status", [], 0, "configured\n", "", 0.1),
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
