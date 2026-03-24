from config import AppConfig, HostConfig, PRIMARY_PROFILE_NAME
from ssh_runner import SSHRunner, detect_ssh_issue


def test_build_ssh_command_injects_path_prefix():
    config = AppConfig(
        profiles={
            PRIMARY_PROFILE_NAME: HostConfig(
                remote_host="ops@example-host.local",
                remote_path_prefix="export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH;",
                ssh_identity_file="",
                ssh_config_path="",
            ),
        }
    )
    runner = SSHRunner(config)
    command = runner.build_ssh_command("node -v")
    assert command == [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "NumberOfPasswordPrompts=0",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=5",
        "-o",
        "ServerAliveCountMax=1",
        "-o",
        "IdentitiesOnly=yes",
        "ops@example-host.local",
        "export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH; node -v",
    ]


def test_build_ssh_command_supports_identity_file_and_config():
    config = AppConfig(
        profiles={
            PRIMARY_PROFILE_NAME: HostConfig(
                remote_host="openclaw-example",
                ssh_identity_file="~/.ssh/id_ed25519",
                ssh_config_path="~/.ssh/config",
            ),
        }
    )
    runner = SSHRunner(config)

    command = runner.build_ssh_command("hostname")

    assert command == [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "NumberOfPasswordPrompts=0",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "ServerAliveInterval=5",
        "-o",
        "ServerAliveCountMax=1",
        "-o",
        "IdentitiesOnly=yes",
        "-i",
        "/Users/moon/.ssh/id_ed25519",
        "-F",
        "/Users/moon/.ssh/config",
        "openclaw-example",
        "export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH; hostname",
    ]


def test_detect_ssh_issue_for_password_prompt():
    message = detect_ssh_issue(255, "Permission denied, please try again.\npassword:", False)
    assert message == "ssh authentication failed or requires interactive password input"


def test_detect_ssh_issue_for_host_key_failure():
    message = detect_ssh_issue(255, "Host key verification failed.\n", False)
    assert message == "ssh host key verification failed"
