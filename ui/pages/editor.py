from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.image_utils import choose_image_file, crop_image, crop_image_polygon, scaled_image
from ui.widgets.crop_widget import CropWidget
from ui.widgets.message_dialog import show_message, show_confirm
from ui.widgets.rounded_combo import RoundedComboBox


class EditorPage(QWidget):
    def __init__(self, base_dir: Path, on_saved=None):
        super().__init__()
        self.base_dir = base_dir
        self.on_saved = on_saved
        self.source_path: str | None = None
        self.original_image = QImage()
        self.preview_image = QImage()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)

        card = QFrame()
        card.setObjectName("surfaceCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(18)

        title = QLabel("添加图片")
        title.setObjectName("pageTitle")
        desc = QLabel("可将外部图片导入到 region 或 item，并在保存前完成裁剪、缩放和格式转换。")
        desc.setObjectName("mutedText")
        layout.addWidget(title)
        layout.addWidget(desc)

        top = QHBoxLayout()
        top.setSpacing(12)
        self.type_combo = RoundedComboBox()
        self.type_combo.addItems(["item", "region"])
        self.crop_mode_combo = RoundedComboBox()
        self.crop_mode_combo.addItems(["矩形裁剪", "不规则裁剪"])
        self.format_combo = RoundedComboBox()
        self.format_combo.addItems(["png", "jpg", "webp"])
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("保存文件名，不带后缀")
        self.open_button = QPushButton("选择图片")
        self.open_button.setCursor(Qt.PointingHandCursor)
        top.addWidget(self._field_wrap("类型", self.type_combo))
        top.addWidget(self._field_wrap("裁剪模式", self.crop_mode_combo))
        top.addWidget(self._field_wrap("格式", self.format_combo))
        top.addWidget(self._field_wrap("文件名", self.name_edit), 1)
        top.addWidget(self.open_button)
        layout.addLayout(top)

        res_row = QHBoxLayout()
        res_row.setSpacing(12)
        self.res_label = QLabel("当前分辨率: - x -")
        self.target_w_edit = QLineEdit()
        self.target_w_edit.setPlaceholderText("宽")
        self.target_w_edit.setFixedWidth(80)
        self.target_h_edit = QLineEdit()
        self.target_h_edit.setPlaceholderText("高")
        self.target_h_edit.setFixedWidth(80)
        self.apply_res_button = QPushButton("按分辨率缩放")
        self.apply_res_button.setCursor(Qt.PointingHandCursor)
        
        res_row.addWidget(self.res_label)
        res_row.addStretch(1)
        res_row.addWidget(QLabel("目标分辨率:"))
        res_row.addWidget(self.target_w_edit)
        res_row.addWidget(QLabel("x"))
        res_row.addWidget(self.target_h_edit)
        res_row.addWidget(self.apply_res_button)
        layout.addLayout(res_row)

        slider_row = QHBoxLayout()
        slider_row.setSpacing(12)
        self.scale_label = QLabel("缩放: 100%")
        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setRange(10, 200)
        self.scale_slider.setValue(100)
        slider_row.addWidget(self.scale_label)
        slider_row.addWidget(self.scale_slider, 1)
        layout.addLayout(slider_row)

        self.crop_widget = CropWidget()
        layout.addWidget(self.crop_widget, 1)

        action_row = QHBoxLayout()
        action_row.setSpacing(12)
        self.crop_button = QPushButton("应用裁剪")
        self.reset_button = QPushButton("重置")
        self.save_button = QPushButton("保存图片")
        self.save_button.setObjectName("primaryButton")
        for button in [self.crop_button, self.reset_button, self.save_button]:
            button.setCursor(Qt.PointingHandCursor)
            action_row.addWidget(button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        root.addWidget(card)

        self.open_button.clicked.connect(self.open_image)
        self.crop_mode_combo.currentIndexChanged.connect(self.change_crop_mode)
        self.scale_slider.valueChanged.connect(self.apply_scale)
        self.crop_button.clicked.connect(self.apply_crop)
        self.apply_res_button.clicked.connect(self.apply_target_resolution)
        self.reset_button.clicked.connect(self.reset_editor)
        self.save_button.clicked.connect(self.save_image)
        self.change_crop_mode()

    def open_image(self):
        file_path = choose_image_file(self)
        if not file_path:
            return
        image = QImage(file_path)
        if image.isNull():
            show_message(self, "图片错误", "无法加载所选图片。")
            return
        self.source_path = file_path
        self.original_image = image
        self.preview_image = image
        self.scale_slider.setValue(100)
        self.crop_widget.set_image(self.preview_image)
        if not self.name_edit.text():
            self.name_edit.setText(Path(file_path).stem)
        self._update_res_label()

    def apply_scale(self):
        if self.original_image.isNull():
            return
        factor = self.scale_slider.value() / 100
        self.scale_label.setText(f"缩放: {self.scale_slider.value()}%")
        self.preview_image = scaled_image(self.original_image, factor)
        self.crop_widget.set_image(self.preview_image)
        self._update_res_label()

    def apply_crop(self):
        if self.preview_image.isNull():
            return
        if self.crop_mode_combo.currentText() == "矩形裁剪":
            rect = self.crop_widget.current_selection()
            if rect.isNull():
                show_message(self, "未选择区域", "请先在图片上拖拽选择裁剪区域。")
                return
            self.preview_image = crop_image(self.preview_image, rect)
        else:
            polygon = self.crop_widget.current_polygon()
            if polygon.count() < 3:
                show_message(self, "未完成选区", "请拖动鼠标绘制一个闭合感足够的不规则区域。")
                return
            self.preview_image = crop_image_polygon(self.preview_image, polygon)
        self.original_image = self.preview_image
        self.scale_slider.setValue(100)
        self.crop_widget.set_image(self.preview_image)
        self._update_res_label()

    def reset_editor(self):
        if not self.source_path:
            return
        image = QImage(self.source_path)
        self.original_image = image
        self.preview_image = image
        self.scale_slider.setValue(100)
        self.crop_widget.set_image(self.preview_image)
        self._update_res_label()

    def save_image(self):
        if self.preview_image.isNull():
            show_message(self, "没有图片", "请先加载要添加的图片。")
            return
        name = self.name_edit.text().strip()
        if not name:
            show_message(self, "缺少名称", "请填写保存文件名。")
            return
        target_dir = self.base_dir / self.type_combo.currentText()
        target_dir.mkdir(parents=True, exist_ok=True)
        target_format = self.format_combo.currentText()
        output_path = target_dir / f"{name}.{target_format}"
        
        if output_path.exists():
            if not show_confirm(self, "确认替换", f"目标路径中已存在同名文件:\n{output_path.name}\n是否覆盖替换原文件？"):
                return
                
        save_image = self.preview_image
        if target_format == "jpg" and not self.preview_image.isNull():
            flattened = QImage(self.preview_image.size(), QImage.Format_RGB32)
            flattened.fill(QColor("#ffffff"))
            painter = QPainter(flattened)
            painter.drawImage(0, 0, self.preview_image)
            painter.end()
            save_image = flattened
            
        # 临时加个下划线保存
        temp_path = target_dir / f"_{name}.{target_format}"
        ok = save_image.save(str(temp_path))
        if not ok:
            show_message(self, "保存失败", "图片保存到临时文件失败，请检查格式或路径。")
            return
            
        # 删除原图片后再把保存后的图片文件名改回去
        try:
            if output_path.exists():
                output_path.unlink()
            temp_path.rename(output_path)
        except Exception as e:
            temp_path.unlink(missing_ok=True)
            show_message(self, "替换失败", f"无法替换原图片，可能文件被锁定:\n{e}")
            return
            
        show_message(self, "保存成功", f"已保存到:\n{output_path}")
        if self.on_saved:
            self.on_saved()

    def change_crop_mode(self):
        mode = "rectangle" if self.crop_mode_combo.currentText() == "矩形裁剪" else "freeform"
        self.crop_widget.set_crop_mode(mode)

    def set_scale_factor(self, scale: float):
        self.crop_widget.setMinimumHeight(max(280, int(round(440 * scale))))

    def _update_res_label(self):
        if self.preview_image.isNull():
            self.res_label.setText("当前分辨率: - x -")
        else:
            w, h = self.preview_image.width(), self.preview_image.height()
            self.res_label.setText(f"当前分辨率: {w} x {h}")
            self.target_w_edit.setText(str(w))
            self.target_h_edit.setText(str(h))

    def apply_target_resolution(self):
        if self.preview_image.isNull():
            show_message(self, "没有图片", "请先加载图片。")
            return
        try:
            w = int(self.target_w_edit.text().strip())
            h = int(self.target_h_edit.text().strip())
            if w <= 0 or h <= 0:
                raise ValueError
        except ValueError:
            show_message(self, "输入错误", "请输入有效的目标宽高（必须为正整数）。")
            return
        
        self.preview_image = self.preview_image.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        self.original_image = self.preview_image
        self.scale_slider.blockSignals(True)
        self.scale_slider.setValue(100)
        self.scale_slider.blockSignals(False)
        self.scale_label.setText("缩放: 100%")
        self.crop_widget.set_image(self.preview_image)
        self._update_res_label()

    def load_for_edit(self, file_path: Path):
        image = QImage(str(file_path))
        if image.isNull():
            show_message(self, "图片错误", "无法加载所选图片。")
            return
        self.source_path = str(file_path)
        self.original_image = image
        self.preview_image = image
        self.scale_slider.setValue(100)
        self.crop_widget.set_image(self.preview_image)
        self.name_edit.setText(file_path.stem)
        
        parent_dir = file_path.parent.name
        if parent_dir in ["item", "region"]:
            self.type_combo.setCurrentText(parent_dir)
            
        self._update_res_label()

    def _field_wrap(self, title: str, widget: QWidget):
        frame = QFrame()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        label = QLabel(title)
        label.setObjectName("fieldLabel")
        layout.addWidget(label)
        layout.addWidget(widget)
        return frame
