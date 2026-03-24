from logging_utils import create_log_file, slugify_action_name


def test_create_log_file_uses_timestamp_and_action_name(tmp_path):
    path = create_log_file(tmp_path, "连接检查")
    assert path.exists()
    assert path.name.endswith("_连接检查.log")


def test_slugify_action_name_preserves_non_ascii_word_characters():
    assert slugify_action_name("连接检查") == "连接检查"
