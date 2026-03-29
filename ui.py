from __future__ import annotations

import subprocess
import sys
import traceback
import webbrowser
from pathlib import Path
import shlex

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QCloseEvent, QGuiApplication, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from actions import (
    check_connection,
    diagnose_environment,
    fallback_source_build,
    format_summary,
    get_localhost_access_url,
    open_localhost_webui,
    repair_and_upgrade,
    self_repair_openclaw,
    start_localhost_access,
    stop_localhost_access,
    verify_openclaw,
)
from config import AppConfig, HostConfig, normalize_profile_name, save_config
from models import ActionResult, ActionStatus
from ssh_runner import SSHRunner


ROOT_DIR = Path(__file__).resolve().parent
APP_ICON_PATH = ROOT_DIR / "assets" / "openclaw.png"
OFFICIAL_COMMANDS_REFERENCE = """OpenClaw 官方 CLI 速查

常用检查
  openclaw status
  openclaw status --all
  openclaw status --deep
  openclaw health --json
  openclaw logs --follow

更新与修复
  openclaw doctor
  openclaw doctor --repair
  openclaw doctor --generate-gateway-token
  openclaw update

Gateway
  openclaw gateway status
  openclaw gateway health --url ws://127.0.0.1:18789
  openclaw gateway stop
  openclaw gateway restart
  openclaw gateway --port 18789

渠道与会话
  openclaw channels status
  openclaw channels login
  openclaw channels logout
  openclaw sessions
  openclaw message

模型与系统
  openclaw models status
  openclaw system
  openclaw memory
  openclaw directory

其他官方命令入口
  setup / onboard / configure / config
  dashboard / backup / reset / uninstall
  agent / agents / acp
  nodes / node / devices
  approvals / sandbox
  tui / browser / cron / dns / docs
  hooks / webhooks / pairing / plugins / channels

官方文档
  CLI Reference: https://docs.openclaw.ai/cli
  Gateway CLI: https://docs.openclaw.ai/cli/gateway
  Health Checks: https://docs.openclaw.ai/gateway/health
  Updating: https://docs.openclaw.ai/updating
"""


class HostProfileDialog(QDialog):
    def __init__(self, parent: QWidget | None, profile: HostConfig, *, creating: bool, logs_dir):
        super().__init__(parent)
        self.profile = profile
        self.creating = creating
        self.logs_dir = logs_dir
        self.test_worker_thread: QThread | None = None
        self.test_worker: ActionWorker | None = None
        self.test_in_progress = False
        self.setWindowTitle("新增主机" if creating else "编辑当前主机")
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.profile_name_input = QLineEdit(self.profile.profile_name)
        self.profile_name_input.setEnabled(self.creating)
        self.display_name_input = QLineEdit(self.profile.display_name)
        self.remote_host_input = QLineEdit(self.profile.remote_host)
        self.remote_user_input = QLineEdit(self.profile.remote_user)
        self.identity_only_input = QCheckBox()
        self.identity_only_input.setChecked(self.profile.ssh_identities_only)
        self.identity_file_input = QLineEdit(self.profile.ssh_identity_file)
        self.ssh_config_input = QLineEdit(self.profile.ssh_config_path)
        self.command_timeout_input = QSpinBox()
        self.command_timeout_input.setRange(1, 7200)
        self.command_timeout_input.setValue(self.profile.command_timeout_seconds)
        self.gateway_timeout_input = QSpinBox()
        self.gateway_timeout_input.setRange(1, 300)
        self.gateway_timeout_input.setValue(self.profile.gateway_probe_timeout_seconds)
        self.gateway_web_port_input = QSpinBox()
        self.gateway_web_port_input.setRange(1, 65535)
        self.gateway_web_port_input.setValue(self.profile.gateway_web_port)
        self.local_forward_port_input = QSpinBox()
        self.local_forward_port_input.setRange(1, 65535)
        self.local_forward_port_input.setValue(self.profile.local_forward_port)

        form.addRow("内部名称", self.profile_name_input)
        form.addRow("显示名称", self.display_name_input)
        form.addRow("SSH 目标", self.remote_host_input)
        form.addRow("远程用户", self.remote_user_input)
        form.addRow("强制 IdentitiesOnly", self.identity_only_input)
        form.addRow("私钥路径", self.identity_file_input)
        form.addRow("SSH 配置路径", self.ssh_config_input)
        form.addRow("命令超时秒数", self.command_timeout_input)
        form.addRow("gateway 探活秒数", self.gateway_timeout_input)
        form.addRow("远端 WebUI 端口", self.gateway_web_port_input)
        form.addRow("本地转发端口", self.local_forward_port_input)
        layout.addLayout(form)

        self.test_status_label = QLabel("")
        self.test_status_label.setWordWrap(True)
        layout.addWidget(self.test_status_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.test_connection_button = buttons.addButton("测试连接", QDialogButtonBox.ButtonRole.ActionRole)
        self.test_connection_button.clicked.connect(self._test_connection)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def profile_data(self) -> HostConfig:
        profile_name = normalize_profile_name(self.profile_name_input.text())
        return HostConfig(
            profile_name=profile_name,
            display_name=self.display_name_input.text().strip() or profile_name,
            remote_host=self.remote_host_input.text().strip(),
            remote_user=self.remote_user_input.text().strip() or self.profile.remote_user,
            ssh_identities_only=self.identity_only_input.isChecked(),
            ssh_identity_file=self.identity_file_input.text().strip(),
            ssh_config_path=self.ssh_config_input.text().strip(),
            remote_path_prefix=self.profile.remote_path_prefix,
            openclaw_repo_url=self.profile.openclaw_repo_url,
            remote_workdir=self.profile.remote_workdir,
            npm_global_root=self.profile.npm_global_root,
            command_timeout_seconds=self.command_timeout_input.value(),
            gateway_probe_timeout_seconds=self.gateway_timeout_input.value(),
            gateway_web_port=self.gateway_web_port_input.value(),
            local_forward_port=self.local_forward_port_input.value(),
        )

    def _test_connection(self) -> None:
        if self.test_in_progress:
            QMessageBox.information(self, "测试进行中", "当前正在测试连接，请等待结果返回。")
            return
        profile = self.profile_data()
        if not profile.remote_host:
            QMessageBox.warning(self, "缺少 SSH 目标", "请先填写 SSH 目标。")
            return
        config = AppConfig(
            profiles={profile.profile_name: profile},
            selected_profile=profile.profile_name,
            logs_dir=self.logs_dir,
        )
        self._set_test_running_state(True, "正在测试连接... 这一步会在超时后自动失败，不会一直等待。")
        self.test_worker_thread = QThread(self)
        self.test_worker = ActionWorker(config, check_connection)
        self.test_worker.moveToThread(self.test_worker_thread)
        self.test_worker_thread.started.connect(self.test_worker.run)
        self.test_worker.result_emitted.connect(self._handle_test_connection_result)
        self.test_worker.error_emitted.connect(self._handle_test_connection_error)
        self.test_worker.finished.connect(self._cleanup_test_worker)
        self.test_worker.finished.connect(self.test_worker_thread.quit)
        self.test_worker_thread.finished.connect(self.test_worker_thread.deleteLater)
        self.test_worker_thread.start()

    def _handle_test_connection_result(self, result: ActionResult) -> None:
        self._set_test_running_state(False)
        if result.status == ActionStatus.SUCCESS:
            target_host = result.summary.get("target_host") or self.profile_data().remote_host
            self.test_status_label.setText(f"连接成功: {target_host}")
            QMessageBox.information(self, "连接成功", f"已连接到 {target_host}")
            return
        self.test_status_label.setText(self._format_connection_failure_text(result.summary))
        QMessageBox.critical(self, "连接失败", format_summary(result.summary))

    def _handle_test_connection_error(self, trace: str) -> None:
        self._set_test_running_state(False)
        self.test_status_label.setText("连接失败: 后台线程异常")
        QMessageBox.critical(self, "连接失败", trace)

    def _cleanup_test_worker(self) -> None:
        if self.test_worker is not None:
            self.test_worker.deleteLater()
            self.test_worker = None
        self.test_worker_thread = None

    def _set_test_running_state(self, running: bool, status_text: str | None = None) -> None:
        self.test_in_progress = running
        self.test_connection_button.setEnabled(not running)
        save_button = self.findChild(QDialogButtonBox)
        if save_button is not None:
            save_button.button(QDialogButtonBox.StandardButton.Save).setEnabled(not running)
        if status_text is not None:
            self.test_status_label.setText(status_text)

    def _format_connection_failure_text(self, summary: dict) -> str:
        issue = str(summary.get("ssh_issue") or "").strip()
        stderr = str(summary.get("stderr") or "").strip()
        if issue:
            return f"连接失败: {issue}"
        if stderr:
            first_line = stderr.splitlines()[0]
            return f"连接失败: {first_line}"
        if summary.get("timed_out"):
            return "连接失败: 连接测试超时"
        return "连接失败"

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.test_in_progress:
            QMessageBox.information(self, "测试进行中", "连接测试还未结束，请等待当前测试完成。")
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if self.test_in_progress:
            QMessageBox.information(self, "测试进行中", "连接测试还未结束，请等待当前测试完成。")
            return
        super().reject()

    def accept(self) -> None:
        if self.test_in_progress:
            QMessageBox.information(self, "测试进行中", "连接测试还未结束，请等待当前测试完成。")
            return
        super().accept()


class ActionWorker(QObject):
    log_emitted = pyqtSignal(str)
    result_emitted = pyqtSignal(object)
    error_emitted = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, config: AppConfig, func):
        super().__init__()
        self.config = config
        self.func = func

    def run(self) -> None:
        def ui_callback(text: str) -> None:
            self.log_emitted.emit(text)

        try:
            result = self.func(self.config, ui_callback=ui_callback)
            self.result_emitted.emit(result)
        except Exception:
            self.error_emitted.emit(traceback.format_exc())
        finally:
            self.finished.emit()


class OpenClawDesktopApp(QMainWindow):
    def __init__(self, config: AppConfig):
        super().__init__()
        self.config = config
        self.running = False
        self.buttons: list[QPushButton] = []
        self.worker_thread: QThread | None = None
        self.worker: ActionWorker | None = None
        self.current_task_value = "空闲"
        self.current_status_value = "Idle"
        self.current_version_value = "-"
        self.last_result_value = "-"
        self.current_localhost_url_value = get_localhost_access_url(config) or "-"
        self.current_localhost_status_value = self._compute_localhost_status(self.current_localhost_url_value)
        self.current_localhost_launch_url_value = ""
        self.bottom_status_value = "未运行"
        self.last_finished_value = "-"
        self.last_log_path_value = "-"
        self.status_light_color = "#9ca3af"
        self._build_ui()

    def _build_ui(self) -> None:
        self.setWindowTitle("OpenClaw 桌面运维工具")
        self.resize(1280, 840)

        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        top_card = QFrame()
        top_card.setFrameShape(QFrame.Shape.StyledPanel)
        top_layout = QGridLayout(top_card)
        top_layout.setHorizontalSpacing(18)
        top_layout.setVerticalSpacing(10)

        selector_row = QWidget()
        selector_layout = QHBoxLayout(selector_row)
        selector_layout.setContentsMargins(0, 0, 0, 0)
        selector_layout.setSpacing(8)
        self.profile_selector = QComboBox()
        self.profile_selector.currentIndexChanged.connect(self._switch_profile)
        self.new_host_button = QPushButton("新增主机")
        self.new_host_button.clicked.connect(self._create_profile)
        self.clone_host_button = QPushButton("复制当前主机")
        self.clone_host_button.clicked.connect(self._clone_current_profile)
        self.edit_host_button = QPushButton("编辑当前主机")
        self.edit_host_button.clicked.connect(self._edit_current_profile)
        self.delete_host_button = QPushButton("删除当前主机")
        self.delete_host_button.clicked.connect(self._delete_current_profile)
        selector_layout.addWidget(self.profile_selector, 1)
        selector_layout.addWidget(self.new_host_button)
        selector_layout.addWidget(self.clone_host_button)
        selector_layout.addWidget(self.edit_host_button)
        selector_layout.addWidget(self.delete_host_button)

        self.target_host_label = QLabel(self.config.remote_host)
        self.current_status_label = QLabel(self.current_status_value)
        self.current_version_label = QLabel(self.current_version_value)
        self.last_result_label = QLabel(self.last_result_value)
        self.localhost_status_label = QLabel(self.current_localhost_status_value)
        self.localhost_url_label = QLabel(self.current_localhost_url_value)
        self._add_info_row(top_layout, 0, "当前主机", selector_row)
        self._add_info_row(top_layout, 1, "目标主机", self.target_host_label)
        self._add_info_row(top_layout, 2, "当前状态", self.current_status_label)
        self._add_info_row(top_layout, 3, "当前 OpenClaw 版本", self.current_version_label)
        self._add_info_row(top_layout, 4, "localhost 状态", self.localhost_status_label)
        self._add_info_row(top_layout, 5, "localhost 地址", self.localhost_url_label)
        self._add_info_row(top_layout, 6, "最近一次操作结果", self.last_result_label)
        layout.addWidget(top_card)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        footer = QFrame()
        footer.setFrameShape(QFrame.Shape.StyledPanel)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(12, 10, 12, 10)
        footer_layout.setSpacing(8)
        self.current_task_label = QLabel(f"当前任务: {self.current_task_value}")
        self.status_light = QLabel()
        self.status_light.setFixedSize(10, 10)
        self.bottom_status_label = QLabel(self.bottom_status_value)
        self.last_finished_label = QLabel(f"最后完成时间: {self.last_finished_value}")
        footer_layout.addWidget(self.current_task_label)
        footer_layout.addWidget(self._divider_label())
        footer_layout.addWidget(self.status_light)
        footer_layout.addWidget(self.bottom_status_label)
        footer_layout.addWidget(self._divider_label())
        footer_layout.addWidget(self.last_finished_label)
        footer_layout.addStretch(1)
        layout.addWidget(footer)

        self.setCentralWidget(root)
        self._build_menu()
        self._reload_profile_selector()

    def _build_left_panel(self) -> QWidget:
        panel = QFrame()
        panel.setMinimumWidth(280)
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)
        layout.addWidget(QLabel("操作"))

        function_specs = [
            ("连接检查", check_connection, False),
            ("打开 localhost WebUI", self.open_localhost_url),
            ("OpenClaw 自我修复", self_repair_openclaw, True),
            ("一键升级并启动", repair_and_upgrade, True),
            ("关闭 localhost 访问", stop_localhost_access, False),
        ]
        layout.addWidget(self._build_mixed_section("功能区", function_specs))

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(separator)

        log_specs = [
            ("打开日志目录", self.open_logs_dir),
            ("复制最近摘要", self.copy_summary),
        ]
        layout.addWidget(self._build_mixed_section("日志区", log_specs))

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(separator)

        tool_specs = [
            ("打开 SSH 终端", self.open_ssh_terminal),
            ("OpenClaw 官方命令", self.show_official_commands),
        ]
        layout.addWidget(self._build_mixed_section("工具区", tool_specs))

        layout.addStretch(1)

        advanced_specs = [
            ("环境诊断", diagnose_environment, False),
            ("验证 OpenClaw", verify_openclaw, False),
            ("源码构建兜底", fallback_source_build, True),
        ]
        layout.addWidget(self._build_collapsible_action_section("高级操作", advanced_specs))
        return panel

    def _build_action_section(self, title: str, action_specs: list[tuple[str, object, bool]]) -> QWidget:
        section = QWidget()
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(8)
        heading = QLabel(title)
        heading.setStyleSheet("font-weight: 600; color: #334155;")
        section_layout.addWidget(heading)
        for label, func, dangerous in action_specs:
            text = f"{label} {'(危险)' if dangerous else ''}".strip()
            button = QPushButton(text)
            button.setMinimumHeight(34)
            button.clicked.connect(lambda _checked=False, f=func, l=label, d=dangerous: self.start_action(l, f, d))
            section_layout.addWidget(button)
            self.buttons.append(button)
        return section

    def _build_mixed_section(self, title: str, action_specs: list[tuple]) -> QWidget:
        section = QWidget()
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(8)
        heading = QLabel(title)
        heading.setStyleSheet("font-weight: 600; color: #334155;")
        section_layout.addWidget(heading)
        for spec in action_specs:
            if len(spec) == 3:
                label, func, dangerous = spec
                text = f"{label} {'(危险)' if dangerous else ''}".strip()
                button = QPushButton(text)
                button.setMinimumHeight(34)
                button.clicked.connect(lambda _checked=False, f=func, l=label, d=dangerous: self.start_action(l, f, d))
            else:
                label, handler = spec
                button = QPushButton(label)
                button.setMinimumHeight(34)
                button.clicked.connect(handler)
            section_layout.addWidget(button)
            self.buttons.append(button)
        return section

    def _build_collapsible_action_section(self, title: str, action_specs: list[tuple[str, object, bool]]) -> QWidget:
        section = QWidget()
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(8)

        toggle = QToolButton()
        toggle.setText(title)
        toggle.setCheckable(True)
        toggle.setChecked(False)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setArrowType(Qt.ArrowType.RightArrow)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(12, 0, 0, 0)
        content_layout.setSpacing(8)
        content.setVisible(False)
        for label, func, dangerous in action_specs:
            text = f"{label} {'(危险)' if dangerous else ''}".strip()
            button = QPushButton(text)
            button.setMinimumHeight(34)
            button.clicked.connect(lambda _checked=False, f=func, l=label, d=dangerous: self.start_action(l, f, d))
            content_layout.addWidget(button)
            self.buttons.append(button)

        def _toggle_content(checked: bool) -> None:
            toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
            content.setVisible(checked)

        toggle.toggled.connect(_toggle_content)
        section_layout.addWidget(toggle)
        section_layout.addWidget(content)
        return section

    def _build_right_panel(self) -> QWidget:
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(QLabel("实时日志"))
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text, 3)
        layout.addWidget(QLabel("最近摘要"))
        self.summary_text = QPlainTextEdit()
        self.summary_text.setReadOnly(True)
        layout.addWidget(self.summary_text, 2)
        return panel

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("文件")
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)

    def _add_info_row(self, layout: QGridLayout, row: int, label: str, widget: QWidget) -> None:
        key = QLabel(f"{label}:")
        key.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(key, row, 0)
        if isinstance(widget, QLabel):
            widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(widget, row, 1)

    def _divider_label(self) -> QLabel:
        return QLabel("|")

    def _reload_profile_selector(self) -> None:
        self.profile_selector.blockSignals(True)
        self.profile_selector.clear()
        for profile_name in self.config.profile_names:
            profile = self.config.profiles[profile_name]
            self.profile_selector.addItem(profile.display_name, profile_name)
        self.profile_selector.setCurrentIndex(self.config.profile_names.index(self.config.selected_profile))
        self.profile_selector.blockSignals(False)
        self._refresh_status_labels()

    def _default_new_profile(self) -> HostConfig:
        base = self.config.active_profile
        return HostConfig(
            profile_name="new_host",
            display_name="新主机",
            remote_host="ops@newhost.local",
            remote_user=base.remote_user,
            ssh_identities_only=base.ssh_identities_only,
            ssh_identity_file=base.ssh_identity_file,
            ssh_config_path=base.ssh_config_path,
            remote_path_prefix=base.remote_path_prefix,
            openclaw_repo_url=base.openclaw_repo_url,
            remote_workdir=base.remote_workdir,
            npm_global_root=base.npm_global_root,
            command_timeout_seconds=base.command_timeout_seconds,
            gateway_probe_timeout_seconds=base.gateway_probe_timeout_seconds,
            gateway_web_port=base.gateway_web_port,
            local_forward_port=base.local_forward_port,
        )

    def _create_profile(self) -> None:
        if self.running:
            QMessageBox.information(self, "任务进行中", "任务运行期间不能新增主机。")
            return
        dialog = HostProfileDialog(self, self._default_new_profile(), creating=True, logs_dir=self.config.logs_dir)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._apply_profile_update(dialog.profile_data())

    def _clone_current_profile(self) -> None:
        if self.running:
            QMessageBox.information(self, "任务进行中", "任务运行期间不能复制主机。")
            return
        base = self.config.active_profile
        cloned = HostConfig(
            profile_name=f"{base.profile_name}_copy",
            display_name=f"{base.display_name} 副本",
            remote_host=base.remote_host,
            remote_user=base.remote_user,
            ssh_identities_only=base.ssh_identities_only,
            ssh_identity_file=base.ssh_identity_file,
            ssh_config_path=base.ssh_config_path,
            remote_path_prefix=base.remote_path_prefix,
            openclaw_repo_url=base.openclaw_repo_url,
            remote_workdir=base.remote_workdir,
            npm_global_root=base.npm_global_root,
            command_timeout_seconds=base.command_timeout_seconds,
            gateway_probe_timeout_seconds=base.gateway_probe_timeout_seconds,
            gateway_web_port=base.gateway_web_port,
            local_forward_port=base.local_forward_port,
        )
        dialog = HostProfileDialog(self, cloned, creating=True, logs_dir=self.config.logs_dir)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._apply_profile_update(dialog.profile_data())

    def _edit_current_profile(self) -> None:
        if self.running:
            QMessageBox.information(self, "任务进行中", "任务运行期间不能编辑主机。")
            return
        dialog = HostProfileDialog(self, self.config.active_profile, creating=False, logs_dir=self.config.logs_dir)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._apply_profile_update(dialog.profile_data())

    def _delete_current_profile(self) -> None:
        if self.running:
            QMessageBox.information(self, "任务进行中", "任务运行期间不能删除主机。")
            return
        profile = self.config.active_profile
        if profile.profile_name == "default":
            QMessageBox.information(self, "不能删除", "默认示例主机不能删除。")
            return
        confirmed = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除主机“{profile.display_name}”吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        self.config = self.config.remove_profile(profile.profile_name)
        save_config(self.config)
        self._reload_profile_selector()
        self._reset_profile_state()
        QMessageBox.information(self, "主机已删除", f"已删除主机配置：{profile.display_name}")

    def _apply_profile_update(self, profile: HostConfig) -> None:
        self.config = self.config.upsert_profile(profile, select=True)
        save_config(self.config)
        self._reload_profile_selector()
        self._reset_profile_state()
        QMessageBox.information(self, "主机已保存", f"已保存主机配置：{profile.display_name}")

    def start_action(self, label: str, func, dangerous: bool) -> None:
        if self.running:
            QMessageBox.information(self, "任务进行中", "已有任务在运行中，请等待当前任务完成。")
            return
        if dangerous:
            confirmed = QMessageBox.question(
                self,
                "确认操作",
                f"{label} 可能修改远程环境，是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirmed != QMessageBox.StandardButton.Yes:
                return

        self.running = True
        self.current_task_value = label
        self.current_status_value = "Running"
        self.bottom_status_value = "运行中"
        self._refresh_status_labels()
        self._set_controls_enabled(False)
        self.log_text.clear()
        self.summary_text.clear()
        self._start_worker(func)

    def _start_worker(self, func) -> None:
        self.worker_thread = QThread(self)
        self.worker = ActionWorker(self.config, func)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.log_emitted.connect(self.append_log)
        self.worker.result_emitted.connect(self.handle_result)
        self.worker.error_emitted.connect(self.handle_error)
        self.worker.finished.connect(self._cleanup_worker)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()

    def append_log(self, text: str) -> None:
        cursor = self.log_text.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self.log_text.setTextCursor(cursor)
        self.log_text.ensureCursorVisible()

    def handle_result(self, result: ActionResult) -> None:
        self.running = False
        self._set_controls_enabled(True)
        self.current_task_value = "空闲"
        self.current_status_value = result.status.value.title()
        self.current_version_value = self._extract_version(result)
        self.last_result_value = result.message or result.status.value
        localhost_url = self._extract_localhost_url(result)
        if localhost_url is not None:
            self.current_localhost_url_value = localhost_url
            self.current_localhost_status_value = self._compute_localhost_status(localhost_url)
        launch_url = self._extract_localhost_launch_url(result)
        if launch_url is not None:
            self.current_localhost_launch_url_value = launch_url
        self.bottom_status_value = "运行中: 否"
        self.last_finished_value = result.finished_at
        self.last_log_path_value = result.log_path or "-"
        self.summary_text.setPlainText(format_summary(result.summary))
        self._refresh_status_labels()
        if result.action_name == "打开 localhost WebUI" and self.current_localhost_launch_url_value:
            webbrowser.open(self.current_localhost_launch_url_value)

    def handle_error(self, trace: str) -> None:
        self.running = False
        self._set_controls_enabled(True)
        self.current_task_value = "空闲"
        self.current_status_value = "Failed"
        self.last_result_value = "UI thread error"
        self.bottom_status_value = "运行中: 否"
        self.summary_text.setPlainText(trace)
        self._refresh_status_labels()
        QMessageBox.critical(self, "后台线程异常", trace)

    def _cleanup_worker(self) -> None:
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None
        self.worker_thread = None

    def _extract_version(self, result: ActionResult) -> str:
        if isinstance(result.summary, dict):
            details = result.summary.get("details")
            if isinstance(details, dict):
                version = details.get("openclaw_version")
                if isinstance(version, str) and version:
                    return version
            diagnose = result.summary.get("diagnose")
            if isinstance(diagnose, dict):
                version = diagnose.get("openclaw_version")
                if isinstance(version, str) and version:
                    return version
                normalized = diagnose.get("current_version_normalized")
                if isinstance(normalized, str) and normalized:
                    return normalized
            verify = result.summary.get("verify")
            if isinstance(verify, dict):
                verify_details = verify.get("details")
                if isinstance(verify_details, dict):
                    version = verify_details.get("openclaw_version")
                    if isinstance(version, str) and version:
                        return version
            version = result.summary.get("openclaw_version")
            if version is not None:
                return str(version)
        return self.current_version_value

    def _extract_localhost_url(self, result: ActionResult) -> str | None:
        if isinstance(result.summary, dict) and "localhost_url" in result.summary:
            value = str(result.summary.get("localhost_url") or "").strip()
            return value or "-"
        return None

    def _extract_localhost_launch_url(self, result: ActionResult) -> str | None:
        if isinstance(result.summary, dict) and "launch_url" in result.summary:
            value = str(result.summary.get("launch_url") or "").strip()
            return value
        if result.action_name == "关闭 localhost 访问":
            return ""
        return None

    def _set_controls_enabled(self, enabled: bool) -> None:
        for button in self.buttons:
            button.setEnabled(enabled)
        self.profile_selector.setEnabled(enabled)
        self.new_host_button.setEnabled(enabled)
        self.clone_host_button.setEnabled(enabled)
        self.edit_host_button.setEnabled(enabled)
        self.delete_host_button.setEnabled(enabled)

    def _switch_profile(self, index: int) -> None:
        profile_name = self.profile_selector.itemData(index)
        if not isinstance(profile_name, str) or profile_name == self.config.selected_profile:
            return
        if self.running:
            QMessageBox.information(self, "任务进行中", "任务运行期间不能切换主机。")
            self._reload_profile_selector()
            return
        self.config = self.config.select_profile(profile_name)
        self._reset_profile_state()

    def _reset_profile_state(self) -> None:
        self.current_task_value = "空闲"
        self.current_status_value = "Idle"
        self.current_version_value = "-"
        self.last_result_value = "-"
        self.current_localhost_url_value = get_localhost_access_url(self.config) or "-"
        self.current_localhost_status_value = self._compute_localhost_status(self.current_localhost_url_value)
        self.current_localhost_launch_url_value = ""
        self.bottom_status_value = "未运行"
        self.last_finished_value = "-"
        self.last_log_path_value = "-"
        self.log_text.clear()
        self.summary_text.clear()
        self._refresh_status_labels()

    def _refresh_status_labels(self) -> None:
        self.target_host_label.setText(self.config.remote_host)
        self.current_task_label.setText(f"当前任务: {self.current_task_value}")
        self.current_status_label.setText(self.current_status_value)
        self.current_version_label.setText(self.current_version_value)
        self.localhost_status_label.setText(self.current_localhost_status_value)
        self.localhost_url_label.setText(self.current_localhost_url_value)
        self.last_result_label.setText(self.last_result_value)
        self.bottom_status_label.setText(self.bottom_status_value)
        self.last_finished_label.setText(f"最后完成时间: {self.last_finished_value}")
        self._refresh_status_light()

    def _refresh_status_light(self) -> None:
        lowered = self.current_status_value.lower()
        if lowered == "success":
            self.status_light_color = "#16a34a"
        elif lowered == "warning":
            self.status_light_color = "#eab308"
        elif lowered == "failed":
            self.status_light_color = "#dc2626"
        elif lowered == "running":
            self.status_light_color = "#2563eb"
        else:
            self.status_light_color = "#9ca3af"
        self.status_light.setStyleSheet(
            "border-radius: 5px; "
            f"background-color: {self.status_light_color}; "
            "border: 1px solid rgba(15, 23, 42, 0.2);"
        )
        self.current_status_label.setStyleSheet(
            f"color: {self.status_light_color}; font-weight: 600;"
        )
        localhost_enabled = self.current_localhost_status_value == "已开启"
        self.localhost_status_label.setStyleSheet(
            f"color: {'#16a34a' if localhost_enabled else '#64748b'}; font-weight: 600;"
        )

    def _compute_localhost_status(self, localhost_url: str) -> str:
        return "已开启" if localhost_url and localhost_url != "-" else "未开启"

    def open_logs_dir(self) -> None:
        self.config.logs_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["open", str(self.config.logs_dir.resolve())])

    def open_ssh_terminal(self) -> None:
        if sys.platform != "darwin":
            QMessageBox.information(self, "暂不支持", "当前仅支持在 macOS 上自动打开已连接的 SSH 终端。")
            return
        ssh_command = shlex.join([*SSHRunner(self.config).build_ssh_base_command(), self.config.remote_host])
        script_command = ssh_command.replace("\\", "\\\\").replace('"', '\\"')
        apple_script = [
            "osascript",
            "-e",
            'tell application "Terminal" to activate',
            "-e",
            f'tell application "Terminal" to do script "{script_command}"',
        ]
        subprocess.Popen(apple_script)

    def show_official_commands(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("OpenClaw 官方命令")
        dialog.resize(760, 620)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText(OFFICIAL_COMMANDS_REFERENCE)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def open_localhost_url(self) -> None:
        if not self.current_localhost_url_value or self.current_localhost_url_value == "-":
            answer = QMessageBox.question(
                self,
                "开启 localhost 访问",
                "当前未开启 localhost 访问，是否立即开启并打开 WebUI？",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.start_action("打开 localhost WebUI", open_localhost_webui, False)

    def copy_log_path(self) -> None:
        path = self.last_log_path_value
        if not path or path == "-":
            QMessageBox.information(self, "暂无日志", "还没有可复制的日志路径。")
            return
        QGuiApplication.clipboard().setText(path)

    def copy_summary(self) -> None:
        text = self.summary_text.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "暂无摘要", "还没有可复制的摘要内容。")
            return
        QGuiApplication.clipboard().setText(text)


def launch_app(config: AppConfig) -> int:
    app = QApplication.instance()
    owns_app = app is None
    if owns_app:
        app = QApplication(sys.argv)
    if APP_ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    window = OpenClawDesktopApp(config)
    if APP_ICON_PATH.exists():
        window.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    window.show()
    if owns_app:
        return app.exec()
    return 0
