from __future__ import annotations

from dataclasses import dataclass
import os
import queue
import shlex
import subprocess
import threading
import time

from config import AppConfig
from models import CommandResult


@dataclass
class SSHRunner:
    config: AppConfig

    def _ssh_options(self) -> list[str]:
        options: list[str] = []
        if self.config.ssh_identities_only:
            options.extend(["-o", "IdentitiesOnly=yes"])
        if self.config.ssh_identity_file:
            options.extend(["-i", os.path.expanduser(self.config.ssh_identity_file)])
        if self.config.ssh_config_path:
            options.extend(["-F", os.path.expanduser(self.config.ssh_config_path)])
        return options

    def build_remote_command(self, remote_command: str) -> str:
        prefix = self.config.remote_path_prefix.strip()
        command = remote_command.strip()
        return f"{prefix} {command}"

    def build_ssh_command(self, remote_command: str) -> list[str]:
        wrapped = self.build_remote_command(remote_command)
        return ["ssh", *self._ssh_options(), self.config.remote_host, wrapped]

    def run(
        self,
        remote_command: str,
        timeout_seconds: int | None = None,
        stream_callback=None,
    ) -> CommandResult:
        ssh_command = self.build_ssh_command(remote_command)
        started = time.monotonic()
        process = subprocess.Popen(
            ssh_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=os.environ.copy(),
        )
        output_queue: queue.Queue[tuple[str, str]] = queue.Queue()

        def reader_thread(pipe, label: str) -> None:
            try:
                for line in iter(pipe.readline, ""):
                    output_queue.put((label, line))
            finally:
                pipe.close()

        threads = [
            threading.Thread(target=reader_thread, args=(process.stdout, "stdout"), daemon=True),
            threading.Thread(target=reader_thread, args=(process.stderr, "stderr"), daemon=True),
        ]
        for thread in threads:
            thread.start()

        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        timed_out = False
        timeout = timeout_seconds or self.config.command_timeout_seconds

        while True:
            try:
                label, chunk = output_queue.get(timeout=0.1)
                if label == "stdout":
                    stdout_parts.append(chunk)
                else:
                    stderr_parts.append(chunk)
                if stream_callback:
                    stream_callback(label, chunk)
            except queue.Empty:
                pass

            if process.poll() is not None and output_queue.empty():
                break

            if time.monotonic() - started > timeout:
                timed_out = True
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                break

        for thread in threads:
            thread.join(timeout=1)

        while not output_queue.empty():
            label, chunk = output_queue.get_nowait()
            if label == "stdout":
                stdout_parts.append(chunk)
            else:
                stderr_parts.append(chunk)
            if stream_callback:
                stream_callback(label, chunk)

        exit_code = process.poll()
        if exit_code is None:
            exit_code = -9 if timed_out else -1
        duration = time.monotonic() - started
        stdout_text = "".join(stdout_parts)
        stderr_text = "".join(stderr_parts)
        ssh_issue = detect_ssh_issue(exit_code, stderr_text, timed_out)
        return CommandResult(
            command=remote_command,
            full_command=ssh_command,
            exit_code=exit_code,
            stdout=stdout_text,
            stderr=stderr_text,
            duration_seconds=duration,
            timed_out=timed_out,
            ssh_issue=ssh_issue,
        )


def detect_ssh_issue(exit_code: int, stderr: str, timed_out: bool) -> str | None:
    lowered = stderr.lower()
    if timed_out:
        return "remote command timed out"
    if "permission denied" in lowered or "password" in lowered:
        return "ssh authentication failed or requires interactive password input"
    if "could not resolve hostname" in lowered:
        return "ssh host resolution failed"
    if "operation timed out" in lowered or "connection timed out" in lowered:
        return "ssh connection timed out"
    if "connection refused" in lowered:
        return "ssh connection refused"
    if exit_code == 255:
        return "ssh transport failed"
    return None


def quote_remote(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)
