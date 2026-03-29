from __future__ import annotations

from dataclasses import dataclass
import os
import queue
import shlex
import select
import socket
import socketserver
import subprocess
import threading
import time

import paramiko

from config import AppConfig
from models import CommandResult


_TUNNEL_REGISTRY: dict[str, "_ManagedTunnel"] = {}


class _TunnelTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, RequestHandlerClass, bind_and_activate=True):
        super().__init__(server_address, RequestHandlerClass, bind_and_activate)
        self.ssh_transport = None
        self.remote_host = "127.0.0.1"
        self.remote_port = 0


class _TunnelHandler(socketserver.BaseRequestHandler):
    def handle(self):
        server: _TunnelTCPServer = self.server
        channel = server.ssh_transport.open_channel(
            "direct-tcpip",
            (server.remote_host, server.remote_port),
            self.request.getpeername(),
        )
        try:
            self._bridge(self.request, channel)
        finally:
            channel.close()
            self.request.close()

    def _bridge(self, client_socket, channel):
        sockets = [client_socket, channel]
        while True:
            readable, _, _ = select.select(sockets, [], [], 0.5)
            if client_socket in readable:
                data = client_socket.recv(1024)
                if not data:
                    break
                channel.sendall(data)
            if channel in readable:
                data = channel.recv(1024)
                if not data:
                    break
                client_socket.sendall(data)


class _ManagedTunnel:
    def __init__(self, config: AppConfig, local_port: int, remote_port: int):
        self.config = config
        self.local_port = local_port
        self.remote_port = remote_port
        self.client: paramiko.SSHClient | None = None
        self.server: _TunnelTCPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.client = _connect_paramiko(self.config)
        transport = self.client.get_transport()
        if transport is None or not transport.is_active():
            raise RuntimeError("ssh transport failed")
        self.server = _TunnelTCPServer(("127.0.0.1", self.local_port), _TunnelHandler)
        self.server.ssh_transport = transport
        self.server.remote_host = "127.0.0.1"
        self.server.remote_port = self.remote_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)
        if self.client is not None:
            self.client.close()


def _connect_paramiko(config: AppConfig) -> paramiko.SSHClient:
    host, username = _split_remote_host(config)
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = {
        "hostname": host,
        "username": username,
        "timeout": min(config.command_timeout_seconds, 15),
        "banner_timeout": min(config.command_timeout_seconds, 15),
        "auth_timeout": min(config.command_timeout_seconds, 15),
        "look_for_keys": config.ssh_auth_method != "password",
        "allow_agent": config.ssh_auth_method != "password",
    }
    if config.ssh_auth_method == "password":
        kwargs["password"] = config.ssh_password
    elif config.ssh_identity_file:
        kwargs["key_filename"] = os.path.expanduser(config.ssh_identity_file)
    client.connect(**kwargs)
    return client


def _split_remote_host(config: AppConfig) -> tuple[str, str]:
    remote_host = config.remote_host.strip()
    if "@" in remote_host:
        user_part, host_part = remote_host.rsplit("@", 1)
        return host_part, user_part
    return remote_host, config.remote_user


@dataclass
class SSHRunner:
    config: AppConfig

    def _connect_timeout_seconds(self) -> int:
        timeout = min(self.config.command_timeout_seconds, 15)
        return max(timeout, 3)

    def _ssh_options(self, *, interactive: bool = False) -> list[str]:
        options: list[str] = []
        if not interactive:
            options.extend(
                [
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "NumberOfPasswordPrompts=0",
                ]
            )
        options.extend(
            [
                "-o",
                f"ConnectTimeout={self._connect_timeout_seconds()}",
                "-o",
                "ServerAliveInterval=5",
                "-o",
                "ServerAliveCountMax=1",
            ]
        )
        if self.config.ssh_auth_method != "password" and self.config.ssh_identities_only:
            options.extend(["-o", "IdentitiesOnly=yes"])
        if self.config.ssh_auth_method != "password" and self.config.ssh_identity_file:
            options.extend(["-i", os.path.expanduser(self.config.ssh_identity_file)])
        if self.config.ssh_config_path:
            options.extend(["-F", os.path.expanduser(self.config.ssh_config_path)])
        return options

    def build_remote_command(self, remote_command: str) -> str:
        prefix = self.config.remote_path_prefix.strip()
        command = remote_command.strip()
        return f"{prefix} {command}"

    def build_ssh_base_command(self) -> list[str]:
        return ["ssh", *self._ssh_options()]

    def build_interactive_ssh_command(self) -> list[str]:
        return ["ssh", *self._ssh_options(interactive=True), self.config.remote_host]

    def build_ssh_command(self, remote_command: str) -> list[str]:
        wrapped = self.build_remote_command(remote_command)
        return [*self.build_ssh_base_command(), self.config.remote_host, wrapped]

    def build_tunnel_command(self, *, local_port: int, remote_port: int) -> list[str]:
        return [
            *self.build_ssh_base_command(),
            "-o",
            "ExitOnForwardFailure=yes",
            "-N",
            "-L",
            f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}",
            self.config.remote_host,
        ]

    def start_managed_tunnel(self, *, local_port: int, remote_port: int) -> None:
        handle = _ManagedTunnel(self.config, local_port, remote_port)
        handle.start()
        _TUNNEL_REGISTRY[self.config.selected_profile] = handle

    def stop_managed_tunnel(self) -> bool:
        handle = _TUNNEL_REGISTRY.pop(self.config.selected_profile, None)
        if handle is None:
            return False
        handle.stop()
        return True

    def has_managed_tunnel(self) -> bool:
        return self.config.selected_profile in _TUNNEL_REGISTRY

    def run(
        self,
        remote_command: str,
        timeout_seconds: int | None = None,
        stream_callback=None,
    ) -> CommandResult:
        if self.config.ssh_auth_method == "password":
            return self._run_paramiko(remote_command, timeout_seconds=timeout_seconds, stream_callback=stream_callback)
        return self._run_subprocess(remote_command, timeout_seconds=timeout_seconds, stream_callback=stream_callback)

    def _run_subprocess(self, remote_command: str, timeout_seconds: int | None = None, stream_callback=None) -> CommandResult:
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

    def _run_paramiko(self, remote_command: str, timeout_seconds: int | None = None, stream_callback=None) -> CommandResult:
        wrapped = self.build_remote_command(remote_command)
        started = time.monotonic()
        timeout = timeout_seconds or self.config.command_timeout_seconds
        client = None
        try:
            client = _connect_paramiko(self.config)
            stdin, stdout, stderr = client.exec_command(wrapped, timeout=timeout)
            channel = stdout.channel
            stdout_parts: list[str] = []
            stderr_parts: list[str] = []
            timed_out = False
            while True:
                if channel.recv_ready():
                    chunk = channel.recv(4096).decode("utf-8", errors="replace")
                    stdout_parts.append(chunk)
                    if stream_callback:
                        stream_callback("stdout", chunk)
                if channel.recv_stderr_ready():
                    chunk = channel.recv_stderr(4096).decode("utf-8", errors="replace")
                    stderr_parts.append(chunk)
                    if stream_callback:
                        stream_callback("stderr", chunk)
                if channel.exit_status_ready():
                    while channel.recv_ready():
                        chunk = channel.recv(4096).decode("utf-8", errors="replace")
                        stdout_parts.append(chunk)
                        if stream_callback:
                            stream_callback("stdout", chunk)
                    while channel.recv_stderr_ready():
                        chunk = channel.recv_stderr(4096).decode("utf-8", errors="replace")
                        stderr_parts.append(chunk)
                        if stream_callback:
                            stream_callback("stderr", chunk)
                    break
                if time.monotonic() - started > timeout:
                    timed_out = True
                    channel.close()
                    break
                time.sleep(0.05)
            exit_code = channel.recv_exit_status() if not timed_out else -9
            duration = time.monotonic() - started
            stdout_text = "".join(stdout_parts)
            stderr_text = "".join(stderr_parts)
            ssh_issue = detect_ssh_issue(exit_code, stderr_text, timed_out)
            return CommandResult(
                command=remote_command,
                full_command=["paramiko", self.config.remote_host, wrapped],
                exit_code=exit_code,
                stdout=stdout_text,
                stderr=stderr_text,
                duration_seconds=duration,
                timed_out=timed_out,
                ssh_issue=ssh_issue,
            )
        except Exception as exc:
            duration = time.monotonic() - started
            stderr_text = str(exc)
            return CommandResult(
                command=remote_command,
                full_command=["paramiko", self.config.remote_host, wrapped],
                exit_code=255,
                stdout="",
                stderr=stderr_text,
                duration_seconds=duration,
                ssh_issue=detect_ssh_issue(255, stderr_text, False),
            )
        finally:
            if client is not None:
                client.close()


def detect_ssh_issue(exit_code: int, stderr: str, timed_out: bool) -> str | None:
    lowered = stderr.lower()
    if timed_out:
        return "remote command timed out"
    if "host key verification failed" in lowered:
        return "ssh host key verification failed"
    if "permission denied" in lowered or "password" in lowered or "authentication failed" in lowered:
        return "ssh authentication failed or requires interactive password input"
    if "could not resolve hostname" in lowered or "name or service not known" in lowered:
        return "ssh host resolution failed"
    if "operation timed out" in lowered or "connection timed out" in lowered or "timed out" in lowered:
        return "ssh connection timed out"
    if "connection refused" in lowered:
        return "ssh connection refused"
    if exit_code == 255:
        return "ssh transport failed"
    return None


def quote_remote(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)
