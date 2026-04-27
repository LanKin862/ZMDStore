from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QListView


class RoundedComboBox(QComboBox):
    def __init__(self):
        super().__init__()
        view = QListView()
        view.setWindowFlag(Qt.FramelessWindowHint, True)
        view.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setView(view)

    def showPopup(self):
        container = self.view().window()
        container.setWindowFlag(Qt.FramelessWindowHint, True)
        container.setAttribute(Qt.WA_TranslucentBackground, True)
        super().showPopup()
