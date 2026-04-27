from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygon
from PySide6.QtWidgets import QWidget


class CropWidget(QWidget):
    selection_changed = Signal(QRect)
    polygon_changed = Signal(QPolygon)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(440)
        self.setObjectName("cropCanvas")
        self.image = QImage()
        self.display_pixmap = QPixmap()
        self.origin = QPoint()
        self.current = QPoint()
        self.selection = QRect()
        self.freeform_points: list[QPoint] = []
        self.image_rect = QRect()
        self.dragging = False
        self.crop_mode = "rectangle"

    def set_image(self, image: QImage):
        self.image = image
        self.selection = QRect()
        self.freeform_points = []
        self._update_scaled_pixmap()
        self.update()

    def set_crop_mode(self, mode: str):
        self.crop_mode = mode
        self.selection = QRect()
        self.freeform_points = []
        self.dragging = False
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_scaled_pixmap()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        if self.display_pixmap.isNull():
            return

        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.drawPixmap(self.image_rect, self.display_pixmap)
        if self.crop_mode == "rectangle":
            if self.selection.isNull():
                return
            mapped = self._image_to_view_rect(self.selection)
            painter.fillRect(mapped, QColor(17, 126, 255, 36))
            painter.setPen(QPen(QColor("#117eff"), 2, Qt.SolidLine))
            painter.drawRoundedRect(mapped, 12, 12)
            return

        if len(self.freeform_points) < 2:
            return

        view_polygon = self._image_to_view_polygon(self.current_polygon())
        path = QPainterPath()
        path.addPolygon(view_polygon)
        painter.fillPath(path, QColor(17, 126, 255, 30))
        painter.setPen(QPen(QColor("#117eff"), 2, Qt.SolidLine))
        painter.drawPolygon(view_polygon)

    def mousePressEvent(self, event):
        point = event.position().toPoint()
        if self.image.isNull() or not self.image_rect.contains(point):
            return
        self.dragging = True
        if self.crop_mode == "rectangle":
            self.origin = point
            self.current = point
            self.selection = self._view_to_image_rect(QRect(self.origin, self.current).normalized())
            self.selection_changed.emit(self.selection)
        else:
            self.freeform_points = [point]
            self.polygon_changed.emit(self.current_polygon())
        self.update()

    def mouseMoveEvent(self, event):
        if not self.dragging:
            return
        point = event.position().toPoint()
        if self.crop_mode == "rectangle":
            self.current = point
            rect = QRect(self.origin, self.current).normalized().intersected(self.image_rect)
            self.selection = self._view_to_image_rect(rect)
            self.selection_changed.emit(self.selection)
        elif self.image_rect.contains(point):
            self.freeform_points.append(point)
            self.polygon_changed.emit(self.current_polygon())
        self.update()

    def mouseReleaseEvent(self, event):
        self.dragging = False
        if self.crop_mode == "freeform" and len(self.freeform_points) > 2:
            self.polygon_changed.emit(self.current_polygon())
        self.origin = QPoint()
        self.current = QPoint()

    def current_selection(self) -> QRect:
        return self.selection

    def current_polygon(self) -> QPolygon:
        return self._view_to_image_polygon(QPolygon(self.freeform_points))

    def _update_scaled_pixmap(self):
        if self.image.isNull():
            self.display_pixmap = QPixmap()
            self.image_rect = QRect()
            return

        self.display_pixmap = QPixmap.fromImage(
            self.image.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        x = (self.width() - self.display_pixmap.width()) // 2
        y = (self.height() - self.display_pixmap.height()) // 2
        self.image_rect = QRect(x, y, self.display_pixmap.width(), self.display_pixmap.height())

    def _view_to_image_rect(self, rect: QRect) -> QRect:
        if self.image_rect.isNull() or rect.isNull():
            return QRect()
        x_ratio = self.image.width() / self.image_rect.width()
        y_ratio = self.image.height() / self.image_rect.height()
        left = int((rect.left() - self.image_rect.left()) * x_ratio)
        top = int((rect.top() - self.image_rect.top()) * y_ratio)
        width = int(rect.width() * x_ratio)
        height = int(rect.height() * y_ratio)
        return QRect(left, top, width, height).intersected(self.image.rect())

    def _image_to_view_rect(self, rect: QRect) -> QRect:
        if self.image.isNull() or self.image_rect.isNull() or rect.isNull():
            return QRect()
        x_ratio = self.image_rect.width() / self.image.width()
        y_ratio = self.image_rect.height() / self.image.height()
        left = int(rect.left() * x_ratio) + self.image_rect.left()
        top = int(rect.top() * y_ratio) + self.image_rect.top()
        width = int(rect.width() * x_ratio)
        height = int(rect.height() * y_ratio)
        return QRect(left, top, width, height)

    def _view_to_image_polygon(self, polygon: QPolygon) -> QPolygon:
        if self.image_rect.isNull() or polygon.isEmpty():
            return QPolygon()
        x_ratio = self.image.width() / self.image_rect.width()
        y_ratio = self.image.height() / self.image_rect.height()
        points = []
        for point in polygon:
            clipped_x = min(max(point.x(), self.image_rect.left()), self.image_rect.right())
            clipped_y = min(max(point.y(), self.image_rect.top()), self.image_rect.bottom())
            x = int((clipped_x - self.image_rect.left()) * x_ratio)
            y = int((clipped_y - self.image_rect.top()) * y_ratio)
            points.append(QPoint(x, y))
        return QPolygon(points)

    def _image_to_view_polygon(self, polygon: QPolygon) -> QPolygon:
        if self.image.isNull() or self.image_rect.isNull() or polygon.isEmpty():
            return QPolygon()
        x_ratio = self.image_rect.width() / self.image.width()
        y_ratio = self.image_rect.height() / self.image.height()
        points = []
        for point in polygon:
            x = int(point.x() * x_ratio) + self.image_rect.left()
            y = int(point.y() * y_ratio) + self.image_rect.top()
            points.append(QPoint(x, y))
        return QPolygon(points)
