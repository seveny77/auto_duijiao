# -*- coding: utf-8 -*-
"""对焦图像视图：缩放、平移和清晰度评价 ROI 编辑。"""

import math

import cv2
from PyQt5.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QImage, QPen, QPixmap
from PyQt5.QtWidgets import (
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from backend.focus_roi import fit_evaluation_roi, normalize_evaluation_roi


class EvaluationRoiItem(QGraphicsRectItem):
    """可移动、可从右下角缩放的清晰度评价矩形。"""

    MIN_SIZE_PX = 8.0
    HANDLE_SIZE_PX = 12.0

    def __init__(self, changed_callback, parent=None):
        super().__init__(parent)
        self._changed_callback = changed_callback
        self._bounds = QRectF()
        self._suppress_callback = False
        self._resizing = False
        self._resize_start_scene = QPointF()
        self._resize_start_rect = QRectF()

        pen = QPen(QColor(255, 215, 0), 2)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setBrush(QColor(255, 215, 0, 28))
        self.setZValue(20)
        self.setFlags(
            QGraphicsItem.ItemIsMovable
            | QGraphicsItem.ItemIsSelectable
            | QGraphicsItem.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)

    def set_bounds(self, bounds: QRectF) -> None:
        self._bounds = QRectF(bounds)

    def set_roi(self, roi) -> None:
        x, y, width, height = roi
        self._suppress_callback = True
        try:
            self.setRect(0.0, 0.0, float(width), float(height))
            # 必须先更新尺寸，再按新尺寸执行位置边界约束。
            # 否则从“整图 ROI”切到较小 ROI 时，旧宽高会把 x/y
            # 错误地夹回图像左上角。
            self.setPos(float(x), float(y))
        finally:
            self._suppress_callback = False

    def roi(self):
        position = self.pos()
        rect = self.rect()
        x = max(0, int(round(position.x())))
        y = max(0, int(round(position.y())))
        width = max(1, int(round(rect.width())))
        height = max(1, int(round(rect.height())))
        return x, y, width, height

    def _near_resize_handle(self, local_position: QPointF) -> bool:
        rect = self.rect()
        margin = self.HANDLE_SIZE_PX
        return (
            abs(local_position.x() - rect.right()) <= margin
            and abs(local_position.y() - rect.bottom()) <= margin
        )

    def hoverMoveEvent(self, event):
        if self._near_resize_handle(event.pos()):
            self.setCursor(QCursor(Qt.SizeFDiagCursor))
        else:
            self.setCursor(QCursor(Qt.SizeAllCursor))
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event):
        self.unsetCursor()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._near_resize_handle(event.pos()):
            self._resizing = True
            self._resize_start_scene = event.scenePos()
            self._resize_start_rect = QRectF(self.rect())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self._resizing:
            super().mouseMoveEvent(event)
            return

        delta = event.scenePos() - self._resize_start_scene
        maximum_width = max(
            self.MIN_SIZE_PX,
            self._bounds.right() - self.pos().x(),
        )
        maximum_height = max(
            self.MIN_SIZE_PX,
            self._bounds.bottom() - self.pos().y(),
        )
        width = min(
            maximum_width,
            max(self.MIN_SIZE_PX, self._resize_start_rect.width() + delta.x()),
        )
        height = min(
            maximum_height,
            max(self.MIN_SIZE_PX, self._resize_start_rect.height() + delta.y()),
        )
        self.setRect(0.0, 0.0, width, height)
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._resizing and event.button() == Qt.LeftButton:
            self._resizing = False
            self._notify_changed()
            event.accept()
            return
        super().mouseReleaseEvent(event)
        self._notify_changed()

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionChange and not self._bounds.isEmpty():
            requested = value
            rect = self.rect()
            maximum_x = max(self._bounds.left(), self._bounds.right() - rect.width())
            maximum_y = max(self._bounds.top(), self._bounds.bottom() - rect.height())
            return QPointF(
                min(max(requested.x(), self._bounds.left()), maximum_x),
                min(max(requested.y(), self._bounds.top()), maximum_y),
            )
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._notify_changed()
        return super().itemChange(change, value)

    def _notify_changed(self) -> None:
        if not self._suppress_callback and self._changed_callback is not None:
            self._changed_callback(self.roi())

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 215, 0))
        size = self.HANDLE_SIZE_PX
        painter.drawRect(
            QRectF(
                self.rect().right() - size,
                self.rect().bottom() - size,
                size,
                size,
            )
        )
        painter.restore()


class ZoomableGraphicsView(QGraphicsView):
    """支持缩放、平移，并可在图像场景坐标中拖出 ROI。"""

    roi_drawn = pyqtSignal(object)
    roi_edit_finished = pyqtSignal()

    ZOOM_FACTOR = 1.15
    MAX_ZOOM_STEPS = 20

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self._zoom_steps = 0
        self._roi_edit_mode = False
        self._roi_start = None
        self._roi_preview_item = None
        self._image_rect = QRectF()
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.ScrollHandDrag)

    @property
    def user_zoomed(self) -> bool:
        return self._zoom_steps > 0

    @property
    def roi_edit_mode(self) -> bool:
        return self._roi_edit_mode

    def set_image_rect(self, rect: QRectF) -> None:
        self._image_rect = QRectF(rect)

    def set_roi_edit_mode(self, enabled: bool) -> None:
        self._roi_edit_mode = bool(enabled)
        self._roi_start = None
        self._remove_roi_preview()
        if self._roi_edit_mode:
            self.setDragMode(QGraphicsView.NoDrag)
            self.viewport().setCursor(Qt.CrossCursor)
        else:
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self.viewport().unsetCursor()

    def reset_zoom(self):
        scene = self.scene()
        if scene is None:
            return
        rect = scene.itemsBoundingRect()
        if rect.isEmpty():
            return
        self.resetTransform()
        self.fitInView(rect, Qt.KeepAspectRatio)
        self._zoom_steps = 0

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.user_zoomed:
            self.reset_zoom()

    def wheelEvent(self, event):
        scene = self.scene()
        if scene is None or scene.itemsBoundingRect().isEmpty():
            super().wheelEvent(event)
            return

        delta = event.angleDelta().y()
        if delta > 0:
            if self._zoom_steps >= self.MAX_ZOOM_STEPS:
                event.accept()
                return
            self.scale(self.ZOOM_FACTOR, self.ZOOM_FACTOR)
            self._zoom_steps += 1
        elif delta < 0:
            if self._zoom_steps <= 0:
                self.reset_zoom()
                event.accept()
                return
            inverse = 1.0 / self.ZOOM_FACTOR
            self.scale(inverse, inverse)
            self._zoom_steps -= 1
            if self._zoom_steps == 0:
                self.reset_zoom()
        event.accept()

    def mousePressEvent(self, event):
        if self._roi_edit_mode and event.button() == Qt.LeftButton:
            point = self._clamp_to_image(self.mapToScene(event.pos()))
            if point is not None:
                self._roi_start = point
                preview_pen = QPen(QColor(255, 215, 0), 2, Qt.DashLine)
                preview_pen.setCosmetic(True)
                self._roi_preview_item = self.scene().addRect(
                    QRectF(point, point),
                    preview_pen,
                    QColor(255, 215, 0, 20),
                )
                self._roi_preview_item.setZValue(30)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._roi_edit_mode and self._roi_start is not None:
            point = self._clamp_to_image(self.mapToScene(event.pos()))
            if point is not None and self._roi_preview_item is not None:
                self._roi_preview_item.setRect(
                    QRectF(self._roi_start, point).normalized()
                )
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            self._roi_edit_mode
            and event.button() == Qt.LeftButton
            and self._roi_start is not None
        ):
            point = self._clamp_to_image(self.mapToScene(event.pos()))
            start = self._roi_start
            self._roi_start = None
            self._remove_roi_preview()
            if point is not None:
                rect = QRectF(start, point).normalized().intersected(self._image_rect)
                if rect.width() >= 2.0 and rect.height() >= 2.0:
                    left = max(0, int(math.floor(rect.left())))
                    top = max(0, int(math.floor(rect.top())))
                    right = min(int(self._image_rect.width()), int(math.ceil(rect.right())))
                    bottom = min(int(self._image_rect.height()), int(math.ceil(rect.bottom())))
                    self.roi_drawn.emit(
                        (left, top, max(1, right - left), max(1, bottom - top))
                    )
            self.set_roi_edit_mode(False)
            self.roi_edit_finished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if not self._roi_edit_mode and event.button() == Qt.LeftButton:
            self.reset_zoom()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _clamp_to_image(self, point: QPointF):
        if self._image_rect.isEmpty():
            return None
        return QPointF(
            min(max(point.x(), self._image_rect.left()), self._image_rect.right()),
            min(max(point.y(), self._image_rect.top()), self._image_rect.bottom()),
        )

    def _remove_roi_preview(self) -> None:
        if self._roi_preview_item is not None:
            scene = self._roi_preview_item.scene()
            if scene is not None:
                scene.removeItem(self._roi_preview_item)
            self._roi_preview_item = None


class ImageWidget(QGroupBox):
    """显示对焦图像，并维护硬件 ROI 图像内的清晰度 ROI。"""

    evaluation_roi_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__("图像显示", parent)
        layout = QVBoxLayout(self)

        toolbar = QHBoxLayout()
        self.live_btn = QPushButton("实时预览")
        self.roi_edit_btn = QPushButton("设置清晰度 ROI")
        self.roi_edit_btn.setCheckable(True)
        self.roi_edit_btn.setToolTip("进入后在图像中按住左键拖出清晰度评价区域")
        self.roi_clear_btn = QPushButton("清除 ROI")
        self.roi_clear_btn.setToolTip("恢复为整张硬件 ROI 图像参与清晰度评价")
        self.roi_label = QLabel("清晰度 ROI：等待图像")
        toolbar.addWidget(self.live_btn)
        toolbar.addWidget(self.roi_edit_btn)
        toolbar.addWidget(self.roi_clear_btn)
        toolbar.addWidget(self.roi_label)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self._scene = QGraphicsScene(self)
        self.pixel_item = QGraphicsPixmapItem()
        self._scene.addItem(self.pixel_item)

        self._roi = None
        self._frame_width = 0
        self._frame_height = 0
        self._roi_item = EvaluationRoiItem(self._on_roi_item_changed)
        self._roi_item.setVisible(False)
        self._scene.addItem(self._roi_item)

        self._view = ZoomableGraphicsView(self._scene)
        self._view.setAlignment(Qt.AlignCenter)
        self._view.setStyleSheet("background-color: #202020;")
        layout.addWidget(self._view)

        self.roi_edit_btn.toggled.connect(self._view.set_roi_edit_mode)
        self.roi_clear_btn.clicked.connect(self.clear_evaluation_roi)
        self._view.roi_drawn.connect(self.set_evaluation_roi)
        self._view.roi_edit_finished.connect(
            lambda: self.roi_edit_btn.setChecked(False)
        )

    @property
    def view(self):
        """供界面测试和后续交互协调读取实际视图。"""

        return self._view

    @property
    def evaluation_roi(self):
        return self._roi

    def set_evaluation_roi(self, roi, emit_signal: bool = True) -> None:
        """设置局部像素 ROI；尚无图像时先保存，首帧到达后校验。"""

        normalized = normalize_evaluation_roi(roi)
        if self._frame_width > 0 and self._frame_height > 0:
            normalized = fit_evaluation_roi(
                normalized,
                self._frame_width,
                self._frame_height,
            )
        changed = normalized != self._roi
        self._roi = normalized
        self._apply_roi_visual()
        if emit_signal and changed:
            self.evaluation_roi_changed.emit(self._roi)

    def clear_evaluation_roi(self) -> None:
        """清除局部选择；有图像时恢复整图评价。"""

        if self._frame_width > 0 and self._frame_height > 0:
            self.set_evaluation_roi(
                (0, 0, self._frame_width, self._frame_height),
                emit_signal=True,
            )
        else:
            self.set_evaluation_roi(None, emit_signal=True)

    def set_roi_interaction_enabled(self, enabled: bool) -> None:
        """任务运行时可锁定 ROI，避免评价区域在中途变化。"""

        enabled = bool(enabled)
        self.roi_edit_btn.setEnabled(enabled)
        self.roi_clear_btn.setEnabled(enabled)
        self._roi_item.setFlag(QGraphicsItem.ItemIsMovable, enabled)
        self._roi_item.setAcceptedMouseButtons(
            Qt.LeftButton if enabled else Qt.NoButton
        )
        if not enabled:
            self.roi_edit_btn.setChecked(False)

    def show_frame(self, img, reset_view: bool = False):
        """显示一帧并让清晰度 ROI 适配当前原图尺寸。"""

        pm = self._numpy_to_pixmap(img)
        old_pixmap = self.pixel_item.pixmap()
        had_image = not old_pixmap.isNull()
        old_size = old_pixmap.size()

        self.pixel_item.setPixmap(pm)
        image_rect = self.pixel_item.boundingRect()
        self._scene.setSceneRect(image_rect)
        self._view.set_image_rect(image_rect)
        self._roi_item.set_bounds(image_rect)

        self._frame_width = pm.width()
        self._frame_height = pm.height()
        fitted_roi = fit_evaluation_roi(
            self._roi,
            self._frame_width,
            self._frame_height,
        )
        roi_changed = fitted_roi != self._roi
        self._roi = fitted_roi
        self._apply_roi_visual()
        if roi_changed:
            self.evaluation_roi_changed.emit(self._roi)

        image_size_changed = had_image and old_size != pm.size()
        should_reset = (
            reset_view
            or not had_image
            or image_size_changed
            or not self._view.user_zoomed
        )
        if should_reset:
            self._view.reset_zoom()

    def _on_roi_item_changed(self, roi) -> None:
        if self._frame_width <= 0 or self._frame_height <= 0:
            return
        fitted = fit_evaluation_roi(roi, self._frame_width, self._frame_height)
        if fitted != self._roi:
            self._roi = fitted
            self._update_roi_label()
            self.evaluation_roi_changed.emit(self._roi)

    def _apply_roi_visual(self) -> None:
        if self._roi is None or self._frame_width <= 0 or self._frame_height <= 0:
            self._roi_item.setVisible(False)
        else:
            self._roi_item.set_roi(self._roi)
            self._roi_item.setVisible(True)
        self._update_roi_label()

    def _update_roi_label(self) -> None:
        if self._roi is None:
            self.roi_label.setText("清晰度 ROI：等待图像")
            return
        x, y, width, height = self._roi
        self.roi_label.setText(
            f"清晰度 ROI：x={x}, y={y}, {width}×{height}"
        )

    @staticmethod
    def _numpy_to_pixmap(img) -> QPixmap:
        if img.ndim == 2:
            height, width = img.shape
            qimg = QImage(
                img.data,
                width,
                height,
                width,
                QImage.Format_Grayscale8,
            )
        else:
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            height, width, _channels = rgb.shape
            qimg = QImage(
                rgb.data,
                width,
                height,
                3 * width,
                QImage.Format_RGB888,
            )
        return QPixmap.fromImage(qimg.copy())
