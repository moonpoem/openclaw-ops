from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex


def normalize_remote_platform_name(text: str) -> str:
    normalized = text.strip().lower()
    if normalized.startswith("darwin"):
        return "macos"
    if normalized.startswith("linux"):
        return "linux"
    return "unknown"


@dataclass(frozen=True)
class LocalPlatformAdapter:
    platform_name: str

    @classmethod
    def for_platform(cls, platform_name: str) -> "LocalPlatformAdapter":
        return cls(platform_name=platform_name)

    def open_path_command(self, path: Path) -> list[str]:
        resolved = str(path.resolve())
        if self.platform_name == "darwin":
            return ["open", resolved]
        if self.platform_name.startswith("win"):
            return ["explorer", resolved]
        return ["xdg-open", resolved]

    def open_ssh_terminal_command(self, ssh_command: list[str]) -> list[str]:
        command_text = shlex.join(ssh_command)
        if self.platform_name == "darwin":
            script_command = command_text.replace("\\", "\\\\").replace('"', '\\"')
            return [
                "osascript",
                "-e",
                'tell application "Terminal" to activate',
                "-e",
                f'tell application "Terminal" to do script "{script_command}"',
            ]
        if self.platform_name.startswith("win"):
            return [
                "cmd",
                "/c",
                "start",
                "",
                "powershell",
                "-NoExit",
                "-Command",
                command_text,
            ]
        return ["x-terminal-emulator", "-e", *ssh_command]
