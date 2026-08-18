import cv2
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QGroupBox, QVBoxLayout, QPushButton,
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
)
class ZoomableGraphicsView(QGraphicsView):
    """第一版：支持最基础的鼠标滚轮缩放。"""

    ZOOM_FACTOR = 1.15
    MAX_ZOOM_STEPS = 20

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self._zoom_steps = 0
        # 让缩放尽量围绕鼠标所在位置进行。
        self.setTransformationAnchor(
            QGraphicsView.AnchorUnderMouse
        )
        # 图像放大后，按住鼠标左键可以拖动查看不同区域。
        self.setDragMode(
            QGraphicsView.ScrollHandDrag
        )

    @property
    def user_zoomed(self) -> bool:
        """用户是否已经手动缩放视图。"""
        return self._zoom_steps>0

    def reset_zoom(self):
        """清除手动缩放，恢复完整显示整个场景。"""
        scene = self.scene()

        if scene is None:
            return
        # 获取场景中所有图元的总范围。
        # 当前场景中主要是 QGraphicsPixmapItem。

        rect = scene.itemsBoundingRect()
        if rect.isEmpty():
            return
        # 清除之前 scale() 形成的视图变换。
        self.resetTransform()
        # 按当前窗口大小重新完整显示图像。
        self.fitInView(
            rect,
            Qt.KeepAspectRatio,
        )
        # 回到基础缩放状态。
        self._zoom_steps = 0

    def resizeEvent(self, event):
        """视图窗口尺寸变化时重新适配图像。"""

        # 先让 QGraphicsView 完成自己的尺寸调整。
        super().resizeEvent(event)

        # 用户没有手动放大时，图像继续自动适配窗口。
        #
        # 用户已经放大时，不进行复位，
        # 避免窗口变化破坏当前观察区域。
        if not self.user_zoomed:
            self.reset_zoom()

    def wheelEvent(self, event):
        """鼠标滚轮事件。"""
        scene = self.scene()
        if scene is None or scene.itemsBoundingRect().isEmpty():
            super().wheelEvent(event)
            return

        delta = event.angleDelta().y()
        if delta > 0:
            # 向上滚轮：放大。
            if self._zoom_steps >= self.MAX_ZOOM_STEPS:
                # 已达到最大放大档数。
                event.accept()
                return

            self.scale(
                self.ZOOM_FACTOR,
                self.ZOOM_FACTOR,
            )
            self._zoom_steps += 1

        elif delta < 0:
            # 向下滚轮：缩小。
            if self._zoom_steps <= 0:
                # 已经处于完整显示状态，
                # 不允许继续把图像缩得比窗口更小。
                self.reset_zoom()
                event.accept()
                return

            inverse = 1.0 / self.ZOOM_FACTOR
            self.scale(
                inverse,
                inverse,
            )
            self._zoom_steps -= 1

            # 多次浮点缩放可能有少量累计误差。
            # 回到第 0 档时重新执行一次精确的完整显示。
            if self._zoom_steps == 0:
                self.reset_zoom()

        event.accept()

    def mouseDoubleClickEvent(self, event):
        """鼠标左键双击时恢复完整显示。"""
        if event.button() == Qt.LeftButton:
            self.reset_zoom()
            event.accept()
            return

        super().mouseDoubleClickEvent(event)

class ImageWidget(QGroupBox):
    def __init__(self, parent=None):
        super().__init__("图像显示",parent)
        layout = QVBoxLayout(self)

        self.live_btn = QPushButton("实时预览")
        layout.addWidget(self.live_btn,alignment=Qt.AlignLeft)

        #场景QGraphicsScene、图片QGraphicsPixmapItem、视图QGraphicsView
        self._scene = QGraphicsScene(self)
        self.pixel_item = QGraphicsPixmapItem()
        self._scene.addItem(self.pixel_item)

        self._view = ZoomableGraphicsView(self._scene)
        self._view.setAlignment(Qt.AlignCenter)
        self._view.setStyleSheet("background-color: #202020;")
        layout.addWidget(self._view)

    def show_frame(
            self,
            img,
            reset_view: bool = False,
    ):
        """显示一帧图像。

        reset_view=True：
            无论当前是否缩放，都恢复完整显示。

        reset_view=False：
            相同分辨率的实时帧保留用户缩放状态。
        """

        pm = self._numpy_to_pixmap(img)

        # 在替换图片以前，记录旧 Pixmap 的状态。
        old_pixmap = self.pixel_item.pixmap()
        had_image = not old_pixmap.isNull()
        old_size = old_pixmap.size()

        # 更新图像内容。
        self.pixel_item.setPixmap(pm)

        # 明确更新场景范围。
        #
        # 如果从大图切换到小图，QGraphicsScene 的自动范围
        # 不一定会立即缩小，可能留下多余的空白拖动区域。
        self._scene.setSceneRect(
            self.pixel_item.boundingRect()
        )

        # 只有已经显示过图像时，尺寸比较才有意义。
        image_size_changed = (
                had_image
                and old_size != pm.size()
        )

        should_reset = (
                reset_view
                or not had_image
                or image_size_changed
                or not self._view.user_zoomed
        )

        if should_reset:
            self._view.reset_zoom()

    @staticmethod
    def _numpy_to_pixmap(img) -> QPixmap:
        if img.ndim == 2:  # 灰度图
            h, w = img.shape
            qimg = QImage(img.data, w, h, w, QImage.Format_Grayscale8)
        else:  # BGR 彩色
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            h, w, _ = rgb.shape
            qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        return QPixmap.fromImage(qimg.copy())

