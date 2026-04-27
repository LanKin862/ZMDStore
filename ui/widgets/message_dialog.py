from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


class MessageDialog(QDialog):
    def __init__(self, parent, title: str, message: str, button_text: str = "确定"):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setModal(True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setObjectName("messageDialog")
        self.setMinimumWidth(360)

        font = QFont("SimHei", 10)
        self.setFont(font)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        card = QFrame()
        card.setObjectName("messageCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        title_label = QLabel(title)
        title_label.setObjectName("messageDialogTitle")
        title_label.setFont(QFont("SimHei", 12))
        message_label = QLabel(message)
        message_label.setObjectName("messageDialogBody")
        message_label.setWordWrap(True)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        button = QPushButton(button_text)
        button.setObjectName("messageDialogButton")
        button.clicked.connect(self.accept)
        action_row.addWidget(button)

        layout.addWidget(title_label)
        layout.addWidget(message_label)
        layout.addLayout(action_row)
        outer.addWidget(card)


def show_message(parent, title: str, message: str):
    dialog = MessageDialog(parent, title, message)
    dialog.exec()

class ConfirmDialog(QDialog):
    def __init__(self, parent, title: str, message: str):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setModal(True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setObjectName("messageDialog")
        self.setMinimumWidth(360)

        font = QFont("SimHei", 10)
        self.setFont(font)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        card = QFrame()
        card.setObjectName("messageCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        title_label = QLabel(title)
        title_label.setObjectName("messageDialogTitle")
        title_label.setFont(QFont("SimHei", 12))
        message_label = QLabel(message)
        message_label.setObjectName("messageDialogBody")
        message_label.setWordWrap(True)

        action_row = QHBoxLayout()
        action_row.setSpacing(12)
        action_row.addStretch(1)
        cancel_button = QPushButton("取消")
        cancel_button.setObjectName("messageDialogButton")
        cancel_button.clicked.connect(self.reject)
        
        ok_button = QPushButton("确认")
        ok_button.setObjectName("primaryButton")
        ok_button.setMinimumWidth(88)
        ok_button.setStyleSheet("padding: 8px 14px;")
        ok_button.clicked.connect(self.accept)
        
        action_row.addWidget(cancel_button)
        action_row.addWidget(ok_button)

        layout.addWidget(title_label)
        layout.addWidget(message_label)
        layout.addLayout(action_row)
        outer.addWidget(card)

def show_confirm(parent, title: str, message: str) -> bool:
    dialog = ConfirmDialog(parent, title, message)
    return dialog.exec() == QDialog.Accepted
