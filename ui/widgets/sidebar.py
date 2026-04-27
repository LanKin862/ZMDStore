from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QLabel, QPushButton, QVBoxLayout, QWidget


class Sidebar(QWidget):
    page_selected = Signal(int)

    def __init__(self):
        super().__init__()
        self.setObjectName("sidebar")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 20, 18, 20)
        layout.setSpacing(14)

        title = QLabel("ZMD Store")
        title.setObjectName("sidebarTitle")
        subtitle = QLabel("搬运与素材工具")
        subtitle.setObjectName("sidebarSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = []
        for index, text in enumerate(["运送任务", "添加图片"]):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setObjectName("navButton")
            self.group.addButton(button, index)
            layout.addWidget(button)
            self.buttons.append(button)
        layout.addStretch(1)

        self.group.idClicked.connect(self.page_selected.emit)
        self.buttons[0].setChecked(True)

    def set_scale_factor(self, scale: float):
        layout = self.layout()
        if layout is None:
            return
        def px(value: int) -> int:
            return max(1, int(round(value * scale)))
        layout.setContentsMargins(px(18), px(20), px(18), px(20))
        layout.setSpacing(px(14))
