from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.hotkey import GlobalHotkeyManager
from core.image_utils import list_item_paths
from core.worker import WorkerHandle
from ui.widgets.image_grid import ImageGrid
from ui.widgets.message_dialog import show_message, show_confirm
from ui.widgets.rounded_combo import RoundedComboBox


class TransportPage(QWidget):
    request_minimize = Signal()
    edit_requested = Signal(Path)

    def __init__(self, base_dir: Path):
        super().__init__()
        self.base_dir = base_dir
        self.item_dir = base_dir / "item"
        self.region_dir = base_dir / "region"
        self.worker = WorkerHandle()
        self.worker.log.connect(self.append_log)
        self.worker.finished.connect(self.on_finished)
        self.worker.error.connect(self.on_error)

        self.selected_item: Path | None = None
        self.regions: list[str] = []
        self.region_paths: dict[str, Path] = {}
        self.hotkey_manager = GlobalHotkeyManager()
        self.hotkey_manager.activated.connect(self.on_hotkey_stop)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)

        card = QFrame()
        card.setObjectName("surfaceCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(22, 22, 22, 22)
        card_layout.setSpacing(18)

        heading = QLabel("运送任务")
        heading.setObjectName("pageTitle")
        tip = QLabel("选择起点、终点、次数和物品图片后开始搬运。")
        tip.setObjectName("mutedText")
        card_layout.addWidget(heading)
        card_layout.addWidget(tip)

        top_grid = QGridLayout()
        top_grid.setHorizontalSpacing(16)
        top_grid.setVerticalSpacing(12)

        self.begin_combo = RoundedComboBox()
        self.end_combo = RoundedComboBox()
        self.times_spin = QSpinBox()
        self.hotkey_edit = QKeySequenceEdit()
        self.times_spin.setRange(1, 999)
        self.times_spin.setValue(30)
        self.hotkey_edit.setKeySequence("F8")

        top_grid.addWidget(self._field("起点"), 0, 0)
        top_grid.addWidget(self._field("终点"), 0, 1)
        top_grid.addWidget(self._field("次数"), 0, 2)
        top_grid.addWidget(self._field("停止快捷键"), 0, 3)
        top_grid.addWidget(self.begin_combo, 1, 0)
        top_grid.addWidget(self.end_combo, 1, 1)
        top_grid.addWidget(self.times_spin, 1, 2)
        top_grid.addWidget(self.hotkey_edit, 1, 3)
        card_layout.addLayout(top_grid)

        item_label = QLabel("选择物品")
        item_label.setObjectName("sectionTitle")
        self.image_grid = ImageGrid()
        self.image_grid.item_selected.connect(self._set_item)
        self.image_grid.edit_requested.connect(self.edit_requested.emit)
        self.image_grid.delete_requested.connect(self._request_delete)
        card_layout.addWidget(item_label)
        card_layout.addWidget(self.image_grid, 1)

        action_row = QHBoxLayout()
        action_row.setSpacing(12)
        self.refresh_button = QPushButton("刷新资源")
        self.start_button = QPushButton("开始搬运")
        self.start_button.setObjectName("primaryButton")
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        for button in [self.refresh_button, self.start_button, self.stop_button]:
            button.setCursor(Qt.PointingHandCursor)
            action_row.addWidget(button)
        action_row.addStretch(1)
        card_layout.addLayout(action_row)

        log_card = QFrame()
        log_card.setObjectName("subtleCard")
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(14, 14, 14, 14)
        log_layout.setSpacing(8)
        log_layout.addWidget(QLabel("运行日志"))
        self.log_output = QTextEdit()
        self.log_output.setObjectName("logOutput")
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(150)
        log_layout.addWidget(self.log_output)
        card_layout.addWidget(log_card)

        root.addWidget(card)

        self.refresh_button.clicked.connect(self.reload_data)
        self.start_button.clicked.connect(self.start_transport)
        self.stop_button.clicked.connect(self.stop_transport)
        self.begin_combo.currentIndexChanged.connect(self._sync_region_choices)
        self.end_combo.currentIndexChanged.connect(self._sync_region_choices)

        self.reload_data()

    def reload_data(self):
        self.region_paths = {}
        for suffix in [".png", ".jpg", ".jpeg", ".bmp", ".webp"]:
            for path in sorted(self.region_dir.glob(f"*{suffix}")):
                if path.stem.startswith("already_in_"):
                    continue
                self.region_paths[path.stem] = path
        self.regions = list(self.region_paths.keys())
        items = list_item_paths(self.item_dir)

        self.begin_combo.blockSignals(True)
        self.end_combo.blockSignals(True)
        self.begin_combo.clear()
        self.end_combo.clear()
        self.begin_combo.addItems(self.regions)
        self.end_combo.addItems(self.regions)
        if len(self.regions) > 1:
            self.end_combo.setCurrentIndex(1)
        self.begin_combo.blockSignals(False)
        self.end_combo.blockSignals(False)
        self._sync_region_choices()

        self.image_grid.set_items(items)
        self.selected_item = self.image_grid.current_path()
        self.append_log("已刷新 item / region 资源。")

    def start_transport(self):
        if not self.selected_item:
            show_message(self, "缺少物品", "请先选择一个需要搬运的物品。")
            return
        if self.begin_combo.currentText() == self.end_combo.currentText():
            show_message(self, "起终点冲突", "起点和终点不能相同。")
            return

        begin = str(self.region_paths[self.begin_combo.currentText()])
        end = str(self.region_paths[self.end_combo.currentText()])
        item = str(self.selected_item)
        hotkey_text = self.hotkey_edit.keySequence().toString().strip() or "F8"
        try:
            self.hotkey_manager.register(hotkey_text)
        except Exception as exc:
            show_message(self, "快捷键不可用", str(exc))
            return
        started = self.worker.start(begin, end, item, self.times_spin.value())
        if not started:
            self.hotkey_manager.unregister()
            show_message(self, "任务运行中", "当前已有搬运任务在执行。")
            return

        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.append_log(f"任务开始，窗口将自动最小化。停止快捷键: {hotkey_text}")
        self.request_minimize.emit()

    def stop_transport(self):
        self.worker.stop()
        self.append_log("已发送停止请求，等待当前步骤结束。")

    def on_finished(self, message: str):
        self.hotkey_manager.unregister()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.append_log(message)

    def on_error(self, message: str):
        self.hotkey_manager.unregister()
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.append_log(message)
        show_message(self, "搬运任务异常", message)

    def on_hotkey_stop(self):
        if not self.worker.is_running():
            return
        self.stop_transport()

    def append_log(self, message: str):
        self.log_output.append(message)

    def _set_item(self, path: Path):
        self.selected_item = path
        self.append_log(f"已选择物品: {path.stem}")

    def _request_delete(self, path: Path):
        if show_confirm(self, "确认删除", f"确定要删除图片 {path.name} 吗？\n注意：如果该图片是区域图片，可能会影响其他关联记录。"):
            try:
                path.unlink(missing_ok=True)
                self.append_log(f"已删除图片: {path.name}")
                self.reload_data()
            except Exception as e:
                show_message(self, "删除失败", str(e))

    def _field(self, title: str) -> QLabel:
        label = QLabel(title)
        label.setObjectName("fieldLabel")
        return label

    def _sync_region_choices(self):
        if self.begin_combo.currentText() == self.end_combo.currentText() and len(self.regions) > 1:
            new_index = (self.end_combo.currentIndex() + 1) % len(self.regions)
            if new_index == self.begin_combo.currentIndex():
                new_index = (new_index + 1) % len(self.regions)
            self.end_combo.blockSignals(True)
            self.end_combo.setCurrentIndex(new_index)
            self.end_combo.blockSignals(False)

    def set_scale_factor(self, scale: float):
        self.image_grid.set_scale_factor(scale)
