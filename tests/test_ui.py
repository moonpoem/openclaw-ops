import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication, QMessageBox

from config import AppConfig, HostConfig, PRIMARY_PROFILE_NAME
from models import ActionResult, ActionStatus
from ui import HostProfileDialog, OpenClawDesktopApp
import ui


_APP = None


def get_qapp():
    global _APP
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    _APP = app
    return _APP


def make_window():
    get_qapp()
    config = AppConfig(
        profiles={
            PRIMARY_PROFILE_NAME: HostConfig(),
            "staging": HostConfig(
                profile_name="staging",
                display_name="预发布环境",
                remote_host="ops@staging.example.com",
                remote_user="ops",
                remote_workdir="$HOME/openclaw-staging",
            ),
        },
    )
    window = OpenClawDesktopApp(config)
    return window


def test_handle_result_updates_status_and_version(monkeypatch):
    window = make_window()
    warnings = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *args: warnings.append(args[1:3]))
    monkeypatch.setattr(ui.QMessageBox, "critical", lambda *args: warnings.append(args[1:3]))
    result = ActionResult(
        action_name="验证 OpenClaw",
        status=ActionStatus.WARNING,
        started_at="2026-03-24T22:00:00",
        finished_at="2026-03-24T22:00:08",
        duration_seconds=8,
        summary={"details": {"openclaw_version": "1.2.3"}, "reasons": ["gateway token missing"]},
        log_path="/tmp/test.log",
        message="Warning",
    )

    window.handle_result(result)

    assert window.running is False
    assert window.current_task_label.text() == "当前任务: 空闲"
    assert window.current_status_label.text() == "Warning"
    assert window.status_light_color == "#eab308"
    assert window.current_version_label.text() == "1.2.3"
    assert window.last_result_label.text() == "Warning"
    assert window.last_log_path_value == "/tmp/test.log"
    assert "gateway token missing" in window.summary_text.toPlainText()
    assert warnings == []
    window.close()


def test_handle_result_extracts_version_from_connection_check(monkeypatch):
    window = make_window()
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *args: None)
    monkeypatch.setattr(ui.QMessageBox, "critical", lambda *args: None)
    result = ActionResult(
        action_name="连接检查",
        status=ActionStatus.SUCCESS,
        started_at="2026-03-29T21:00:00",
        finished_at="2026-03-29T21:00:23",
        duration_seconds=22.9,
        summary={
            "connected": True,
            "target_host": "smarthost.local",
            "diagnose": {
                "openclaw_version": "OpenClaw 2026.3.28 (f9b1079)",
                "current_version_normalized": "2026.3.28",
            },
            "verify": {
                "details": {
                    "openclaw_version": "OpenClaw 2026.3.28 (f9b1079)",
                }
            },
        },
        message="连接检查完成",
    )

    window.handle_result(result)

    assert window.current_version_label.text() == "OpenClaw 2026.3.28 (f9b1079)"
    window.close()


def test_handle_result_shows_need_upgrade_in_last_result(monkeypatch):
    window = make_window()
    warnings = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *args: warnings.append(args[1:3]))
    monkeypatch.setattr(ui.QMessageBox, "critical", lambda *args: None)

    window.handle_result(
        ActionResult(
            action_name="环境诊断",
            status=ActionStatus.WARNING,
            started_at="2026-03-24T22:00:00",
            finished_at="2026-03-24T22:00:08",
            duration_seconds=8,
            summary={
                "current_version_normalized": "2026.3.23-2",
                "latest_version_normalized": "2026.3.24-1",
                "up_to_date": False,
            },
            log_path="/tmp/test.log",
            message="需升级",
        )
    )

    assert window.last_result_label.text() == "需升级"
    assert window.status_light_color == "#eab308"
    assert warnings == []
    window.close()


def test_handle_result_updates_localhost_url(monkeypatch):
    window = make_window()
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *args: None)
    monkeypatch.setattr(ui.QMessageBox, "critical", lambda *args: None)

    window.handle_result(
        ActionResult(
            action_name="开启 localhost 访问",
            status=ActionStatus.SUCCESS,
            started_at="2026-03-29T10:00:00",
            finished_at="2026-03-29T10:00:01",
            duration_seconds=0.2,
            summary={"localhost_url": "http://127.0.0.1:18789"},
            message="localhost 访问已就绪",
        )
    )

    assert window.localhost_url_label.text() == "http://127.0.0.1:18789"
    assert window.localhost_status_label.text() == "已开启"
    window.close()


def test_handle_result_opens_localhost_launch_url(monkeypatch):
    window = make_window()
    opened = []
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *args: None)
    monkeypatch.setattr(ui.QMessageBox, "critical", lambda *args: None)
    monkeypatch.setattr(ui.webbrowser, "open", lambda url: opened.append(url))

    window.handle_result(
        ActionResult(
            action_name="打开 localhost WebUI",
            status=ActionStatus.SUCCESS,
            started_at="2026-03-29T10:00:00",
            finished_at="2026-03-29T10:00:01",
            duration_seconds=0.2,
            summary={
                "localhost_url": "http://127.0.0.1:18789",
                "launch_url": "http://127.0.0.1:18789#token=abc123",
            },
            message="localhost WebUI 已准备好",
        )
    )

    assert opened == ["http://127.0.0.1:18789#token=abc123"]
    window.close()


def test_handle_error_resets_state_and_shows_dialog(monkeypatch):
    window = make_window()
    errors = []
    monkeypatch.setattr(ui.QMessageBox, "critical", lambda *args: errors.append(args[1:3]))

    window.handle_error("traceback")

    assert window.running is False
    assert window.current_task_label.text() == "当前任务: 空闲"
    assert window.current_status_label.text() == "Failed"
    assert window.status_light_color == "#dc2626"
    assert window.summary_text.toPlainText() == "traceback"
    assert errors == [("后台线程异常", "traceback")]
    assert all(button.isEnabled() for button in window.buttons)
    window.close()


def test_copy_summary_without_content_shows_message(monkeypatch):
    window = make_window()
    infos = []
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *args: infos.append(args[1:3]))

    window.copy_summary()

    assert infos == [("暂无摘要", "还没有可复制的摘要内容。")]
    window.close()


def test_copy_log_path_without_log_shows_message(monkeypatch):
    window = make_window()
    infos = []
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *args: infos.append(args[1:3]))

    window.copy_log_path()

    assert infos == [("暂无日志", "还没有可复制的日志路径。")]
    window.close()


def test_start_action_blocks_when_running(monkeypatch):
    window = make_window()
    infos = []
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *args: infos.append(args[1:3]))
    window.running = True

    window.start_action("连接检查", lambda config, ui_callback=None: None, False)

    assert infos == [("任务进行中", "已有任务在运行中，请等待当前任务完成。")]
    window.close()


def test_start_action_respects_danger_confirmation(monkeypatch):
    window = make_window()
    monkeypatch.setattr(ui.QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)

    window.start_action("升级 OpenClaw", lambda config, ui_callback=None: None, True)

    assert window.running is False
    assert window.current_status_label.text() == "Idle"
    assert window.status_light_color == "#9ca3af"
    window.close()


def test_switch_profile_updates_target_host_and_resets_state():
    window = make_window()
    window.current_status_value = "Success"
    window.current_version_value = "1.2.3"
    window.last_result_value = "ok"
    window._refresh_status_labels()

    window.profile_selector.setCurrentIndex(1)

    assert window.config.selected_profile == "staging"
    assert window.target_host_label.text() == "ops@staging.example.com"
    assert window.current_status_label.text() == "Idle"
    assert window.status_light_color == "#9ca3af"
    assert window.current_version_label.text() == "-"
    assert window.last_result_label.text() == "-"
    window.close()


def test_start_action_sets_running_status_light(monkeypatch):
    window = make_window()

    monkeypatch.setattr(window, "_start_worker", lambda func: None)
    window.start_action("连接检查", lambda config, ui_callback=None: None, False)

    assert window.current_status_label.text() == "Running"
    assert window.status_light_color == "#2563eb"
    assert window.status_light.width() == 10
    assert window.status_light.height() == 10
    window.close()


def test_handle_result_success_sets_green_status_light(monkeypatch):
    window = make_window()
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *args: None)
    monkeypatch.setattr(ui.QMessageBox, "critical", lambda *args: None)

    window.handle_result(
        ActionResult(
            action_name="连接检查",
            status=ActionStatus.SUCCESS,
            started_at="2026-03-24T23:40:00",
            finished_at="2026-03-24T23:40:01",
            duration_seconds=0.2,
            summary={"target_host": "example-host.local"},
            message="connection ok",
        )
    )

    assert window.current_status_label.text() == "Success"
    assert window.status_light_color == "#16a34a"
    assert "#16a34a" in window.current_status_label.styleSheet()
    window.close()


def test_primary_action_buttons_are_streamlined():
    window = make_window()

    labels = [button.text() for button in window.buttons]

    assert "连接检查" in labels
    assert "打开 localhost WebUI" in labels
    assert "关闭 localhost 访问" in labels
    assert "OpenClaw 自我修复 (危险)" in labels
    assert "一键升级并启动 (危险)" in labels
    assert "验证 OpenClaw" in labels
    assert "源码构建兜底 (危险)" in labels
    assert "最新版检查" not in labels
    assert "修复 npm 环境 (危险)" not in labels
    assert "清理 OpenClaw 残留 (危险)" not in labels
    assert "升级 OpenClaw (危险)" not in labels
    assert "启动 OpenClaw" not in labels
    assert "停止 OpenClaw" not in labels
    assert "重启 OpenClaw" not in labels
    assert "修复并升级 (危险)" not in labels
    window.close()


def test_primary_action_buttons_follow_grouped_order():
    window = make_window()

    labels = [button.text() for button in window.buttons[:5]]

    assert labels == [
        "连接检查",
        "打开 localhost WebUI",
        "OpenClaw 自我修复 (危险)",
        "一键升级并启动 (危险)",
        "关闭 localhost 访问",
    ]
    window.close()


def test_advanced_and_known_issue_buttons_exist_in_collapsed_sections():
    window = make_window()

    labels = [button.text() for button in window.buttons]

    assert labels == [
        "连接检查",
        "打开 localhost WebUI",
        "OpenClaw 自我修复 (危险)",
        "一键升级并启动 (危险)",
        "关闭 localhost 访问",
        "打开日志目录",
        "复制最近摘要",
        "打开 SSH 终端",
        "OpenClaw 官方命令",
        "环境诊断",
        "验证 OpenClaw",
        "源码构建兜底 (危险)",
    ]
    window.close()


def test_buttons_have_roomier_spacing():
    window = make_window()

    assert window.buttons[0].minimumHeight() == 34

    window.close()


def test_open_ssh_terminal_launches_terminal(monkeypatch):
    window = make_window()
    commands = []
    monkeypatch.setattr(ui.sys, "platform", "darwin")
    monkeypatch.setattr(ui.subprocess, "Popen", lambda command: commands.append(command))

    window.open_ssh_terminal()

    assert commands
    assert commands[0][0] == "osascript"
    assert "Terminal" in commands[0][2]
    assert "ssh" in commands[0][4]
    assert window.config.remote_host in commands[0][4]
    window.close()


def test_show_official_commands_opens_dialog(monkeypatch):
    window = make_window()
    executed = []

    class FakeDialog:
        def __init__(self, parent=None):
            self.parent = parent

        def setWindowTitle(self, title):
            self.title = title

        def resize(self, width, height):
            self.size = (width, height)

        def reject(self):
            pass

        def accept(self):
            pass

        def exec(self):
            executed.append((self.title, self.size))

    class FakeTextEdit:
        def setReadOnly(self, value):
            self.read_only = value

        def setPlainText(self, value):
            self.value = value

    class FakeLayout:
        def __init__(self, parent=None):
            self.widgets = []

        def addWidget(self, widget):
            self.widgets.append(widget)

    class FakeButtons:
        class StandardButton:
            Close = object()

        class _Signal:
            def connect(self, callback):
                self.callback = callback

        class _FakeButton:
            def __init__(self):
                self.clicked = FakeButtons._Signal()

        def __init__(self, *_args, **_kwargs):
            self.accepted = self._Signal()
            self.rejected = self._Signal()
            self._button = self._FakeButton()

        def button(self, _which):
            return self._button

    monkeypatch.setattr(ui, "QDialog", FakeDialog)
    monkeypatch.setattr(ui, "QPlainTextEdit", FakeTextEdit)
    monkeypatch.setattr(ui, "QVBoxLayout", FakeLayout)
    monkeypatch.setattr(ui, "QDialogButtonBox", FakeButtons)

    window.show_official_commands()

    assert executed == [("OpenClaw 官方命令", (760, 620))]
    window.close()


def test_apply_profile_update_adds_profile(monkeypatch, tmp_path):
    window = make_window()
    infos = []
    monkeypatch.setattr(ui, "save_config", lambda config, env_path=None: tmp_path / ".env")
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *args: infos.append(args[1:3]))

    window._apply_profile_update(
        HostConfig(
            profile_name="lab",
            display_name="实验机",
            remote_host="moon@lab.local",
        )
    )

    assert "lab" in window.config.profiles
    assert window.config.selected_profile == "lab"
    assert window.profile_selector.currentText() == "实验机"
    assert infos == [("主机已保存", "已保存主机配置：实验机")]
    window.close()


def test_delete_primary_profile_is_blocked(monkeypatch):
    window = make_window()
    infos = []
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *args: infos.append(args[1:3]))

    window._delete_current_profile()

    assert infos == [("不能删除", "默认示例主机不能删除。")]
    assert PRIMARY_PROFILE_NAME in window.config.profiles
    window.close()


def test_delete_current_profile_removes_it_and_switches_back(monkeypatch, tmp_path):
    window = make_window()
    infos = []
    monkeypatch.setattr(ui, "save_config", lambda config, env_path=None: tmp_path / ".env")
    monkeypatch.setattr(ui.QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *args: infos.append(args[1:3]))
    window.profile_selector.setCurrentIndex(1)

    window._delete_current_profile()

    assert "staging" not in window.config.profiles
    assert window.config.selected_profile == PRIMARY_PROFILE_NAME
    assert window.profile_selector.currentText() == "示例主机"
    assert infos == [("主机已删除", "已删除主机配置：预发布环境")]
    window.close()


def test_clone_current_profile_uses_dialog_result(monkeypatch, tmp_path):
    window = make_window()
    infos = []
    monkeypatch.setattr(ui, "save_config", lambda config, env_path=None: tmp_path / ".env")
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *args: infos.append(args[1:3]))

    class FakeDialog:
        def __init__(self, parent, profile, creating, logs_dir):
            self.profile = profile
            self.creating = creating
            self.logs_dir = logs_dir

        def exec(self):
            return ui.QDialog.DialogCode.Accepted

        def profile_data(self):
            return HostConfig(
                profile_name="default_copy",
                display_name="示例主机 副本",
                remote_host="ops@copy.local",
            )

    monkeypatch.setattr(ui, "HostProfileDialog", FakeDialog)

    window._clone_current_profile()

    assert "default_copy" in window.config.profiles
    assert window.config.selected_profile == "default_copy"
    assert window.profile_selector.currentText() == "示例主机 副本"
    assert infos == [("主机已保存", "已保存主机配置：示例主机 副本")]
    window.close()


def test_open_localhost_url_without_value_requests_enable_confirmation(monkeypatch):
    window = make_window()
    prompts = []
    starts = []
    window.current_localhost_url_value = "-"
    monkeypatch.setattr(ui.QMessageBox, "question", lambda *args, **kwargs: prompts.append(args[1:3]) or QMessageBox.StandardButton.No)
    monkeypatch.setattr(window, "start_action", lambda *args: starts.append(args))

    window.open_localhost_url()

    assert prompts == [("开启 localhost 访问", "当前未开启 localhost 访问，是否立即开启并打开 WebUI？")]
    assert starts == []
    window.close()


def test_open_localhost_url_starts_prepare_action(monkeypatch):
    window = make_window()
    window.current_localhost_url_value = "http://127.0.0.1:18789"
    calls = []
    monkeypatch.setattr(window, "start_action", lambda label, func, dangerous: calls.append((label, func, dangerous)))

    window.open_localhost_url()

    assert calls and calls[0][0] == "打开 localhost WebUI"
    assert calls[0][2] is False
    window.close()


def test_open_localhost_url_confirms_and_starts_when_disabled(monkeypatch):
    window = make_window()
    calls = []
    window.current_localhost_url_value = "-"
    monkeypatch.setattr(ui.QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(window, "start_action", lambda label, func, dangerous: calls.append((label, func, dangerous)))

    window.open_localhost_url()

    assert calls and calls[0][0] == "打开 localhost WebUI"
    assert calls[0][2] is False
    window.close()


def test_host_profile_dialog_test_connection_starts_background_run(monkeypatch, tmp_path):
    get_qapp()
    dialog = HostProfileDialog(None, HostConfig(), creating=True, logs_dir=tmp_path)

    class FakeSignal:
        def connect(self, callback):
            self.callback = callback

    class FakeThread:
        def __init__(self, parent=None):
            self.started = FakeSignal()
            self.finished = FakeSignal()

        def start(self):
            pass

        def quit(self):
            pass

        def wait(self, timeout):
            return True

        def deleteLater(self):
            pass

    class FakeWorker:
        def __init__(self, config, func):
            self.result_emitted = FakeSignal()
            self.error_emitted = FakeSignal()
            self.finished = FakeSignal()

        def moveToThread(self, thread):
            self.thread = thread

        def run(self):
            pass

        def deleteLater(self):
            pass

    monkeypatch.setattr(ui, "QThread", FakeThread)
    monkeypatch.setattr(ui, "ActionWorker", FakeWorker)

    dialog._test_connection()

    assert dialog.test_connection_button.isEnabled() is False
    assert "正在测试连接" in dialog.test_status_label.text()
    assert dialog.test_worker_thread is not None
    dialog.test_in_progress = False
    dialog.close()


def test_host_profile_dialog_test_connection_success(monkeypatch, tmp_path):
    get_qapp()
    dialog = HostProfileDialog(None, HostConfig(), creating=True, logs_dir=tmp_path)
    infos = []
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *args: infos.append(args[1:3]))
    dialog.test_connection_button.setEnabled(False)

    dialog._handle_test_connection_result(
        ActionResult(
            action_name="连接检查",
            status=ActionStatus.SUCCESS,
            started_at="2026-03-24T23:30:00",
            finished_at="2026-03-24T23:30:01",
            duration_seconds=0.2,
            summary={"target_host": "example-host.local"},
            message="connection ok",
        )
    )

    assert dialog.test_connection_button.isEnabled() is True
    assert dialog.test_status_label.text() == "连接成功: example-host.local"
    assert infos == [("连接成功", "已连接到 example-host.local")]
    dialog.close()


def test_host_profile_dialog_test_connection_failure(monkeypatch, tmp_path):
    get_qapp()
    errors = []
    dialog = HostProfileDialog(None, HostConfig(), creating=True, logs_dir=tmp_path)
    monkeypatch.setattr(ui.QMessageBox, "critical", lambda *args: errors.append(args[1:3]))
    dialog.test_connection_button.setEnabled(False)

    dialog._handle_test_connection_result(
        ActionResult(
            action_name="连接检查",
            status=ActionStatus.FAILED,
            started_at="2026-03-24T23:30:00",
            finished_at="2026-03-24T23:30:01",
            duration_seconds=0.2,
            summary={"ssh_issue": "ssh authentication failed"},
            message="connection failed",
        )
    )

    assert dialog.test_connection_button.isEnabled() is True
    assert dialog.test_status_label.text() == "连接失败: ssh authentication failed"
    assert errors and errors[0][0] == "连接失败"
    assert "ssh authentication failed" in errors[0][1]
    dialog.close()


def test_host_profile_dialog_blocks_duplicate_connection_test(monkeypatch, tmp_path):
    get_qapp()
    dialog = HostProfileDialog(None, HostConfig(), creating=True, logs_dir=tmp_path)
    infos = []
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *args: infos.append(args[1:3]))
    dialog.test_in_progress = True

    dialog._test_connection()

    assert infos == [("测试进行中", "当前正在测试连接，请等待结果返回。")]
    dialog.test_in_progress = False
    dialog.close()


def test_host_profile_dialog_reject_is_blocked_while_testing(monkeypatch, tmp_path):
    get_qapp()
    dialog = HostProfileDialog(None, HostConfig(), creating=True, logs_dir=tmp_path)
    infos = []
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *args: infos.append(args[1:3]))
    dialog.test_in_progress = True

    dialog.reject()

    assert infos == [("测试进行中", "连接测试还未结束，请等待当前测试完成。")]
    assert dialog.isVisible() is False
    dialog.test_in_progress = False
    dialog.close()


def test_host_profile_dialog_close_event_is_blocked_while_testing(monkeypatch, tmp_path):
    get_qapp()
    dialog = HostProfileDialog(None, HostConfig(), creating=True, logs_dir=tmp_path)
    infos = []
    monkeypatch.setattr(ui.QMessageBox, "information", lambda *args: infos.append(args[1:3]))
    dialog.test_in_progress = True
    event = QCloseEvent()

    dialog.closeEvent(event)

    assert infos == [("测试进行中", "连接测试还未结束，请等待当前测试完成。")]
    assert event.isAccepted() is False
    dialog.test_in_progress = False
    dialog.close()
