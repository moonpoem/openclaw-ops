from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ActionStatus(str, Enum):
    SUCCESS = "success"
    WARNING = "warning"
    FAILED = "failed"
    RUNNING = "running"


@dataclass
class CommandResult:
    command: str
    full_command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    ssh_issue: str | None = None


@dataclass
class StepResult:
    name: str
    status: ActionStatus
    command_result: CommandResult | None = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionResult:
    action_name: str
    status: ActionStatus
    started_at: str
    finished_at: str
    duration_seconds: float
    steps: list[StepResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    log_path: str = ""
    message: str = ""
    launch_url: str = ""


@dataclass
class ActionContext:
    action_name: str
    log_path: str
    started_at: str
