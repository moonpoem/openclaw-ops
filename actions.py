from __future__ import annotations

from datetime import datetime
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import time

from config import AppConfig
from logging_utils import ActionLogger, create_log_file
from models import ActionResult, ActionStatus, CommandResult, StepResult
from ssh_runner import SSHRunner


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def format_summary(summary: dict) -> str:
    return json.dumps(summary, ensure_ascii=False, indent=2)


def detect_ui_assets_issue(text: str) -> bool:
    return "Control UI assets not found" in text


def normalize_version_text(text: str) -> str:
    normalized = text.strip()
    if normalized.startswith("v"):
        normalized = normalized[1:]
    match = re.search(r"(\d+\.\d+\.\d+(?:[-._][0-9A-Za-z]+)*)", normalized)
    if match:
        return match.group(1)
    return normalized


def shell_quote_remote(value: str, *, allow_env_expansion: bool = False) -> str:
    if allow_env_expansion and "$" in value:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return shlex.quote(value)


def run_action(action_name: str, config: AppConfig, ui_callback, worker) -> ActionResult:
    started_at = now_iso()
    log_path = create_log_file(config.logs_dir, action_name)
    logger = ActionLogger(log_path=log_path, ui_callback=ui_callback)
    logger.header(action_name, started_at)
    runner = SSHRunner(config)
    started = time.monotonic()
    try:
        result = worker(runner, logger, config, started_at)
    except Exception as exc:
        finished_at = now_iso()
        duration = time.monotonic() - started
        logger.write(f"unhandled_exception: {exc}")
        logger.footer(finished_at, ActionStatus.FAILED.value, duration)
        return ActionResult(
            action_name=action_name,
            status=ActionStatus.FAILED,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration,
            steps=[],
            summary={"error": str(exc)},
            log_path=str(log_path),
            message=str(exc),
        )
    result.log_path = str(log_path)
    logger.write_json("summary", result.summary)
    logger.footer(result.finished_at, result.status.value, result.duration_seconds)
    return result


def _stream_log(logger: ActionLogger):
    def callback(label: str, chunk: str) -> None:
        prefix = "" if label == "stdout" else "[stderr] "
        logger.write(f"{prefix}{chunk.rstrip()}")

    return callback


def _step_status(command_result, warn_on_non_zero: bool = False) -> ActionStatus:
    if command_result.exit_code == 0:
        return ActionStatus.SUCCESS
    if warn_on_non_zero:
        return ActionStatus.WARNING
    return ActionStatus.FAILED


def _run_remote_step(
    runner: SSHRunner,
    logger: ActionLogger,
    name: str,
    command: str,
    timeout: int,
    warn_on_non_zero: bool = False,
) -> StepResult:
    logger.write(f"==> {name}")
    result = runner.run(command, timeout_seconds=timeout, stream_callback=_stream_log(logger))
    logger.write_command_result(name, result)
    status = _step_status(result, warn_on_non_zero=warn_on_non_zero)
    return StepResult(
        name=name,
        status=status,
        command_result=result,
        message=result.ssh_issue or "",
    )


def _verify_openclaw_with_runner(
    runner: SSHRunner,
    logger: ActionLogger,
    config: AppConfig,
    started_at: str,
) -> ActionResult:
    checks = [
        ("node_version", "node -v"),
        ("npm_version", "npm -v"),
        ("openclaw_path", "which openclaw"),
        ("openclaw_version", "openclaw --version"),
        ("openclaw_status", "openclaw status --all"),
        ("openclaw_health", "openclaw health --json"),
    ]

    steps = []
    outputs = {}
    for name, command in checks:
        step = _run_remote_step(runner, logger, name, command, config.command_timeout_seconds, True)
        steps.append(step)
        outputs[name] = (step.command_result.stdout or step.command_result.stderr).strip()

    reasons = []
    status_output = outputs.get("openclaw_status", "")
    health_output = outputs.get("openclaw_health", "")
    status_summary_output = status_output.split("Gateway logs (tail, summarized):", 1)[0]
    token_check_output = "\n".join([status_summary_output, health_output]).lower()
    has_assets_issue = detect_ui_assets_issue(status_output) or detect_ui_assets_issue(health_output)
    if has_assets_issue:
        reasons.append("official status check reported missing Control UI assets")
    if "reason=token_missing" in token_check_output or "gateway token missing" in token_check_output:
        reasons.append("gateway token missing")
    elif "token mismatch" in token_check_output:
        reasons.append("gateway token mismatch")
    if any(step.command_result.exit_code != 0 for step in steps):
        reasons.append("basic binaries or versions check failed")
    for step in steps:
        if step.command_result.ssh_issue:
            reasons.append(step.command_result.ssh_issue)

    advice: list[str] = []
    if "gateway token mismatch" in reasons:
        advice.append("重新打开 localhost WebUI 以刷新当前 token")
        advice.append("如果仍然失败，重新生成 gateway token 后再打开 WebUI")
    elif "gateway token missing" in reasons:
        advice.append("先生成或配置 gateway token，再重新打开 localhost WebUI")

    if not reasons:
        status = ActionStatus.SUCCESS
        verdict = "Healthy"
        message = "Healthy"
    elif has_assets_issue or any("failed" in r for r in reasons):
        status = ActionStatus.FAILED
        verdict = "Failed"
        message = "Failed"
    else:
        status = ActionStatus.WARNING
        verdict = "Warning"
        if "gateway token mismatch" in reasons:
            message = "WebUI token 不匹配，请重新打开 localhost WebUI"
        elif "gateway token missing" in reasons:
            message = "缺少 gateway token"
        else:
            message = "Warning"

    summary = {
        "verdict": verdict,
        "reasons": reasons,
        "advice": advice,
        "details": outputs,
    }
    finished_at = now_iso()
    return ActionResult(
        action_name="验证 OpenClaw",
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=sum(step.command_result.duration_seconds for step in steps if step.command_result),
        steps=steps,
        summary=summary,
        message=message,
    )


def _check_openclaw_version_status(
    runner: SSHRunner,
    logger: ActionLogger,
    config: AppConfig,
) -> tuple[list[StepResult], str | None, str | None]:
    version_checks = [
        ("current_openclaw_version", "openclaw --version"),
        ("latest_openclaw_version", "npm view openclaw version"),
    ]
    steps: list[StepResult] = []
    outputs: dict[str, str] = {}
    for name, command in version_checks:
        step = _run_remote_step(runner, logger, name, command, config.command_timeout_seconds, True)
        steps.append(step)
        outputs[name] = normalize_version_text((step.command_result.stdout or step.command_result.stderr).strip())

    current_version = outputs["current_openclaw_version"] if steps[0].command_result.exit_code == 0 else None
    latest_version = outputs["latest_openclaw_version"] if steps[1].command_result.exit_code == 0 else None
    return steps, current_version, latest_version


def _openclaw_process_query_command() -> str:
    return "pgrep -af 'openclaw gateway' || true"


def _openclaw_process_running(step: StepResult) -> bool:
    if step.command_result is None:
        return False
    output = f"{step.command_result.stdout}\n{step.command_result.stderr}".strip()
    return bool(output)


def _upgrade_failure_requires_safe_stop(summary: dict) -> bool:
    return bool(summary.get("upgrade_ssh_issue") or summary.get("upgrade_timed_out"))


def _check_openclaw_install_state(
    runner: SSHRunner,
    logger: ActionLogger,
    config: AppConfig,
) -> tuple[list[StepResult], bool, bool]:
    npm_root_step = _run_remote_step(runner, logger, "npm_global_root", "npm root -g", config.command_timeout_seconds, True)
    npm_global_root = (npm_root_step.command_result.stdout or npm_root_step.command_result.stderr).strip() or config.npm_global_root
    checks = [
        (
            "global_openclaw_exists",
            f"if [ -d '{npm_global_root}/openclaw' ]; then echo exists; else echo missing; fi",
        ),
        (
            "global_openclaw_residue",
            f"find '{npm_global_root}' -maxdepth 1 -name '.openclaw-*' -print",
        ),
    ]
    steps: list[StepResult] = [npm_root_step]
    outputs: dict[str, str] = {}
    for name, command in checks:
        step = _run_remote_step(runner, logger, name, command, config.command_timeout_seconds, True)
        steps.append(step)
        outputs[name] = (step.command_result.stdout or step.command_result.stderr).strip()
    install_exists = outputs["global_openclaw_exists"] == "exists"
    has_residue = bool(outputs["global_openclaw_residue"])
    return steps, install_exists, has_residue


def _tunnel_state_path(config: AppConfig) -> Path:
    return config.logs_dir / f".localhost_tunnel_{config.selected_profile}.json"


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _run_secret_remote_command(
    runner: SSHRunner,
    logger: ActionLogger,
    name: str,
    command: str,
    timeout: int,
) -> CommandResult:
    logger.write(f"==> {name}")
    result = runner.run(command, timeout_seconds=timeout)
    logger.write(f"[{name}] exit_code={result.exit_code} timed_out={result.timed_out}")
    logger.write(f"command: {result.command}")
    logger.write(f"full_command: {' '.join(result.full_command)}")
    if result.ssh_issue:
        logger.write(f"ssh_issue: {result.ssh_issue}")
    return result


def _read_tunnel_state(config: AppConfig) -> dict | None:
    path = _tunnel_state_path(config)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        path.unlink(missing_ok=True)
        return None
    pid = payload.get("pid")
    if not isinstance(pid, int) or not _process_alive(pid):
        path.unlink(missing_ok=True)
        return None
    return payload


def _write_tunnel_state(config: AppConfig, payload: dict) -> None:
    path = _tunnel_state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_tunnel_state(config: AppConfig) -> None:
    _tunnel_state_path(config).unlink(missing_ok=True)


def get_localhost_access_url(config: AppConfig) -> str | None:
    state = _read_tunnel_state(config)
    if state is None:
        return None
    return str(state.get("localhost_url") or "")


def _get_or_create_gateway_token(
    runner: SSHRunner,
    logger: ActionLogger,
    config: AppConfig,
) -> tuple[str | None, bool, list[StepResult]]:
    steps: list[StepResult] = []
    fetch_command = "openclaw config get gateway.auth.token 2>/dev/null || true"
    fetch_result = _run_secret_remote_command(
        runner,
        logger,
        "gateway_token_status",
        fetch_command,
        config.command_timeout_seconds,
    )
    token = (fetch_result.stdout or "").strip()
    steps.append(
        StepResult(
            name="gateway_token_status",
            status=ActionStatus.SUCCESS if token else ActionStatus.WARNING,
            command_result=fetch_result,
        )
    )
    if token:
        return token, False, steps

    generate_result = _run_secret_remote_command(
        runner,
        logger,
        "generate_gateway_token",
        "openclaw doctor --generate-gateway-token",
        max(config.command_timeout_seconds, 300),
    )
    steps.append(
        StepResult(
            name="generate_gateway_token",
            status=_step_status(generate_result),
            command_result=generate_result,
            message=generate_result.ssh_issue or "",
        )
    )
    if generate_result.exit_code != 0:
        return None, False, steps

    refetch_result = _run_secret_remote_command(
        runner,
        logger,
        "gateway_token_status_after_generate",
        fetch_command,
        config.command_timeout_seconds,
    )
    token = (refetch_result.stdout or "").strip()
    steps.append(
        StepResult(
            name="gateway_token_status_after_generate",
            status=ActionStatus.SUCCESS if token else ActionStatus.FAILED,
            command_result=refetch_result,
        )
    )
    return (token or None), True, steps


def _can_bind_local_port(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _wait_for_local_port(port: int, timeout_seconds: float = 4.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


def _local_command_result(command: list[str], *, exit_code: int, duration_seconds: float, stderr: str = ""):
    return CommandResult(
        command=" ".join(command),
        full_command=command,
        exit_code=exit_code,
        stdout="",
        stderr=stderr,
        duration_seconds=duration_seconds,
    )


def self_repair_openclaw(config: AppConfig, ui_callback=None) -> ActionResult:
    def worker(runner: SSHRunner, logger: ActionLogger, config: AppConfig, started_at: str):
        repair_step = _run_remote_step(
            runner,
            logger,
            "doctor_repair",
            "openclaw doctor --repair",
            max(config.command_timeout_seconds, 600),
        )
        steps = [repair_step]
        if repair_step.status == ActionStatus.FAILED:
            finished_at = now_iso()
            return ActionResult(
                action_name="OpenClaw 自我修复",
                status=ActionStatus.FAILED,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=sum(step.command_result.duration_seconds for step in steps if step.command_result),
                steps=steps,
                summary={"repair_exit_code": repair_step.command_result.exit_code},
                message="OpenClaw 自我修复失败",
            )

        verification = _verify_openclaw_with_runner(runner, logger, config, started_at)
        steps.extend(verification.steps)
        details = verification.summary.get("details", {})
        finished_at = verification.finished_at
        return ActionResult(
            action_name="OpenClaw 自我修复",
            status=verification.status,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=sum(step.command_result.duration_seconds for step in steps if step.command_result),
            steps=steps,
            summary={
                "repair_exit_code": repair_step.command_result.exit_code,
                "verification": verification.summary,
                "openclaw_version": details.get("openclaw_version"),
            },
            message="OpenClaw 自我修复完成" if verification.status == ActionStatus.SUCCESS else verification.message,
        )

    return run_action("OpenClaw 自我修复", config, ui_callback, worker)


def start_localhost_access(config: AppConfig, ui_callback=None) -> ActionResult:
    def worker(runner: SSHRunner, logger: ActionLogger, config: AppConfig, started_at: str):
        return _start_localhost_access_with_runner(runner, logger, config, started_at)

    return run_action("开启 localhost 访问", config, ui_callback, worker)


def _start_localhost_access_with_runner(
    runner: SSHRunner,
    logger: ActionLogger,
    config: AppConfig,
    started_at: str,
) -> ActionResult:
        existing = _read_tunnel_state(config)
        localhost_url = f"http://127.0.0.1:{config.local_forward_port}"
        if existing is not None:
            finished_at = now_iso()
            return ActionResult(
                action_name="开启 localhost 访问",
                status=ActionStatus.SUCCESS,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=0.0,
                steps=[],
                summary={
                    "already_running": True,
                    "localhost_url": existing.get("localhost_url", localhost_url),
                    "pid": existing.get("pid"),
                },
                message=f"localhost 访问已就绪（{existing.get('localhost_url', localhost_url)}）",
            )

        if not _can_bind_local_port(config.local_forward_port):
            if _wait_for_local_port(config.local_forward_port, timeout_seconds=0.5):
                _write_tunnel_state(
                    config,
                    {
                        "pid": -1,
                        "profile_name": config.selected_profile,
                        "localhost_url": localhost_url,
                        "local_forward_port": config.local_forward_port,
                        "remote_gateway_port": config.gateway_web_port,
                    },
                )
                finished_at = now_iso()
                return ActionResult(
                    action_name="开启 localhost 访问",
                    status=ActionStatus.SUCCESS,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_seconds=0.0,
                    steps=[],
                    summary={
                        "already_running": True,
                        "reused_existing_listener": True,
                        "localhost_url": localhost_url,
                        "local_forward_port": config.local_forward_port,
                    },
                    message=f"localhost 访问已就绪（{localhost_url}）",
                )
            finished_at = now_iso()
            return ActionResult(
                action_name="开启 localhost 访问",
                status=ActionStatus.FAILED,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=0.0,
                steps=[],
                summary={
                    "localhost_url": localhost_url,
                    "local_forward_port": config.local_forward_port,
                    "error": "local port is already in use",
                },
                message=f"本地端口已被占用：{config.local_forward_port}",
            )

        tunnel_command = runner.build_tunnel_command(
            local_port=config.local_forward_port,
            remote_port=config.gateway_web_port,
        )
        started = time.monotonic()
        logger.write("==> start_localhost_access")
        with logger.log_path.open("a", encoding="utf-8") as handle:
            process = subprocess.Popen(
                tunnel_command,
                stdout=handle,
                stderr=handle,
                stdin=subprocess.DEVNULL,
                text=True,
                start_new_session=True,
            )
        time.sleep(0.5)
        if process.poll() is not None or not _wait_for_local_port(config.local_forward_port):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
            duration = time.monotonic() - started
            step = StepResult(
                name="start_localhost_access",
                status=ActionStatus.FAILED,
                command_result=_local_command_result(
                    tunnel_command,
                    exit_code=process.poll() or 1,
                    duration_seconds=duration,
                    stderr="localhost tunnel did not become ready",
                ),
                message="localhost tunnel failed to start",
            )
            logger.write_command_result("start_localhost_access", step.command_result)
            finished_at = now_iso()
            return ActionResult(
                action_name="开启 localhost 访问",
                status=ActionStatus.FAILED,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=duration,
                steps=[step],
                summary={
                    "localhost_url": localhost_url,
                    "local_forward_port": config.local_forward_port,
                    "remote_gateway_port": config.gateway_web_port,
                    "error": "localhost tunnel failed to become ready",
                },
                message="localhost 访问启动失败",
            )

        duration = time.monotonic() - started
        step = StepResult(
            name="start_localhost_access",
            status=ActionStatus.SUCCESS,
            command_result=_local_command_result(
                tunnel_command,
                exit_code=0,
                duration_seconds=duration,
            ),
        )
        logger.write_command_result("start_localhost_access", step.command_result)
        _write_tunnel_state(
            config,
            {
                "pid": process.pid,
                "profile_name": config.selected_profile,
                "localhost_url": localhost_url,
                "local_forward_port": config.local_forward_port,
                "remote_gateway_port": config.gateway_web_port,
            },
        )
        finished_at = now_iso()
        return ActionResult(
            action_name="开启 localhost 访问",
            status=ActionStatus.SUCCESS,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration,
            steps=[step],
            summary={
                "localhost_url": localhost_url,
                "local_forward_port": config.local_forward_port,
                "remote_gateway_port": config.gateway_web_port,
                "pid": process.pid,
                },
                message=f"localhost 访问已就绪（{localhost_url}）",
            )

def prepare_localhost_webui(config: AppConfig, ui_callback=None) -> ActionResult:
    def worker(runner: SSHRunner, logger: ActionLogger, config: AppConfig, started_at: str):
        localhost_url = get_localhost_access_url(config)
        if not localhost_url:
            finished_at = now_iso()
            return ActionResult(
                action_name="打开 localhost WebUI",
                status=ActionStatus.FAILED,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=0.0,
                summary={"localhost_url": "", "error": "localhost access is not active"},
                message="请先开启 localhost 访问",
            )

        token, generated, steps = _get_or_create_gateway_token(runner, logger, config)
        finished_at = now_iso()
        duration = sum(step.command_result.duration_seconds for step in steps if step.command_result)
        if not token:
            return ActionResult(
                action_name="打开 localhost WebUI",
                status=ActionStatus.FAILED,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=duration,
                steps=steps,
                summary={"localhost_url": localhost_url, "token_ready": False},
                message="未能获取 gateway token",
            )

        launch_url = f"{localhost_url}?openclaw_ui_refresh={int(time.time())}#token={token}"
        return ActionResult(
            action_name="打开 localhost WebUI",
            status=ActionStatus.SUCCESS,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration,
            steps=steps,
            summary={
                "localhost_url": localhost_url,
                "token_ready": True,
                "token_generated": generated,
            },
            message="localhost WebUI 已准备好",
            launch_url=launch_url,
        )

    return run_action("打开 localhost WebUI", config, ui_callback, worker)


def open_localhost_webui(config: AppConfig, ui_callback=None) -> ActionResult:
    def worker(runner: SSHRunner, logger: ActionLogger, config: AppConfig, started_at: str):
        sequence = []
        localhost_url = get_localhost_access_url(config)
        access_started = False
        if not localhost_url:
            access_result = _start_localhost_access_with_runner(runner, logger, config, started_at)
            sequence.extend(access_result.steps)
            if access_result.status != ActionStatus.SUCCESS:
                return ActionResult(
                    action_name="打开 localhost WebUI",
                    status=ActionStatus.FAILED,
                    started_at=started_at,
                    finished_at=access_result.finished_at,
                    duration_seconds=sum(step.command_result.duration_seconds for step in sequence if step.command_result),
                    steps=sequence,
                    summary={
                        "localhost_url": "",
                        "access_started": False,
                        "nested_summary": access_result.summary,
                    },
                    message=access_result.message,
                )
            localhost_url = str(access_result.summary.get("localhost_url") or "").strip()
            access_started = True

        token, generated, steps = _get_or_create_gateway_token(runner, logger, config)
        sequence.extend(steps)
        finished_at = now_iso()
        duration = sum(step.command_result.duration_seconds for step in sequence if step.command_result)
        if not token:
            return ActionResult(
                action_name="打开 localhost WebUI",
                status=ActionStatus.FAILED,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=duration,
                steps=sequence,
                summary={
                    "localhost_url": localhost_url or "",
                    "token_ready": False,
                    "access_started": access_started,
                },
                message="未能获取 gateway token",
            )

        launch_url = f"{localhost_url}?openclaw_ui_refresh={int(time.time())}#token={token}"
        return ActionResult(
            action_name="打开 localhost WebUI",
            status=ActionStatus.SUCCESS,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration,
            steps=sequence,
            summary={
                "localhost_url": localhost_url,
                "token_ready": True,
                "token_generated": generated,
                "access_started": access_started,
            },
            message="localhost WebUI 已准备好",
            launch_url=launch_url,
        )

    return run_action("打开 localhost WebUI", config, ui_callback, worker)


def stop_localhost_access(config: AppConfig, ui_callback=None) -> ActionResult:
    def worker(runner: SSHRunner, logger: ActionLogger, config: AppConfig, started_at: str):
        existing = _read_tunnel_state(config)
        if existing is None:
            _clear_tunnel_state(config)
            finished_at = now_iso()
            return ActionResult(
                action_name="关闭 localhost 访问",
                status=ActionStatus.SUCCESS,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=0.0,
                steps=[],
                summary={"localhost_url": "", "already_stopped": True},
                message="localhost 访问未开启",
            )

        pid = int(existing["pid"])
        if pid <= 0:
            _clear_tunnel_state(config)
            finished_at = now_iso()
            return ActionResult(
                action_name="关闭 localhost 访问",
                status=ActionStatus.SUCCESS,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=0.0,
                steps=[],
                summary={"localhost_url": "", "stopped_pid": pid},
                message="localhost 访问已关闭",
            )
        started = time.monotonic()
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and _process_alive(pid):
            time.sleep(0.1)
        if _process_alive(pid):
            os.kill(pid, signal.SIGKILL)
        duration = time.monotonic() - started
        _clear_tunnel_state(config)
        step = StepResult(
            name="stop_localhost_access",
            status=ActionStatus.SUCCESS,
            command_result=_local_command_result(["kill", str(pid)], exit_code=0, duration_seconds=duration),
        )
        logger.write_command_result("stop_localhost_access", step.command_result)
        finished_at = now_iso()
        return ActionResult(
            action_name="关闭 localhost 访问",
            status=ActionStatus.SUCCESS,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration,
            steps=[step],
            summary={"localhost_url": "", "stopped_pid": pid},
            message="localhost 访问已关闭",
        )

    return run_action("关闭 localhost 访问", config, ui_callback, worker)


def start_openclaw(config: AppConfig, ui_callback=None) -> ActionResult:
    def worker(runner: SSHRunner, logger: ActionLogger, config: AppConfig, started_at: str):
        steps = [
            _run_remote_step(
                runner,
                logger,
                "pre_start_status",
                _openclaw_process_query_command(),
                config.command_timeout_seconds,
                True,
            )
        ]
        if _openclaw_process_running(steps[0]):
            finished_at = now_iso()
            return ActionResult(
                action_name="启动 OpenClaw",
                status=ActionStatus.SUCCESS,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=sum(step.command_result.duration_seconds for step in steps if step.command_result),
                steps=steps,
                summary={
                    "already_running": True,
                    "processes": steps[0].command_result.stdout.strip(),
                },
                message="OpenClaw 已在运行",
            )

        start_command = "openclaw gateway start"
        steps.append(
            _run_remote_step(
                runner,
                logger,
                "start_openclaw",
                start_command,
                config.command_timeout_seconds,
            )
        )
        steps.append(
            _run_remote_step(
                runner,
                logger,
                "post_start_status",
                _openclaw_process_query_command(),
                config.command_timeout_seconds,
                True,
            )
        )
        running = _openclaw_process_running(steps[-1])
        status = ActionStatus.SUCCESS if running else ActionStatus.FAILED
        start_output = steps[1].command_result.stdout.strip()
        finished_at = now_iso()
        return ActionResult(
            action_name="启动 OpenClaw",
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=sum(step.command_result.duration_seconds for step in steps if step.command_result),
            steps=steps,
            summary={
                "started": running,
                "start_output": start_output,
                "processes": steps[-1].command_result.stdout.strip(),
            },
            message="OpenClaw 启动成功" if running else "OpenClaw 启动失败",
        )

    return run_action("启动 OpenClaw", config, ui_callback, worker)


def stop_openclaw(config: AppConfig, ui_callback=None) -> ActionResult:
    def worker(runner: SSHRunner, logger: ActionLogger, config: AppConfig, started_at: str):
        steps = [
            _run_remote_step(
                runner,
                logger,
                "pre_stop_status",
                _openclaw_process_query_command(),
                config.command_timeout_seconds,
                True,
            )
        ]
        if not _openclaw_process_running(steps[0]):
            finished_at = now_iso()
            return ActionResult(
                action_name="停止 OpenClaw",
                status=ActionStatus.SUCCESS,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=sum(step.command_result.duration_seconds for step in steps if step.command_result),
                steps=steps,
                summary={
                    "already_stopped": True,
                    "processes": "",
                },
                message="OpenClaw 未在运行",
            )

        stop_command = "openclaw gateway stop"
        steps.append(
            _run_remote_step(
                runner,
                logger,
                "stop_openclaw",
                stop_command,
                config.command_timeout_seconds,
            )
        )
        steps.append(
            _run_remote_step(
                runner,
                logger,
                "post_stop_status",
                _openclaw_process_query_command(),
                config.command_timeout_seconds,
                True,
            )
        )
        stopped = not _openclaw_process_running(steps[-1])
        status = ActionStatus.SUCCESS if stopped else ActionStatus.FAILED
        finished_at = now_iso()
        return ActionResult(
            action_name="停止 OpenClaw",
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=sum(step.command_result.duration_seconds for step in steps if step.command_result),
            steps=steps,
            summary={
                "stopped": stopped,
                "remaining_processes": steps[-1].command_result.stdout.strip(),
            },
            message="OpenClaw 已停止" if stopped else "OpenClaw 停止失败",
        )

    return run_action("停止 OpenClaw", config, ui_callback, worker)


def restart_openclaw(config: AppConfig, ui_callback=None) -> ActionResult:
    def worker(runner: SSHRunner, logger: ActionLogger, config: AppConfig, started_at: str):
        steps = []
        steps.append(
            _run_remote_step(
                runner,
                logger,
                "pre_restart_status",
                _openclaw_process_query_command(),
                config.command_timeout_seconds,
                True,
            )
        )
        was_running = _openclaw_process_running(steps[0])
        if was_running:
            stop_command = (
                "pids=$(pgrep -f 'openclaw gateway' || true); "
                'if [ -n "$pids" ]; then '
                'echo "$pids" | xargs kill; '
                "sleep 2; "
                "remaining=$(pgrep -f 'openclaw gateway' || true); "
                'if [ -n "$remaining" ]; then echo "$remaining" | xargs kill -9; fi; '
                "fi"
            )
            steps.append(
                _run_remote_step(
                    runner,
                    logger,
                    "stop_before_restart",
                    stop_command,
                    config.command_timeout_seconds,
                )
            )
            steps.append(
                _run_remote_step(
                    runner,
                    logger,
                    "after_stop_status",
                    _openclaw_process_query_command(),
                    config.command_timeout_seconds,
                    True,
                )
            )

        start_command = (
            'log_file="${TMPDIR:-/tmp}/openclaw-gateway.log"; '
            'nohup openclaw gateway >>"$log_file" 2>&1 </dev/null & pid=$!; '
            'sleep 2; '
            'if kill -0 "$pid" 2>/dev/null; then '
            'echo "__STARTED__:$pid:$log_file"; '
            "else "
            'echo "__START_FAILED__:$pid:$log_file"; '
            'wait "$pid" 2>/dev/null || true; '
            "fi"
        )
        steps.append(
            _run_remote_step(
                runner,
                logger,
                "start_after_restart",
                start_command,
                config.command_timeout_seconds,
            )
        )
        steps.append(
            _run_remote_step(
                runner,
                logger,
                "post_restart_status",
                _openclaw_process_query_command(),
                config.command_timeout_seconds,
                True,
            )
        )
        restarted = _openclaw_process_running(steps[-1])
        status = ActionStatus.SUCCESS if restarted else ActionStatus.FAILED
        finished_at = now_iso()
        return ActionResult(
            action_name="重启 OpenClaw",
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=sum(step.command_result.duration_seconds for step in steps if step.command_result),
            steps=steps,
            summary={
                "was_running": was_running,
                "restarted": restarted,
                "processes": steps[-1].command_result.stdout.strip(),
            },
            message="OpenClaw 重启成功" if restarted else "OpenClaw 重启失败",
        )

    return run_action("重启 OpenClaw", config, ui_callback, worker)


def check_latest_release(config: AppConfig, ui_callback=None) -> ActionResult:
    def worker(runner: SSHRunner, logger: ActionLogger, config: AppConfig, started_at: str):
        steps, current_version, latest_version = _check_openclaw_version_status(runner, logger, config)
        finished_at = now_iso()
        duration = sum(step.command_result.duration_seconds for step in steps if step.command_result)
        if current_version and latest_version:
            if current_version == latest_version:
                status = ActionStatus.SUCCESS
                message = f"当前已是最新版（{current_version}）"
            else:
                status = ActionStatus.WARNING
                message = f"发现新版本：{latest_version}，当前版本：{current_version}"
        else:
            status = ActionStatus.FAILED
            message = "最新版检查失败"

        return ActionResult(
            action_name="最新版检查",
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration,
            steps=steps,
            summary={
                "current_version": current_version,
                "latest_version": latest_version,
                "up_to_date": bool(current_version and latest_version and current_version == latest_version),
            },
            message=message,
        )

    return run_action("最新版检查", config, ui_callback, worker)


def check_connection(config: AppConfig, ui_callback=None) -> ActionResult:
    def worker(runner: SSHRunner, logger: ActionLogger, config: AppConfig, started_at: str):
        step = _run_remote_step(runner, logger, "hostname", "hostname", config.command_timeout_seconds)
        host = step.command_result.stdout.strip() if step.command_result else ""
        if step.status == ActionStatus.FAILED:
            finished_at = now_iso()
            duration = step.command_result.duration_seconds if step.command_result else 0.0
            summary = {
                "connected": False,
                "target_host": host,
                "duration_seconds": round(duration, 2),
                "timed_out": step.command_result.timed_out if step.command_result else False,
                "stderr": step.command_result.stderr.strip() if step.command_result else "",
                "ssh_issue": step.command_result.ssh_issue if step.command_result else None,
            }
            return ActionResult(
                action_name="连接检查",
                status=ActionStatus.FAILED,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=duration,
                steps=[step],
                summary=summary,
                message="connection failed",
            )

        diagnose_result = diagnose_environment(config, ui_callback=logger.ui_callback)
        verify_result = verify_openclaw(config, ui_callback=logger.ui_callback)
        steps = [step, *diagnose_result.steps, *verify_result.steps]
        status = ActionStatus.SUCCESS
        if diagnose_result.status == ActionStatus.FAILED or verify_result.status == ActionStatus.FAILED:
            status = ActionStatus.FAILED
        elif diagnose_result.status == ActionStatus.WARNING or verify_result.status == ActionStatus.WARNING:
            status = ActionStatus.WARNING
        finished_at = verify_result.finished_at or diagnose_result.finished_at or now_iso()
        duration = sum(current.command_result.duration_seconds for current in steps if current.command_result)
        summary = {
            "connected": True,
            "target_host": host,
            "duration_seconds": round(duration, 2),
            "ssh_issue": None,
            "diagnose": diagnose_result.summary,
            "verify": verify_result.summary,
        }
        return ActionResult(
            action_name="连接检查",
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration,
            steps=steps,
            summary=summary,
            message="连接检查完成" if status == ActionStatus.SUCCESS else (verify_result.message or diagnose_result.message),
        )

    return run_action("连接检查", config, ui_callback, worker)


def diagnose_environment(config: AppConfig, ui_callback=None) -> ActionResult:
    commands = [
        ("remote_path", "printf '%s\n' \"$PATH\""),
        ("node_version", "node -v"),
        ("npm_version", "npm -v"),
        ("npm_global_root", "npm root -g"),
        ("openclaw_path", "which openclaw"),
        ("openclaw_version", "openclaw --version"),
        ("latest_openclaw_version", "npm view openclaw version"),
        (
            "gateway_token_status",
            "if [ -n \"$(openclaw config get gateway.auth.token 2>/dev/null)\" ]; then echo configured; else echo missing; fi",
        ),
        ("npm_home_exists", "if [ -d \"$HOME/.npm\" ]; then echo exists; else echo missing; fi"),
        ("npm_home_stat", "if [ -e \"$HOME/.npm\" ]; then ls -ldO \"$HOME/.npm\"; else echo missing; fi"),
    ]

    def worker(runner: SSHRunner, logger: ActionLogger, config: AppConfig, started_at: str):
        steps: list[StepResult] = []
        summary: dict[str, str | bool | list[str]] = {}
        for name, command in commands:
            step = _run_remote_step(runner, logger, name, command, config.command_timeout_seconds, True)
            steps.append(step)
            result = step.command_result
            output = (result.stdout or result.stderr).strip()
            summary[name] = output
        npm_global_root = str(summary.get("npm_global_root") or config.npm_global_root).strip() or config.npm_global_root
        for name, command in [
            ("global_openclaw_exists", f"if [ -d '{npm_global_root}/openclaw' ]; then echo exists; else echo missing; fi"),
            ("global_openclaw_residue", f"find '{npm_global_root}' -maxdepth 1 -name '.openclaw-*' -print"),
        ]:
            step = _run_remote_step(runner, logger, name, command, config.command_timeout_seconds, True)
            steps.append(step)
            result = step.command_result
            summary[name] = (result.stdout or result.stderr).strip()
        current_version = normalize_version_text(str(summary.get("openclaw_version", "")))
        latest_version = normalize_version_text(str(summary.get("latest_openclaw_version", "")))
        up_to_date = bool(current_version and latest_version and current_version == latest_version)
        summary["current_version_normalized"] = current_version
        summary["latest_version_normalized"] = latest_version
        summary["up_to_date"] = up_to_date
        summary["gateway_token_configured"] = summary.get("gateway_token_status") == "configured"
        failures = [step for step in steps if step.status == ActionStatus.FAILED]
        needs_upgrade = bool(current_version and latest_version and current_version != latest_version)
        missing_token = summary.get("gateway_token_status") == "missing"
        status = ActionStatus.SUCCESS if not failures and not needs_upgrade and not missing_token else ActionStatus.WARNING
        if needs_upgrade:
            message = "需升级"
        elif missing_token:
            message = "缺少 gateway token"
        else:
            message = "diagnostic completed"
        finished_at = now_iso()
        return ActionResult(
            action_name="环境诊断",
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=sum(step.command_result.duration_seconds for step in steps if step.command_result),
            steps=steps,
            summary=summary,
            message=message,
        )

    return run_action("环境诊断", config, ui_callback, worker)


def fix_npm_environment(config: AppConfig, ui_callback=None) -> ActionResult:
    commands = [
        (
            "fix_npm_ownership",
            f"if [ -d \"$HOME/.npm\" ]; then chown -R {config.remote_user}:staff \"$HOME/.npm\"; else echo '~/.npm missing'; fi",
        ),
        ("remove_npm_cache", "rm -rf \"$HOME/.npm/_cacache\""),
        ("npm_cache_verify", "npm cache verify"),
    ]

    def worker(runner: SSHRunner, logger: ActionLogger, config: AppConfig, started_at: str):
        steps = []
        failed_step = None
        for name, command in commands:
            step = _run_remote_step(runner, logger, name, command, config.command_timeout_seconds)
            steps.append(step)
            if step.status == ActionStatus.FAILED:
                failed_step = name
                break
        status = ActionStatus.SUCCESS if failed_step is None else ActionStatus.FAILED
        summary = {"failed_step": failed_step, "remote_user": config.remote_user}
        finished_at = now_iso()
        return ActionResult(
            action_name="修复 npm 环境",
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=sum(step.command_result.duration_seconds for step in steps if step.command_result),
            steps=steps,
            summary=summary,
            message="npm repaired" if status == ActionStatus.SUCCESS else f"failed at {failed_step}",
        )

    return run_action("修复 npm 环境", config, ui_callback, worker)


def cleanup_openclaw_residue(config: AppConfig, ui_callback=None) -> ActionResult:
    def worker(runner: SSHRunner, logger: ActionLogger, config: AppConfig, started_at: str):
        npm_root_step = _run_remote_step(runner, logger, "npm_global_root", "npm root -g", config.command_timeout_seconds, True)
        npm_global_root = (npm_root_step.command_result.stdout or npm_root_step.command_result.stderr).strip() or config.npm_global_root
        install_dir = f"{npm_global_root}/openclaw"
        residue_glob = f"{npm_global_root}/.openclaw-*"
        precheck_command = (
            f"echo 'install_dir:'; ls -ld '{install_dir}' 2>/dev/null || true; "
            f"echo 'residue:'; find '{npm_global_root}' -maxdepth 1 -name '.openclaw-*' -print"
        )
        cleanup_command = (
            f"rm -rf '{install_dir}'; "
            f"find '{npm_global_root}' -maxdepth 1 -name '.openclaw-*' -exec rm -rf {{}} +"
        )
        steps = [
            npm_root_step,
            _run_remote_step(runner, logger, "pre_cleanup_status", precheck_command, config.command_timeout_seconds, True),
            _run_remote_step(runner, logger, "cleanup", cleanup_command, config.command_timeout_seconds),
            _run_remote_step(runner, logger, "post_cleanup_status", precheck_command, config.command_timeout_seconds, True),
        ]
        status = ActionStatus.SUCCESS if all(step.status != ActionStatus.FAILED for step in steps) else ActionStatus.FAILED
        summary = {
            "npm_global_root": npm_global_root,
            "install_dir": install_dir,
            "residue_glob": residue_glob,
        }
        finished_at = now_iso()
        return ActionResult(
            action_name="清理 OpenClaw 残留",
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=sum(step.command_result.duration_seconds for step in steps if step.command_result),
            steps=steps,
            summary=summary,
            message="cleanup completed",
        )

    return run_action("清理 OpenClaw 残留", config, ui_callback, worker)


def verify_openclaw(config: AppConfig, ui_callback=None) -> ActionResult:
    def worker(runner: SSHRunner, logger: ActionLogger, config: AppConfig, started_at: str):
        return _verify_openclaw_with_runner(runner, logger, config, started_at)

    return run_action("验证 OpenClaw", config, ui_callback, worker)


def upgrade_openclaw(config: AppConfig, ui_callback=None) -> ActionResult:
    def worker(runner: SSHRunner, logger: ActionLogger, config: AppConfig, started_at: str):
        version_steps, current_version, latest_version = _check_openclaw_version_status(runner, logger, config)
        if current_version and latest_version and current_version == latest_version:
            logger.write("阶段: 跳过升级，当前已是最新版本")
            finished_at = now_iso()
            duration = sum(step.command_result.duration_seconds for step in version_steps if step.command_result)
            return ActionResult(
                action_name="升级 OpenClaw",
                status=ActionStatus.SUCCESS,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=duration,
                steps=version_steps,
                summary={
                    "skipped_upgrade": True,
                    "current_version": current_version,
                    "latest_version": latest_version,
                },
                message=f"当前已是最新版，无需升级（{current_version}）",
            )

        if current_version:
            logger.write(f"阶段: 当前版本 {current_version}，准备升级到 {latest_version or 'latest'}")
        else:
            logger.write("阶段: 当前未检测到 OpenClaw，可执行文件缺失或已被清理，准备安装最新版")
        logger.write("阶段: 开始升级")
        steps = version_steps + [
            _run_remote_step(
                runner,
                logger,
                "upgrade_openclaw",
                "npm install -g openclaw@latest",
                max(config.command_timeout_seconds, 1800),
            )
        ]
        upgrade_step = steps[len(version_steps)]
        if upgrade_step.status == ActionStatus.SUCCESS:
            logger.write("阶段: 进入验证")
            verification = _verify_openclaw_with_runner(runner, logger, config, started_at)
            steps.extend(verification.steps)
            status = verification.status
            summary = {
                "previous_version": current_version,
                "latest_version_before_upgrade": latest_version,
                "upgrade_exit_code": upgrade_step.command_result.exit_code,
                "upgrade_timed_out": upgrade_step.command_result.timed_out,
                "upgrade_ssh_issue": upgrade_step.command_result.ssh_issue,
                "verification": verification.summary,
            }
            message = verification.message
            finished_at = verification.finished_at
            duration = sum(step.command_result.duration_seconds for step in steps if step.command_result)
        else:
            status = ActionStatus.FAILED
            summary = {
                "previous_version": current_version,
                "latest_version_before_upgrade": latest_version,
                "upgrade_exit_code": upgrade_step.command_result.exit_code,
                "upgrade_timed_out": upgrade_step.command_result.timed_out,
                "upgrade_ssh_issue": upgrade_step.command_result.ssh_issue,
                "stderr": upgrade_step.command_result.stderr.strip(),
            }
            if upgrade_step.command_result.ssh_issue:
                message = f"upgrade failed: {upgrade_step.command_result.ssh_issue}"
            elif upgrade_step.command_result.timed_out:
                message = "upgrade failed: install timed out"
            else:
                message = "upgrade failed"
            finished_at = now_iso()
            duration = sum(step.command_result.duration_seconds for step in steps if step.command_result)
        return ActionResult(
            action_name="升级 OpenClaw",
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration,
            steps=steps,
            summary=summary,
            message=message,
        )

    return run_action("升级 OpenClaw", config, ui_callback, worker)


def fallback_source_build(config: AppConfig, ui_callback=None) -> ActionResult:
    remote_workdir = shell_quote_remote(config.remote_workdir, allow_env_expansion=True)
    repo_url = shell_quote_remote(config.openclaw_repo_url)
    setup_command = (
        f"mkdir -p {remote_workdir}; "
        f"if [ ! -d {remote_workdir}/.git ]; then git clone {repo_url} {remote_workdir}; "
        f"else git -C {remote_workdir} fetch --all --tags && git -C {remote_workdir} pull --ff-only; fi"
    )
    build_commands = [
        ("prepare_repo", setup_command),
        ("pnpm_install", f"cd {remote_workdir} && pnpm install"),
        ("pnpm_ui_build", f"cd {remote_workdir} && pnpm ui:build"),
        ("pnpm_build", f"cd {remote_workdir} && pnpm build"),
    ]

    def worker(runner: SSHRunner, logger: ActionLogger, config: AppConfig, started_at: str):
        steps = []
        failed_step = None
        for name, command in build_commands:
            timeout = max(config.command_timeout_seconds, 900)
            step = _run_remote_step(runner, logger, name, command, timeout)
            steps.append(step)
            if step.status == ActionStatus.FAILED:
                failed_step = name
                break
        status = ActionStatus.SUCCESS if failed_step is None else ActionStatus.FAILED
        summary = {
            "repo_url": config.openclaw_repo_url,
            "remote_workdir": config.remote_workdir,
            "failed_step": failed_step,
        }
        finished_at = now_iso()
        return ActionResult(
            action_name="源码构建兜底",
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=sum(step.command_result.duration_seconds for step in steps if step.command_result),
            steps=steps,
            summary=summary,
            message="fallback build completed" if status == ActionStatus.SUCCESS else f"failed at {failed_step}",
        )

    return run_action("源码构建兜底", config, ui_callback, worker)


def repair_and_upgrade(config: AppConfig, ui_callback=None) -> ActionResult:
    def worker(runner: SSHRunner, logger: ActionLogger, config: AppConfig, started_at: str):
        sequence = []
        logger.write("阶段: 开始升级 OpenClaw")
        upgrade_result = upgrade_openclaw(config, ui_callback=logger.ui_callback)
        sequence.extend(upgrade_result.steps)
        if upgrade_result.status != ActionStatus.SUCCESS:
            finished_at = upgrade_result.finished_at
            return ActionResult(
                action_name="一键升级并启动",
                status=ActionStatus.FAILED,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=sum(step.command_result.duration_seconds for step in sequence if step.command_result),
                steps=sequence,
                summary={"failed_action": "upgrade_openclaw", "nested_summary": upgrade_result.summary},
                message=upgrade_result.message,
            )

        logger.write("阶段: 升级完成，开始启动 OpenClaw")
        start_result = start_openclaw(config, ui_callback=logger.ui_callback)
        sequence.extend(start_result.steps)
        finished_at = start_result.finished_at
        if start_result.status == ActionStatus.SUCCESS:
            summary = {
                "status": "complete",
                "strategy": "upgrade_then_start",
                "upgrade": upgrade_result.summary,
                "start": start_result.summary,
            }
            message = "升级并启动完成"
            status = ActionStatus.SUCCESS
        else:
            summary = {
                "failed_action": "start_openclaw",
                "upgrade": upgrade_result.summary,
                "nested_summary": start_result.summary,
            }
            message = start_result.message
            status = ActionStatus.FAILED
        return ActionResult(
            action_name="一键升级并启动",
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=sum(
                step.command_result.duration_seconds for step in sequence if step.command_result
            ),
            steps=sequence,
            summary=summary,
            message=message,
        )

    return run_action("一键升级并启动", config, ui_callback, worker)
