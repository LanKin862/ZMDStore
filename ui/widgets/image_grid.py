from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QMenu

from core.image_utils import make_thumbnail


class ImageGrid(QListWidget):
    item_selected = Signal(Path)
    edit_requested = Signal(Path)
    delete_requested = Signal(Path)

    def __init__(self):
        super().__init__()
        self.setViewMode(QListWidget.IconMode)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setSpacing(6)
        self.setIconSize(QSize(76, 76))
        self.setUniformItemSizes(True)
        self.setWordWrap(True)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.itemSelectionChanged.connect(self._emit_selection)
        
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
    def set_items(self, paths: list[Path]):
        self.clear()
        
        # 动态获取当前已配置的图标大小来实现自适应（类似 CSS flex 计算）
        curr_icon = self.iconSize().width()
        curr_h = curr_icon + int(curr_icon / 3)
        curr_w = curr_h
        
        for path in paths:
            item = QListWidgetItem(QIcon(make_thumbnail(path)), path.stem)
            item.setData(Qt.UserRole, str(path))
            item.setSizeHint(QSize(curr_w, curr_h))
            self.addItem(item)

        if self.count():
            self.setCurrentRow(0)

    def current_path(self) -> Path | None:
        item = self.currentItem()
        if not item:
            return None
        return Path(item.data(Qt.UserRole))

    def _show_context_menu(self, pos):
        item = self.itemAt(pos)
        if not item:
            return
        path = Path(item.data(Qt.UserRole))
        menu = QMenu(self)
        
        menu.setStyleSheet("""
            QMenu { background-color: white; border: 1px solid #dce7f3; border-radius: 4px; padding: 4px; }
            QMenu::item { padding: 6px 24px; color: #17324d; font-size: 14px; border-radius: 4px; }
            QMenu::item:selected { background-color: #eff6ff; color: #1a6dff; }
        """)
        
        edit_action = menu.addAction("编辑")
        delete_action = menu.addAction("删除")
        
        action = menu.exec(self.mapToGlobal(pos))
        if action == edit_action:
            self.edit_requested.emit(path)
        elif action == delete_action:
            self.delete_requested.emit(path)

    def _emit_selection(self):
        path = self.current_path()
        if path:
            self.item_selected.emit(path)

    def set_scale_factor(self, scale: float):
        # 你可以尽情修改基础 icon 大小，后面都会自动自适应！
        icon = max(40, int(round(80 * scale)))
        
        # Flex式计算：高度的后 1/4 留给文字（icon/3），前 3/4 给图标
        item_h = icon + int(icon / 3)
        # 统一宽度和高度，变成完美的正方形背景框
        item_w = item_h
        
        spacing = max(4, int(round(6 * scale)))
        self.setSpacing(spacing)
        self.setIconSize(QSize(icon, icon))
        
        # 将数学公式直接应用到所有现有 item 框上
        for index in range(self.count()):
            self.item(index).setSizeHint(QSize(item_w, item_h))
