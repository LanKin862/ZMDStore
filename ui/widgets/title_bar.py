from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


class TitleBar(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.drag_start = QPoint()
        self.setObjectName("customTitleBar")
        self.setFixedHeight(48)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 8, 10, 8)
        layout.setSpacing(8)

        self.title_label = QLabel("ZMD Store Desktop")
        self.title_label.setFont(QFont("SimHei", 10))
        self.title_label.setObjectName("titleBarLabel")

        self.min_button = QPushButton("—")
        self.max_button = QPushButton("□")
        self.close_button = QPushButton("×")
        for button in [self.min_button, self.max_button, self.close_button]:
            button.setObjectName("titleBarButton")
            button.setFixedSize(34, 28)

        self.min_button.clicked.connect(self.window.showMinimized)
        self.max_button.clicked.connect(self.toggle_max_restore)
        self.close_button.clicked.connect(self.window.close)

        layout.addWidget(self.title_label)
        layout.addStretch(1)
        layout.addWidget(self.min_button)
        layout.addWidget(self.max_button)
        layout.addWidget(self.close_button)

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
