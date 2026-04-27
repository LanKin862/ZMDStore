from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from ctypes import wintypes
from string import Template

from PySide6.QtCore import QEasingCurve, QPoint, QParallelAnimationGroup, QPropertyAnimation, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QHBoxLayout, QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from ui.pages.editor import EditorPage
from ui.pages.transport import TransportPage
from ui.widgets.resize_grip import ResizeGrip
from ui.widgets.sidebar import Sidebar
from ui.widgets.title_bar_modern import TitleBar


BASE_DIR = Path(__file__).resolve().parent
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BASE_DIR
WM_NCHITTEST = 0x0084
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17


class NativePoint(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class NativeMSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", NativePoint),
    ]


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    if getattr(sys, "frozen", False):
        executable = sys.executable
        args = sys.argv[1:]
    else:
        executable = sys.executable
        args = [str(BASE_DIR / "ui_main.py"), *sys.argv[1:]]
    params = " ".join([f'"{arg}"' for arg in args])
    ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, None, 1)


class AnimatedStack(QStackedWidget):
    def __init__(self):
        super().__init__()
        self.anim_group: QParallelAnimationGroup | None = None

    def slide_to(self, index: int):
        if index == self.currentIndex():
            return

        current = self.currentWidget()
        next_widget = self.widget(index)
        if current is None or next_widget is None:
            self.setCurrentIndex(index)
            return

        direction = 1 if index > self.currentIndex() else -1
        width = self.width()

        next_widget.setGeometry(0, 0, width, self.height())
        next_widget.move(direction * width, 0)
        next_widget.show()
        next_widget.raise_()

        current_effect = QGraphicsOpacityEffect(current)
        next_effect = QGraphicsOpacityEffect(next_widget)
        current.setGraphicsEffect(current_effect)
        next_widget.setGraphicsEffect(next_effect)
        next_effect.setOpacity(0.0)

        current_anim = QPropertyAnimation(current, b"pos")
        current_anim.setDuration(260)
        current_anim.setStartValue(QPoint(0, 0))
        current_anim.setEndValue(QPoint(-direction * max(120, width // 5), 0))
        current_anim.setEasingCurve(QEasingCurve.OutCubic)

        next_anim = QPropertyAnimation(next_widget, b"pos")
        next_anim.setDuration(320)
        next_anim.setStartValue(QPoint(direction * width, 0))
        next_anim.setEndValue(QPoint(0, 0))
        next_anim.setEasingCurve(QEasingCurve.OutCubic)

        fade_out = QPropertyAnimation(current_effect, b"opacity")
        fade_out.setDuration(220)
        fade_out.setStartValue(1.0)
        fade_out.setEndValue(0.0)

        fade_in = QPropertyAnimation(next_effect, b"opacity")
        fade_in.setDuration(280)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)

        self.anim_group = QParallelAnimationGroup(self)
        for anim in [current_anim, next_anim, fade_out, fade_in]:
            self.anim_group.addAnimation(anim)

        def finish():
            self.setCurrentIndex(index)
            current.move(0, 0)
            next_widget.move(0, 0)
            current.setGraphicsEffect(None)
            next_widget.setGraphicsEffect(None)

        self.anim_group.finished.connect(finish)
        self.anim_group.start()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("ZMD Store Desktop")
        self.resize(1380, 860)
        self.setMinimumSize(1200, 760)
        self.base_short_side = 760
        self.resize_margin = 8

        wrapper = QWidget()
        wrapper.setObjectName("windowShell")
        wrapper.setAttribute(Qt.WA_StyledBackground, True)
        self.setCentralWidget(wrapper)

        outer = QVBoxLayout(wrapper)
        self.outer_layout = outer
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.title_bar = TitleBar(self)
        outer.addWidget(self.title_bar)

        content = QWidget()
        content.setObjectName("windowContent")
        content.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(content)
        self.content_layout = layout
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(18)

        self.sidebar = Sidebar()
        self.sidebar.setFixedWidth(250)
        layout.addWidget(self.sidebar)

        self.stack = AnimatedStack()
        self.transport_page = TransportPage(APP_DIR)
        self.editor_page = EditorPage(APP_DIR, on_saved=self.transport_page.reload_data)
        self.stack.addWidget(self.transport_page)
        self.stack.addWidget(self.editor_page)
        layout.addWidget(self.stack, 1)
        outer.addWidget(content, 1)

        self.sidebar.page_selected.connect(self.stack.slide_to)
        self.transport_page.request_minimize.connect(self.showMinimized)
        self.transport_page.edit_requested.connect(self.handle_edit_request)
        self._create_resize_grips()

        self.apply_styles()

    def handle_edit_request(self, path: Path):
        self.stack.slide_to(1)
        self.sidebar.group.button(1).setChecked(True)
        self.editor_page.load_for_edit(path)

    def apply_styles(self):
        scale = max(0.9, min(1.45, min(self.width(), self.height()) / self.base_short_side))
        def px(value: int) -> int:
            return max(1, int(round(value * scale)))

        shell_radius = px(20)
        sidebar_radius = px(24)
        card_radius = px(28)
        input_radius = px(16)
        list_radius = px(20)
        title_size = px(30)
        button_font = px(15)
        body_font = px(14)
        heading_font = px(28)
        section_font = px(17)
        small_font = px(13)
        resize_margin = px(6)
        self.resize_margin = resize_margin
        self.title_bar.set_scale(scale)
        self.sidebar.setFixedWidth(px(250))
        self.sidebar.set_scale_factor(scale)
        self.transport_page.set_scale_factor(scale)
        self.editor_page.set_scale_factor(scale)
        self.content_layout.setContentsMargins(px(18), px(18), px(18), px(18))
        self.content_layout.setSpacing(px(18))

        style = Template(
            """
            QWidget {
                background: #f5f7fb;
                color: #17324d;
                font-family: "Microsoft YaHei UI", "PingFang SC", "Segoe UI";
                font-size: ${body_font}px;
            }
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #eef4ff, stop:0.45 #f7fbff, stop:1 #edf5ff);
            }
            #windowShell {
                background: #ffffff;
                border-radius: ${shell_radius}px;
            }
            #windowContent {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #eef4ff, stop:0.45 #f7fbff, stop:1 #edf5ff);
                border-bottom-left-radius: ${shell_radius}px;
                border-bottom-right-radius: ${shell_radius}px;
                margin-top: 0px;
            }
            #customTitleBar {
                background: #ffffff;
                border-top-left-radius: ${shell_radius}px;
                border-top-right-radius: ${shell_radius}px;
                border-bottom: none;
            }
            #titleBarLabel {
                background: transparent;
                color: #17324d;
                font-size: ${small_font}px;
                font-weight: 500;
            }
            #titleBarButton {
                background: #ffffff;
                color: #17324d;
                border: 1px solid #dce7f3;
                border-radius: ${title_button_radius}px;
                font-size: ${body_font}px;
            }
            #titleBarButton:hover {
                background: #edf4fb;
                border: none;
            }
            #sidebar {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #17304d, stop:1 #21456d);
                border-radius: ${sidebar_radius}px;
            }
            #sidebarTitle {
                background: transparent;
                color: #17324d;
                border: none;
                padding: ${sidebar_title_pad_y}px 0 ${sidebar_title_pad_bottom}px 0;
                font-size: ${title_size}px;
                font-weight: 600;
            }
            #sidebarSubtitle {
                background: transparent;
                color: rgba(244,248,255,0.76);
                font-size: ${small_font}px;
                margin-bottom: ${subtitle_margin}px;
            }
            #navButton {
                text-align: left;
                padding: ${nav_pad_y}px ${nav_pad_x}px;
                border: 1px solid rgba(13,33,56,0.10);
                border-radius: ${nav_radius}px;
                background: #ffffff;
                color: #17324d;
                font-size: ${button_font}px;
                font-weight: 500;
            }
            #navButton:hover {
                background: #f6faff;
                border-color: #d4e3f3;
            }
            #navButton:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #1a6dff, stop:1 #3097ff);
                border-color: #1a78ff;
                color: #ffffff;
                font-weight: 700;
            }
            #surfaceCard, #subtleCard {
                border-radius: ${card_radius}px;
                border: 1px solid rgba(19, 52, 83, 0.08);
            }
            #surfaceCard {
                background: rgba(255,255,255,0.96);
            }
            #subtleCard {
                background: #f7faff;
            }
            #pageTitle {
                background: transparent;
                color: #183654;
                font-size: ${heading_font}px;
                font-weight: 600;
            }
            #sectionTitle {
                background: transparent;
                color: #183654;
                font-size: ${section_font}px;
                font-weight: 600;
            }
            #mutedText, #fieldLabel {
                background: transparent;
                color: #64809b;
                font-size: ${small_font}px;
            }
            QPushButton, QComboBox, QSpinBox, QLineEdit, QTextEdit, QKeySequenceEdit {
                border-radius: ${input_radius}px;
                border: 1px solid #d7e3f0;
                background: #ffffff;
                padding: ${input_pad_y}px ${input_pad_x}px;
                selection-background-color: #dcecff;
                selection-color: #17324d;
            }
            QPushButton, QKeySequenceEdit {
                color: #17324d;
                font-weight: 500;
            }
            QPushButton:hover, QComboBox:hover, QSpinBox:hover, QLineEdit:hover, QTextEdit:hover, QKeySequenceEdit:hover {
                border-color: #9dc1e8;
            }
            QPushButton:focus, QComboBox:focus, QSpinBox:focus, QLineEdit:focus, QTextEdit:focus, QKeySequenceEdit:focus {
                border: 1px solid #6ca7e7;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 34px;
                border: none;
                background: transparent;
            }
            QComboBox QAbstractItemView {
                background: #ffffff;
                color: #17324d;
                border: 1px solid #d7e3f0;
                border-radius: ${input_radius}px;
                outline: 0;
                selection-background-color: #eaf3ff;
                selection-color: #17324d;
                padding: ${combo_view_pad}px;
            }
            QComboBoxPrivateContainer {
                border: none;
                background: transparent;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: ${spin_button_width}px;
                border: none;
                background: transparent;
            }
            #primaryButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #1a6dff, stop:1 #3097ff);
                color: white;
                border: 1px solid #1a78ff;
                font-weight: 600;
            }
            #primaryButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #1564ee, stop:1 #238df7);
            }
            QListWidget {
                background: #fbfdff;
                border: 1px solid #d7e3f0;
                border-radius: ${list_radius}px;
                padding: ${list_pad}px;
                outline: 0;
            }
            QListWidget::item {
                border-radius: ${list_item_radius}px;
                padding: ${list_item_pad}px;
                margin: 2px;
                background: #ffffff;
                border: 1px solid #edf2f8;
            }
            QListWidget::item:selected {
                border: 1px solid #78aff0;
                background: transparent;
                color: #17324d;
            }
            QTextEdit#logOutput {
                background: #ffffff;
                color: #28435d;
                border: 1px solid #d7e3f0;
                line-height: 1.5;
            }
            QScrollBar:vertical {
                background: transparent;
                width: ${scroll_width}px;
                margin: ${scroll_margin}px 0 ${scroll_margin}px 0;
            }
            QScrollBar::handle:vertical {
                background: #c8d8ea;
                min-height: ${scroll_handle_min}px;
                border-radius: ${scroll_handle_radius}px;
            }
            QScrollBar::handle:vertical:hover {
                background: #a9c4e2;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
            QScrollBar:horizontal, QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
                background: transparent;
                border: none;
            }
            #cropCanvas {
                background: #ffffff;
                border: 1px solid #d7e3f0;
                border-radius: ${crop_radius}px;
            }
            #messageCard {
                background: #f7fbff;
                border-radius: ${message_radius}px;
                border: 1px solid #dce7f3;
            }
            #messageDialogTitle {
                color: #17324d;
                background: transparent;
                font-size: ${section_font}px;
                font-weight: 600;
            }
            #messageDialogBody {
                color: #17324d;
                background: transparent;
                min-width: ${message_min_width}px;
                font-size: ${body_font}px;
            }
            #messageDialogButton {
                min-width: ${message_button_min}px;
                padding: ${message_button_pad_y}px ${message_button_pad_x}px;
            }
            """
        ).substitute(
            body_font=body_font,
            shell_radius=shell_radius,
            small_font=small_font,
            title_button_radius=px(10),
            sidebar_radius=sidebar_radius,
            sidebar_title_pad_y=px(10),
            title_size=title_size,
            subtitle_margin=px(12),
            sidebar_title_pad_bottom=px(2),
            nav_pad_y=px(16),
            nav_pad_x=px(18),
            nav_radius=px(18),
            button_font=button_font,
            card_radius=card_radius,
            heading_font=heading_font,
            section_font=section_font,
            input_radius=input_radius,
            input_pad_y=px(10),
            input_pad_x=px(12),
            combo_view_pad=px(6),
            spin_button_width=px(22),
            list_radius=list_radius,
            list_pad=px(8),
            list_item_radius=px(8),
            list_item_pad=px(2),
            scroll_width=px(12),
            scroll_margin=px(8),
            scroll_handle_min=px(36),
            scroll_handle_radius=px(6),
            crop_radius=px(22),
            message_radius=px(22),
            message_min_width=px(260),
            message_button_min=px(88),
            message_button_pad_y=px(8),
            message_button_pad_x=px(14),
        )
        self.setStyleSheet(style)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.apply_styles()
        self._layout_resize_grips()

    def nativeEvent(self, event_type, message):
        return super().nativeEvent(event_type, message)

    def _create_resize_grips(self):
        self.resize_grips = {
            "left": ResizeGrip(self, Qt.LeftEdge),
            "right": ResizeGrip(self, Qt.RightEdge),
            "top": ResizeGrip(self, Qt.TopEdge),
            "bottom": ResizeGrip(self, Qt.BottomEdge),
            "top_left": ResizeGrip(self, Qt.TopEdge | Qt.LeftEdge),
            "top_right": ResizeGrip(self, Qt.TopEdge | Qt.RightEdge),
            "bottom_left": ResizeGrip(self, Qt.BottomEdge | Qt.LeftEdge),
            "bottom_right": ResizeGrip(self, Qt.BottomEdge | Qt.RightEdge),
        }
        self._layout_resize_grips()

    def _layout_resize_grips(self):
        if self.isMaximized():
            for grip in self.resize_grips.values():
                grip.hide()
            return

        margin = getattr(self, "resize_margin", 8)
        corner = max(margin * 3, 16)
        width = self.width()
        height = self.height()

        self.resize_grips["left"].setGeometry(0, corner, margin, height - corner * 2)
        self.resize_grips["right"].setGeometry(width - margin, corner, margin, height - corner * 2)
        self.resize_grips["top"].setGeometry(corner, 0, width - corner * 2, margin)
        self.resize_grips["bottom"].setGeometry(corner, height - margin, width - corner * 2, margin)
        self.resize_grips["top_left"].setGeometry(0, 0, corner, corner)
        self.resize_grips["top_right"].setGeometry(width - corner, 0, corner, corner)
        self.resize_grips["bottom_left"].setGeometry(0, height - corner, corner, corner)
        self.resize_grips["bottom_right"].setGeometry(width - corner, height - corner, corner, corner)

        for grip in self.resize_grips.values():
            grip.show()
            grip.raise_()


def main():
    if sys.platform.startswith("win") and not is_admin():
        relaunch_as_admin()
        return

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("ZMD Store Desktop")
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
