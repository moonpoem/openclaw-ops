from pathlib import Path

from platform_support import LocalPlatformAdapter, normalize_remote_platform_name


def test_normalize_remote_platform_name_maps_darwin():
    assert normalize_remote_platform_name("Darwin") == "macos"


def test_normalize_remote_platform_name_maps_linux():
    assert normalize_remote_platform_name("Linux") == "linux"


def test_local_platform_adapter_opens_path_on_macos():
    command = LocalPlatformAdapter.for_platform("darwin").open_path_command(Path("/tmp/logs"))
    assert command == ["open", str(Path("/tmp/logs").resolve())]


def test_local_platform_adapter_opens_path_on_windows():
    command = LocalPlatformAdapter.for_platform("win32").open_path_command(Path("/tmp/logs"))
    assert command == ["explorer", str(Path("/tmp/logs").resolve())]


def test_local_platform_adapter_builds_windows_ssh_terminal_command():
    command = LocalPlatformAdapter.for_platform("win32").open_ssh_terminal_command(["ssh", "ops@example-host.local"])
    assert command == [
        "cmd",
        "/c",
        "start",
        "",
        "powershell",
        "-NoExit",
        "-Command",
        "ssh ops@example-host.local",
    ]
