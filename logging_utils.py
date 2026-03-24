from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import re


def slugify_action_name(action_name: str) -> str:
    normalized = re.sub(r"[^\w]+", "_", action_name.strip().lower(), flags=re.UNICODE)
    return normalized.strip("_") or "action"


def create_log_file(logs_dir: Path, action_name: str) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = logs_dir / f"{timestamp}_{slugify_action_name(action_name)}.log"
    path.touch()
    return path


@dataclass
class ActionLogger:
    log_path: Path
    ui_callback: callable | None = None

    def write(self, line: str = "") -> None:
        text = f"{line}\n"
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(text)
        if self.ui_callback:
            self.ui_callback(text)

    def header(self, action_name: str, started_at: str) -> None:
        self.write(f"=== {action_name} ===")
        self.write(f"started_at: {started_at}")

    def footer(self, finished_at: str, status: str, duration_seconds: float) -> None:
        self.write(f"finished_at: {finished_at}")
        self.write(f"status: {status}")
        self.write(f"duration_seconds: {duration_seconds:.2f}")

    def write_command_result(self, title: str, result) -> None:
        self.write(f"[{title}] exit_code={result.exit_code} timed_out={result.timed_out}")
        self.write(f"command: {result.command}")
        self.write(f"full_command: {' '.join(result.full_command)}")
        if result.ssh_issue:
            self.write(f"ssh_issue: {result.ssh_issue}")
        self.write("--- stdout ---")
        self.write(result.stdout.rstrip())
        self.write("--- stderr ---")
        self.write(result.stderr.rstrip())

    def write_json(self, title: str, payload: dict) -> None:
        self.write(f"[{title}]")
        self.write(json.dumps(payload, ensure_ascii=False, indent=2))
