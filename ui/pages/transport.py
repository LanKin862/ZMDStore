from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PIL import Image

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
        self.liquid_path: Path | None = None
        self.container_path: Path | None = None
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
        self.resolution_combo = RoundedComboBox()
        self.times_spin.setRange(1, 999)
        self.times_spin.setValue(30)
        self.hotkey_edit.setKeySequence("F8")
        self.resolution_combo.addItems(["1920x1080", "2560x1440", "3840x2160"])
        self.resolution_combo.setCurrentText("2560x1440")

        top_grid.addWidget(self._field("起点"), 0, 0)
        top_grid.addWidget(self._field("终点"), 0, 1)
        top_grid.addWidget(self._field("次数"), 0, 2)
        top_grid.addWidget(self._field("停止快捷键"), 0, 3)
        top_grid.addWidget(self._field("屏幕分辨率"), 0, 4)
        top_grid.addWidget(self.begin_combo, 1, 0)
        top_grid.addWidget(self.end_combo, 1, 1)
        top_grid.addWidget(self.times_spin, 1, 2)
        top_grid.addWidget(self.hotkey_edit, 1, 3)
        top_grid.addWidget(self.resolution_combo, 1, 4)
        card_layout.addLayout(top_grid)

        item_header = QHBoxLayout()
        item_label = QLabel("选择物品")
        item_label.setObjectName("sectionTitle")
        
        self.liquid_checkbox = QCheckBox("液体运输模式")
        self.liquid_checkbox.toggled.connect(self._on_liquid_toggled)

        item_header.addWidget(item_label)
        item_header.addWidget(self.liquid_checkbox)
        item_header.addStretch(1)

        self.liquid_panel = QFrame()
        self.liquid_panel.setObjectName("subtleCard")
        self.liquid_panel.setVisible(False)
        liquid_layout = QHBoxLayout(self.liquid_panel)
        liquid_layout.setContentsMargins(12, 8, 12, 8)
        
        self.liquid_label = QLabel("液体: (未选择)")
        self.liquid_label.setStyleSheet("font-weight: bold; color: #1a6dff;")
        self.set_liquid_btn = QPushButton("设为液体")
        
        self.container_label = QLabel("容器: (未选择)")
        self.container_label.setStyleSheet("font-weight: bold; color: #1a6dff;")
        self.set_container_btn = QPushButton("设为容器")
        
        for btn in [self.set_liquid_btn, self.set_container_btn]:
            btn.setCursor(Qt.PointingHandCursor)
            btn.setEnabled(False)
            
        liquid_layout.addWidget(self.set_liquid_btn)
        liquid_layout.addWidget(self.liquid_label)
        liquid_layout.addSpacing(30)
        liquid_layout.addWidget(self.set_container_btn)
        liquid_layout.addWidget(self.container_label)
        liquid_layout.addStretch(1)

        self.set_liquid_btn.clicked.connect(self._set_liquid)
        self.set_container_btn.clicked.connect(self._set_container)

        self.image_grid = ImageGrid()
        self.image_grid.setMinimumHeight(400) # 留出足够大的空间
        self.image_grid.item_selected.connect(self._set_item)
        self.image_grid.edit_requested.connect(self.edit_requested.emit)
        self.image_grid.delete_requested.connect(self._request_delete)
        
        card_layout.addLayout(item_header)
        card_layout.addWidget(self.liquid_panel)
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

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setWidget(card)
        root.addWidget(scroll_area)

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
        liquid_mode = self.liquid_checkbox.isChecked()
        if liquid_mode:
            if self.liquid_path and self.container_path:
                try:
                    import time
                    # 将合成图片存入 temp/item 目录，确保 auto_click.py 中的 is_item 判定为 True
                    temp_dir = self.base_dir / "temp" / "item"
                    temp_dir.mkdir(parents=True, exist_ok=True)
                    
                    # 清理旧的合成文件，避免占用空间
                    for old_file in temp_dir.glob("liquid_output_*.png"):
                        try:
                            old_file.unlink()
                        except Exception:
                            pass
                            
                    # 动态生成带时间戳的文件名，绕过 auto_click.py 的 image_obj_cache 全局缓存机制
                    output_path = temp_dir / f"liquid_output_{int(time.time() * 1000)}.png"
                    
                    A_path = str(self.liquid_path)
                    B_path = str(self.container_path)
                    
                    img_A = Image.open(A_path).convert("RGBA")
                    img_B = Image.open(B_path).convert("RGBA")

                    scale = 40 / 88
                    new_width = int(img_A.width * scale)
                    new_height = int(img_A.height * scale)

                    img_A_resized = img_A.resize((new_width, new_height), Image.Resampling.LANCZOS)

                    bg_width, bg_height = img_B.size
                    x_center = (bg_width - new_width) // 2
                    y_center = (bg_height - new_height) // 2

                    img_B.paste(img_A_resized, (x_center, y_center), img_A_resized)
                    img_B.save(output_path, format="PNG")
                    
                    self.append_log(f"已生成液体容器组合图片并存入 temp 目录。")
                    item = str(output_path)
                except Exception as e:
                    show_message(self, "生成失败", f"合成液体组合图片时发生错误：\n{e}")
                    return
            elif self.container_path and not self.liquid_path:
                # 允许只选择容器
                item = str(self.container_path)
            else:
                show_message(self, "液体运输", "请设置【容器】（可同时设置液体）。不能只设液体或全空！")
                return
        else:
            if not self.selected_item:
                show_message(self, "缺少物品", "请先选择一个需要搬运的物品。")
                return
            item = str(self.selected_item)

        if self.begin_combo.currentText() == self.end_combo.currentText():
            show_message(self, "起终点冲突", "起点和终点不能相同。")
            return

        begin = str(self.region_paths[self.begin_combo.currentText()])
        end = str(self.region_paths[self.end_combo.currentText()])
        resolution_str = self.resolution_combo.currentText()
        hotkey_text = self.hotkey_edit.keySequence().toString().strip() or "F8"
        try:
            self.hotkey_manager.register(hotkey_text)
        except Exception as exc:
            show_message(self, "快捷键不可用", str(exc))
            return
        started = self.worker.start(begin, end, item, self.times_spin.value(), resolution_str, liquid_mode=liquid_mode)
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

    def _on_liquid_toggled(self, checked):
        self.liquid_panel.setVisible(checked)
        if not checked:
            self.liquid_path = None
            self.container_path = None
            self.liquid_label.setText("液体: (未选择)")
            self.container_label.setText("容器: (未选择)")

    def _set_liquid(self):
        if self.selected_item:
            self.liquid_path = self.selected_item
            self.liquid_label.setText(f"液体: {self.liquid_path.stem}")
            self.append_log(f"已设为液体: {self.liquid_path.stem}")

    def _set_container(self):
        if self.selected_item:
            self.container_path = self.selected_item
            self.container_label.setText(f"容器: {self.container_path.stem}")
            self.append_log(f"已设为容器: {self.container_path.stem}")

    def _set_item(self, path: Path):
        self.selected_item = path
        if path:
            self.set_liquid_btn.setEnabled(True)
            self.set_container_btn.setEnabled(True)
        else:
            self.set_liquid_btn.setEnabled(False)
            self.set_container_btn.setEnabled(False)
        
        if not self.liquid_checkbox.isChecked():
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
