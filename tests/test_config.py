from config import HostConfig, PRIMARY_PROFILE_NAME, default_env_path, default_logs_dir, load_config, save_config
import config as config_module


def test_load_config_supports_multiple_profiles(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "DISPLAY_NAME=示例主机",
                "REMOTE_HOST=ops@example-host.local",
                "SSH_AUTH_METHOD=password",
                "SSH_PASSWORD=secret123",
                "SSH_IDENTITY_FILE=~/.ssh/id_ed25519",
                "PROFILE_NAMES=staging",
                "ACTIVE_PROFILE=staging",
                "PROFILE_STAGING_DISPLAY_NAME=预发布环境",
                "PROFILE_STAGING_REMOTE_HOST=ops@staging.example.com",
                "PROFILE_STAGING_REMOTE_USER=ops",
                "PROFILE_STAGING_REMOTE_WORKDIR=$HOME/openclaw-staging",
                "PROFILE_STAGING_GATEWAY_WEB_PORT=28080",
                "PROFILE_STAGING_LOCAL_FORWARD_PORT=18080",
            ],
        ),
        encoding="utf-8",
    )

    config = load_config(env_file)

    assert config.selected_profile == "staging"
    assert config.profile_names == [PRIMARY_PROFILE_NAME, "staging"]
    assert config.profiles[PRIMARY_PROFILE_NAME].display_name == "示例主机"
    assert config.profiles[PRIMARY_PROFILE_NAME].ssh_auth_method == "password"
    assert config.profiles[PRIMARY_PROFILE_NAME].ssh_password == "secret123"
    assert config.profiles["staging"].remote_host == "ops@staging.example.com"
    assert config.remote_host == "ops@staging.example.com"
    assert config.remote_user == "ops"
    assert config.gateway_web_port == 28080
    assert config.local_forward_port == 18080


def test_save_config_round_trip(tmp_path):
    env_file = tmp_path / ".env"
    config = load_config(env_file)
    config = config.upsert_profile(
        HostConfig(
            profile_name="staging",
            display_name="预发布环境",
            remote_host="ops@staging.example.com",
            remote_user="ops",
        )
    )

    save_config(config, env_file)
    reloaded = load_config(env_file)

    assert reloaded.profile_names == [PRIMARY_PROFILE_NAME, "staging"]
    assert reloaded.selected_profile == "staging"
    assert reloaded.profiles["staging"].display_name == "预发布环境"
    assert reloaded.profiles["staging"].remote_host == "ops@staging.example.com"
    assert reloaded.profiles["staging"].ssh_auth_method == "key"
    assert reloaded.profiles["staging"].gateway_web_port == 18789
    assert reloaded.profiles["staging"].local_forward_port == 18789


def test_remove_profile_falls_back_to_primary():
    config = load_config()
    config = config.upsert_profile(
        HostConfig(
            profile_name="staging",
            display_name="预发布环境",
            remote_host="ops@staging.example.com",
        )
    )

    config = config.remove_profile("staging")

    assert config.profile_names == [PRIMARY_PROFILE_NAME]
    assert config.selected_profile == PRIMARY_PROFILE_NAME


def test_default_paths_use_user_data_dir_for_frozen_app(monkeypatch, tmp_path):
    monkeypatch.setattr(config_module, "is_frozen_app", lambda: True)
    monkeypatch.setattr(config_module, "user_data_dir", lambda: tmp_path / "userdata")

    assert default_env_path() == tmp_path / "userdata" / ".env"
    assert default_logs_dir() == tmp_path / "userdata" / "logs"


def test_save_config_creates_parent_dir(tmp_path):
    env_file = tmp_path / "nested" / ".env"
    config = load_config(tmp_path / "missing.env")

    save_config(config, env_file)

    assert env_file.exists()
