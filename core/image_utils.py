from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap, QPolygon
from PySide6.QtWidgets import QFileDialog


SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def list_region_names(region_dir: Path) -> list[str]:
    names = []
    for file_path in sorted(region_dir.iterdir()):
        if file_path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
            continue
        if file_path.stem.startswith("already_in_"):
            continue
        names.append(file_path.stem)
    return names


def list_item_paths(item_dir: Path) -> list[Path]:
    files = []
    for file_path in sorted(item_dir.iterdir()):
        if file_path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES:
            files.append(file_path)
    return files


def make_thumbnail(path: Path, size: int = 132) -> QPixmap:
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        fallback = QPixmap(size, size)
        fallback.fill()
        return fallback
    return pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def choose_image_file(parent) -> str:
    file_path, _ = QFileDialog.getOpenFileName(
        parent,
        "选择图片",
        "",
        "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
    )
    return file_path


def scaled_image(image: QImage, scale_factor: float) -> QImage:
    if image.isNull():
        return image
    width = max(1, int(image.width() * scale_factor))
    height = max(1, int(image.height() * scale_factor))
    return image.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def crop_image(image: QImage, rect: QRect) -> QImage:
    if image.isNull() or rect.isNull():
        return image
    bounded = rect.intersected(image.rect())
    if bounded.isNull():
        return image
    return image.copy(bounded)


def crop_image_polygon(image: QImage, polygon: QPolygon) -> QImage:
    if image.isNull() or polygon.isEmpty():
        return image

    bounded = polygon.boundingRect().intersected(image.rect())
    if bounded.isNull():
        return image

    shifted = QPolygon([point - bounded.topLeft() for point in polygon])
    result = QImage(bounded.size(), QImage.Format_ARGB32)
    result.fill(QColor(255, 255, 255, 0))

    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.addPolygon(shifted)
    painter.setClipPath(path)
    painter.drawImage(-bounded.left(), -bounded.top(), image)
    painter.end()
    return result
