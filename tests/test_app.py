from config import AppConfig
import app


def test_main_exits_with_launch_result(monkeypatch):
    monkeypatch.setattr(app, "load_config", lambda: AppConfig())
    monkeypatch.setattr(app, "launch_app", lambda config: 0)

    try:
        app.main()
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("main() should exit with the application return code")
