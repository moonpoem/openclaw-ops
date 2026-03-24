from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
import os
import re
import sys


APP_NAME = "OpenClawOps"
DEFAULT_ENV_FILENAME = ".env"
DEFAULT_LOGS_DIRNAME = "logs"
DEFAULT_ENV_PATH = Path(DEFAULT_ENV_FILENAME)
PRIMARY_PROFILE_NAME = "smarthost"


def _parse_bool(value: str | bool | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _format_bool(value: bool) -> str:
    return "true" if value else "false"


def _load_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip("\"'")
    return data


def _parse_int(value: str | int | None, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    return int(value)


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def normalize_profile_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    normalized = normalized.strip("_")
    return normalized or PRIMARY_PROFILE_NAME


def is_frozen_app() -> bool:
    return bool(getattr(sys, "frozen", False))


def user_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / APP_NAME
    return Path.home() / ".config" / APP_NAME


def default_env_path() -> Path:
    if is_frozen_app():
        return user_data_dir() / DEFAULT_ENV_FILENAME
    return DEFAULT_ENV_PATH


def default_logs_dir() -> Path:
    if is_frozen_app():
        return user_data_dir() / DEFAULT_LOGS_DIRNAME
    return Path(DEFAULT_LOGS_DIRNAME)


@dataclass(frozen=True)
class HostConfig:
    profile_name: str = PRIMARY_PROFILE_NAME
    display_name: str = "我的 smarthost"
    remote_host: str = "moon@smarthost.local"
    remote_path_prefix: str = "export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH;"
    remote_user: str = "moon"
    ssh_identities_only: bool = True
    ssh_identity_file: str = "~/.ssh/id_ed25519"
    ssh_config_path: str = "~/.ssh/config"
    openclaw_repo_url: str = "https://github.com/example/openclaw.git"
    remote_workdir: str = "$HOME/openclaw-src"
    npm_global_root: str = "/opt/homebrew/lib/node_modules"
    command_timeout_seconds: int = 300
    gateway_probe_timeout_seconds: int = 8

    @property
    def openclaw_install_dir(self) -> str:
        return f"{self.npm_global_root}/openclaw"

    @property
    def openclaw_residue_glob(self) -> str:
        return f"{self.npm_global_root}/.openclaw-*"


@dataclass(frozen=True)
class AppConfig:
    profiles: dict[str, HostConfig] = field(
        default_factory=lambda: {
            PRIMARY_PROFILE_NAME: HostConfig(),
        }
    )
    selected_profile: str = PRIMARY_PROFILE_NAME
    logs_dir: Path = Path("logs")
    env_path: Path = DEFAULT_ENV_PATH

    @property
    def active_profile(self) -> HostConfig:
        profile = self.profiles.get(self.selected_profile)
        if profile is not None:
            return profile
        return next(iter(self.profiles.values()))

    @property
    def profile_names(self) -> list[str]:
        return list(self.profiles.keys())

    def select_profile(self, profile_name: str) -> AppConfig:
        if profile_name not in self.profiles:
            raise KeyError(f"unknown profile: {profile_name}")
        return replace(self, selected_profile=profile_name)

    def upsert_profile(self, profile: HostConfig, *, select: bool = True) -> AppConfig:
        normalized_name = normalize_profile_name(profile.profile_name)
        normalized_profile = replace(profile, profile_name=normalized_name)
        profiles = dict(self.profiles)
        profiles[normalized_name] = normalized_profile
        selected_profile = normalized_name if select else self.selected_profile
        return replace(self, profiles=profiles, selected_profile=selected_profile)

    def remove_profile(self, profile_name: str) -> AppConfig:
        normalized_name = normalize_profile_name(profile_name)
        if normalized_name == PRIMARY_PROFILE_NAME:
            raise ValueError("cannot remove primary profile")
        if normalized_name not in self.profiles:
            raise KeyError(f"unknown profile: {normalized_name}")
        profiles = dict(self.profiles)
        profiles.pop(normalized_name)
        selected_profile = self.selected_profile
        if selected_profile == normalized_name:
            selected_profile = PRIMARY_PROFILE_NAME
        return replace(self, profiles=profiles, selected_profile=selected_profile)

    @property
    def profile_display_name(self) -> str:
        return self.active_profile.display_name

    @property
    def remote_host(self) -> str:
        return self.active_profile.remote_host

    @property
    def remote_path_prefix(self) -> str:
        return self.active_profile.remote_path_prefix

    @property
    def remote_user(self) -> str:
        return self.active_profile.remote_user

    @property
    def ssh_identities_only(self) -> bool:
        return self.active_profile.ssh_identities_only

    @property
    def ssh_identity_file(self) -> str:
        return self.active_profile.ssh_identity_file

    @property
    def ssh_config_path(self) -> str:
        return self.active_profile.ssh_config_path

    @property
    def openclaw_repo_url(self) -> str:
        return self.active_profile.openclaw_repo_url

    @property
    def remote_workdir(self) -> str:
        return self.active_profile.remote_workdir

    @property
    def npm_global_root(self) -> str:
        return self.active_profile.npm_global_root

    @property
    def command_timeout_seconds(self) -> int:
        return self.active_profile.command_timeout_seconds

    @property
    def gateway_probe_timeout_seconds(self) -> int:
        return self.active_profile.gateway_probe_timeout_seconds

    @property
    def openclaw_install_dir(self) -> str:
        return self.active_profile.openclaw_install_dir

    @property
    def openclaw_residue_glob(self) -> str:
        return self.active_profile.openclaw_residue_glob


def _host_from_values(values: dict[str, str], prefix: str, profile_name: str, defaults: HostConfig) -> HostConfig:
    return HostConfig(
        profile_name=profile_name,
        display_name=values.get(f"{prefix}DISPLAY_NAME", defaults.display_name),
        remote_host=values.get(f"{prefix}REMOTE_HOST", defaults.remote_host),
        remote_path_prefix=values.get(f"{prefix}REMOTE_PATH_PREFIX", defaults.remote_path_prefix),
        remote_user=values.get(f"{prefix}REMOTE_USER", defaults.remote_user),
        ssh_identities_only=_parse_bool(
            values.get(f"{prefix}SSH_IDENTITIES_ONLY"),
            defaults.ssh_identities_only,
        ),
        ssh_identity_file=values.get(f"{prefix}SSH_IDENTITY_FILE", defaults.ssh_identity_file),
        ssh_config_path=values.get(f"{prefix}SSH_CONFIG_PATH", defaults.ssh_config_path),
        openclaw_repo_url=values.get(f"{prefix}OPENCLAW_REPO_URL", defaults.openclaw_repo_url),
        remote_workdir=values.get(f"{prefix}REMOTE_WORKDIR", defaults.remote_workdir),
        npm_global_root=values.get(f"{prefix}NPM_GLOBAL_ROOT", defaults.npm_global_root),
        command_timeout_seconds=_parse_int(
            values.get(f"{prefix}COMMAND_TIMEOUT_SECONDS"),
            defaults.command_timeout_seconds,
        ),
        gateway_probe_timeout_seconds=_parse_int(
            values.get(f"{prefix}GATEWAY_PROBE_TIMEOUT_SECONDS"),
            defaults.gateway_probe_timeout_seconds,
        ),
    )


def load_config(env_path: str | os.PathLike[str] = DEFAULT_ENV_PATH) -> AppConfig:
    path = Path(env_path)
    if path == DEFAULT_ENV_PATH:
        path = default_env_path()
    env_file_values = _load_env_file(path)
    merged = {**env_file_values, **os.environ}

    primary_profile = _host_from_values(
        merged,
        prefix="",
        profile_name=PRIMARY_PROFILE_NAME,
        defaults=HostConfig(),
    )
    profiles: dict[str, HostConfig] = {PRIMARY_PROFILE_NAME: primary_profile}

    for raw_name in _split_csv(merged.get("PROFILE_NAMES")):
        profile_key = normalize_profile_name(raw_name)
        if profile_key == PRIMARY_PROFILE_NAME:
            continue
        prefix = f"PROFILE_{profile_key.upper()}_"
        profiles[profile_key] = _host_from_values(
            merged,
            prefix=prefix,
            profile_name=profile_key,
            defaults=primary_profile,
        )

    selected_profile = normalize_profile_name(merged.get("ACTIVE_PROFILE", PRIMARY_PROFILE_NAME))
    if selected_profile not in profiles:
        selected_profile = PRIMARY_PROFILE_NAME

    return AppConfig(
        profiles=profiles,
        selected_profile=selected_profile,
        logs_dir=Path(merged.get("LOGS_DIR", str(default_logs_dir()))),
        env_path=path,
    )


def save_config(config: AppConfig, env_path: str | os.PathLike[str] | None = None) -> Path:
    path = Path(env_path) if env_path is not None else config.env_path
    path.parent.mkdir(parents=True, exist_ok=True)
    primary_profile = config.profiles.get(PRIMARY_PROFILE_NAME, HostConfig())
    extra_profiles = [name for name in config.profile_names if name != PRIMARY_PROFILE_NAME]

    lines = [
        f"DISPLAY_NAME={primary_profile.display_name}",
        f"REMOTE_HOST={primary_profile.remote_host}",
        f"REMOTE_PATH_PREFIX={primary_profile.remote_path_prefix}",
        f"REMOTE_USER={primary_profile.remote_user}",
        f"SSH_IDENTITIES_ONLY={_format_bool(primary_profile.ssh_identities_only)}",
        f"SSH_IDENTITY_FILE={primary_profile.ssh_identity_file}",
        f"SSH_CONFIG_PATH={primary_profile.ssh_config_path}",
        f"OPENCLAW_REPO_URL={primary_profile.openclaw_repo_url}",
        f"REMOTE_WORKDIR={primary_profile.remote_workdir}",
        f"NPM_GLOBAL_ROOT={primary_profile.npm_global_root}",
        f"COMMAND_TIMEOUT_SECONDS={primary_profile.command_timeout_seconds}",
        f"GATEWAY_PROBE_TIMEOUT_SECONDS={primary_profile.gateway_probe_timeout_seconds}",
        f"LOGS_DIR={config.logs_dir}",
        f"PROFILE_NAMES={','.join(extra_profiles)}",
        f"ACTIVE_PROFILE={config.selected_profile}",
    ]

    for profile_name in extra_profiles:
        profile = config.profiles[profile_name]
        prefix = f"PROFILE_{profile_name.upper()}_"
        lines.extend(
            [
                f"{prefix}DISPLAY_NAME={profile.display_name}",
                f"{prefix}REMOTE_HOST={profile.remote_host}",
                f"{prefix}REMOTE_PATH_PREFIX={profile.remote_path_prefix}",
                f"{prefix}REMOTE_USER={profile.remote_user}",
                f"{prefix}SSH_IDENTITIES_ONLY={_format_bool(profile.ssh_identities_only)}",
                f"{prefix}SSH_IDENTITY_FILE={profile.ssh_identity_file}",
                f"{prefix}SSH_CONFIG_PATH={profile.ssh_config_path}",
                f"{prefix}OPENCLAW_REPO_URL={profile.openclaw_repo_url}",
                f"{prefix}REMOTE_WORKDIR={profile.remote_workdir}",
                f"{prefix}NPM_GLOBAL_ROOT={profile.npm_global_root}",
                f"{prefix}COMMAND_TIMEOUT_SECONDS={profile.command_timeout_seconds}",
                f"{prefix}GATEWAY_PROBE_TIMEOUT_SECONDS={profile.gateway_probe_timeout_seconds}",
            ]
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
