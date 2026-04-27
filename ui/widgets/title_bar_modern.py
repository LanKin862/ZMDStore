from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


class TitleBar(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.drag_start = QPoint()
        self.setObjectName("customTitleBar")
        self.setFixedHeight(48)
        self.setAttribute(Qt.WA_StyledBackground, True)

        self.layout_ref = QHBoxLayout(self)
        self.layout_ref.setContentsMargins(18, 8, 10, 8)
        self.layout_ref.setSpacing(8)

        self.title_label = QLabel("ZMD Store Desktop")
        self.title_label.setObjectName("titleBarLabel")
        self.title_label.setFont(QFont("Microsoft YaHei UI", 10))

        self.min_button = QPushButton("-")
        self.max_button = QPushButton("[]")
        self.close_button = QPushButton("x")
        for button in [self.min_button, self.max_button, self.close_button]:
            button.setObjectName("titleBarButton")
            button.setFixedSize(34, 28)

        self.min_button.clicked.connect(self.window.showMinimized)
        self.max_button.clicked.connect(self.toggle_max_restore)
        self.close_button.clicked.connect(self.window.close)

        self.layout_ref.addWidget(self.title_label)
        self.layout_ref.addStretch(1)
        self.layout_ref.addWidget(self.min_button)
        self.layout_ref.addWidget(self.max_button)
        self.layout_ref.addWidget(self.close_button)

    def toggle_max_restore(self):
        if self.window.isMaximized():
            self.window.showNormal()
        else:
            self.window.showMaximized()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_start = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and not self.window.isMaximized():
            self.window.move(event.globalPosition().toPoint() - self.drag_start)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle_max_restore()

    def set_scale(self, scale: float):
        def px(value: int) -> int:
            return max(1, int(round(value * scale)))

        self.setFixedHeight(px(48))
        self.layout_ref.setContentsMargins(px(18), px(8), px(10), px(8))
        self.layout_ref.setSpacing(px(8))
        self.title_label.setFont(QFont("Microsoft YaHei UI", px(10)))
        for button in [self.min_button, self.max_button, self.close_button]:
            button.setFixedSize(px(34), px(28))
