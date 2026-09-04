# -*- coding: utf-8 -*-
"""质检结果图真实落盘及后台任务关联测试，不加载模型或硬件。"""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from PyQt5.QtCore import QCoreApplication, QThread

from backend.inspection_config import InspectionConfig
from backend.inspection_image_store import save_inspection_image
from backend.inspection_types import (
    CircleCandidate,
    CircleInspectionResult,
    ImageInspectionResult,
    InspectionRegionRule,
    InspectionStatus,
    SegmentationInstance,
)
from gui.app.services.inspection_service import InspectionService, InspectionState
from test.test_inspection_service import (
    FakeCircleDetector,
    FakeRuleEngine,
    FakeSegmentationService,
)


class InspectionImageStoreTest(unittest.TestCase):
    def test_export_preserves_source_and_contains_every_circle_and_defect(self):
        image = np.full((240, 400, 3), 35, dtype=np.uint8)
        result = ImageInspectionResult(circle_results=[
            CircleInspectionResult(
                circle_candidate=CircleCandidate(center_x=x, center_y=120, radius_px=50),
                instances=[SegmentationInstance(
                    class_id=0,
                    class_name="异物",
                    polygon=[(x - 15, 110), (x + 15, 110), (x + 15, 135), (x - 15, 135)],
                    pixel_area=750,
                )],
            )
            for x in (100, 300)
        ])
        config = InspectionConfig(
            mm_per_pixel=1.0,
            region_rules=[InspectionRegionRule(outer_radius_mm=70)],
        )
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "中文目录" / "20260904_123456_123456.jpg"
            original.parent.mkdir()
            cv2.imencode(".jpg", image)[1].tofile(str(original))
            original_bytes = original.read_bytes()

            path = Path(save_inspection_image(image, result, config, original))
            decoded = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)

            self.assertEqual(path, original.with_name(original.stem + "_inspection.jpg"))
            self.assertEqual(decoded.shape, image.shape)
            self.assertEqual(original.read_bytes(), original_bytes)
            np.testing.assert_array_equal(image, np.full_like(image, 35))
            for x in (100, 300):
                # 红色缺陷边界与绿色质检圆环在左右两端面均有保存。
                b, g, r = map(int, decoded[110, x])
                self.assertGreater(r, max(b, g) + 40)
                b, g, r = map(int, decoded[120, x + 70])
                self.assertGreater(g, max(b, r) + 40)


class InspectionImageSaveServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.segmentation = FakeSegmentationService()
        # 模型接口均使用具备 load/unload/metadata 的轻量假实现。
        self.service = InspectionService(
            segmentation_service=self.segmentation,
            circle_model_service=FakeSegmentationService(),
            circle_detector=FakeCircleDetector(),
            rule_engine=FakeRuleEngine(),
        )
        self.config = InspectionConfig(mm_per_pixel=0.1)
        self.saved = []
        self.failed = []
        self.completed = []
        self.service.inspection_image_saved.connect(
            lambda task_id, path: self.saved.append((task_id, path))
        )
        self.service.inspection_image_save_failed.connect(
            lambda task_id, message: self.failed.append((task_id, message))
        )
        self.service.inspection_finished.connect(
            lambda task_id, result: self.completed.append((task_id, result))
        )

    def tearDown(self):
        if self.segmentation.predict_gate is not None:
            self.segmentation.predict_gate.set()
        self.service.begin_shutdown()
        self.assertTrue(self.wait_until(lambda: self.service.is_shutdown_complete))
        self.directory.cleanup()

    def wait_until(self, predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        self.app.processEvents()
        return predicate()

    def load_models(self):
        self.assertTrue(self.service.load_model("seg.pt", "circle.pt", self.config))
        self.assertTrue(self.wait_until(lambda: self.service.state == InspectionState.READY))

    def test_latest_pending_task_keeps_its_own_path_and_config(self):
        first_image = np.zeros((80, 100, 3), dtype=np.uint8)
        latest_image = np.full((80, 100, 3), 70, dtype=np.uint8)
        first = self.service.submit_image(
            first_image, self.config, original_image_path=self.root / "first.jpg",
        )
        latest = self.service.submit_image(
            latest_image, self.config, original_image_path=self.root / "最新.jpg",
        )
        self.config.mm_per_pixel = 99.0
        calls = []

        def save_in_worker(image, result, config, path):
            calls.append((QThread.currentThread(), image, config.mm_per_pixel, path))
            return save_inspection_image(image, result, config, path)

        with patch("gui.app.workers.inspection_worker.save_inspection_image", side_effect=save_in_worker):
            self.load_models()
            self.assertTrue(self.wait_until(lambda: len(self.completed) == 1))

        self.assertNotEqual(first, latest)
        self.assertEqual(self.saved, [(latest, str(self.root / "最新_inspection.jpg"))])
        self.assertFalse((self.root / "first_inspection.jpg").exists())
        self.assertIs(calls[0][1], latest_image)
        self.assertEqual(calls[0][2], 0.1)
        self.assertIsNot(calls[0][0], QThread.currentThread())
        self.assertEqual(self.failed, [])

    def test_active_and_replaced_pending_paths_do_not_cross(self):
        self.load_models()
        self.segmentation.predict_gate = threading.Event()
        images = [np.full((80, 100, 3), i * 60, dtype=np.uint8) for i in range(3)]
        first = self.service.submit_image(
            images[0], self.config, original_image_path=self.root / "first.jpg",
        )
        self.assertTrue(self.wait_until(lambda: len(self.segmentation.predict_calls) == 1))
        duplicate = self.service.submit_image(
            images[0], self.config, original_image_path=self.root / "duplicate.jpg",
        )
        self.service.submit_image(images[1], self.config, original_image_path=self.root / "middle.jpg")
        latest = self.service.submit_image(images[2], self.config, original_image_path=self.root / "latest.jpg")
        self.segmentation.predict_gate.set()
        self.assertTrue(self.wait_until(lambda: len(self.completed) == 2))

        self.assertEqual(duplicate, "")
        self.assertEqual(self.saved, [
            (first, str(self.root / "first_inspection.jpg")),
            (latest, str(self.root / "latest_inspection.jpg")),
        ])
        self.assertEqual(len(list(self.root.glob("*.jpg"))), 2)

    def test_no_original_path_disables_export(self):
        self.load_models()
        with patch("gui.app.workers.inspection_worker.save_inspection_image") as save:
            self.service.submit_image(np.zeros((80, 100, 3), dtype=np.uint8), self.config)
            self.assertTrue(self.wait_until(lambda: len(self.completed) == 1))
        save.assert_not_called()
        self.assertEqual(self.saved, [])
        self.assertEqual(self.failed, [])

    def test_redetection_updates_the_same_paired_image(self):
        self.load_models()
        image = np.zeros((160, 200, 3), dtype=np.uint8)
        task_id = self.service.submit_image(
            image, self.config, original_image_path=self.root / "original.jpg",
        )
        self.assertTrue(self.wait_until(lambda: len(self.completed) == 1))
        overlay_path = self.root / "original_inspection.jpg"
        initial_bytes = overlay_path.read_bytes()
        self.config.mm_per_pixel = 1.0
        self.config.region_rules = [InspectionRegionRule(outer_radius_mm=60)]
        redetected = []
        self.service.circle_redetection_finished.connect(
            lambda current_id, result: redetected.append((current_id, result))
        )
        self.assertTrue(self.service.redetect_circle(
            task_id, image, self.completed[0][1], self.config,
        ))
        self.assertTrue(self.wait_until(lambda: len(redetected) == 1))
        self.assertEqual(self.saved, [(task_id, str(overlay_path))] * 2)
        self.assertNotEqual(initial_bytes, overlay_path.read_bytes())
        self.assertEqual(len(list(self.root.glob("*.jpg"))), 1)

    def test_write_failure_does_not_change_verdict_or_block_next_task(self):
        self.load_models()
        # 用普通文件占用目标目录，真实触发保存失败，不依赖机器权限设置。
        blocked_dir = self.root / "blocked"
        blocked_dir.write_text("occupied", encoding="utf-8")
        with self.assertLogs("gui.app.workers.inspection_worker", level="ERROR"):
            first = self.service.submit_image(
                np.zeros((80, 100, 3), dtype=np.uint8), self.config,
                original_image_path=blocked_dir / "first.jpg",
            )
            self.assertTrue(self.wait_until(lambda: len(self.completed) == 1))
        self.assertEqual(self.failed[0][0], first)
        self.assertEqual(self.completed[0][1].status, InspectionStatus.PASS)
        self.assertEqual(self.saved, [])
        second = self.service.submit_image(
            np.ones((80, 100, 3), dtype=np.uint8), self.config,
            original_image_path=self.root / "second.jpg",
        )
        self.assertTrue(self.wait_until(lambda: len(self.completed) == 2))
        self.assertEqual(self.saved, [(second, str(self.root / "second_inspection.jpg"))])


if __name__ == "__main__":
    unittest.main()
