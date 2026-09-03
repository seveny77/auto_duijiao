# -*- coding: utf-8 -*-
"""图像视图中的清晰度 ROI 坐标、拖拽和尺寸变化测试。"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PyQt5.QtCore import QPointF, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from gui.app.widgets.image_view import ImageWidget


class ImageViewEvaluationRoiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widget = ImageWidget()
        self.widget.resize(900, 650)
        self.widget.show()
        self.widget.show_frame(np.zeros((300, 400, 3), dtype=np.uint8))
        self.app.processEvents()

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        self.app.processEvents()

    def test_first_frame_defaults_to_full_image(self):
        self.assertEqual(self.widget.evaluation_roi, (0, 0, 400, 300))

    def test_valid_roi_survives_new_frame_of_same_size(self):
        self.widget.set_evaluation_roi((30, 40, 120, 80))
        self.widget.show_frame(np.zeros((300, 400, 3), dtype=np.uint8))
        self.assertEqual(self.widget.evaluation_roi, (30, 40, 120, 80))
        self.assertEqual(self.widget._roi_item.roi(), (30, 40, 120, 80))

    def test_out_of_bounds_roi_resets_when_image_becomes_smaller(self):
        self.widget.set_evaluation_roi((250, 180, 120, 90))
        self.widget.show_frame(np.zeros((100, 160, 3), dtype=np.uint8))
        self.assertEqual(self.widget.evaluation_roi, (0, 0, 160, 100))

    def test_roi_item_cannot_be_moved_outside_image(self):
        self.widget.set_evaluation_roi((20, 20, 100, 80))
        self.widget._roi_item.setPos(380, 280)
        self.app.processEvents()
        self.assertEqual(self.widget.evaluation_roi, (300, 220, 100, 80))

    def test_dragging_in_edit_mode_creates_image_coordinate_roi(self):
        view = self.widget.view
        start = view.mapFromScene(QPointF(50, 60))
        end = view.mapFromScene(QPointF(210, 180))
        self.widget.roi_edit_btn.setChecked(True)
        QTest.mousePress(view.viewport(), Qt.LeftButton, pos=start)
        QTest.mouseMove(view.viewport(), pos=end, delay=10)
        QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=end)
        self.app.processEvents()

        x, y, width, height = self.widget.evaluation_roi
        self.assertAlmostEqual(x, 50, delta=2)
        self.assertAlmostEqual(y, 60, delta=2)
        self.assertAlmostEqual(width, 160, delta=3)
        self.assertAlmostEqual(height, 120, delta=3)
        self.assertFalse(self.widget.roi_edit_btn.isChecked())


if __name__ == "__main__":
    unittest.main()
