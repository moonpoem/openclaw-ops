from actions import detect_ui_assets_issue


def test_detect_ui_assets_issue_matches_expected_error():
    text = "startup failed: Control UI assets not found. Build them with `pnpm ui:build`"
    assert detect_ui_assets_issue(text) is True


def test_detect_ui_assets_issue_ignores_other_output():
    assert detect_ui_assets_issue("gateway started normally") is False
