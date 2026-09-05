# -*- coding: utf-8 -*-
"""独立异步质检服务的线程、队列、错误和关闭测试。"""

import threading
import time
import unittest
from pathlib import Path

import numpy as np
from PyQt5.QtCore import QCoreApplication, QThread

from backend.inspection_config import InspectionConfig
from backend.inspection_types import (
    CircleCandidate,
    ImageInspectionResult,
    InspectionResult,
    InspectionStatus,
    SegmentationInstance,
)
from gui.app.services.inspection_service import (
    InspectionService,
    InspectionState,
)


class FakeSegmentationService:
    def __init__(self):
        self.loaded = False
        self.fail_load = False
        self.fail_predict = False
        self.fail_predict_calls = set()
        self.load_thread = None
        self.predict_thread = None
        self.load_native_thread = None
        self.predict_native_thread = None
        self.unload_native_thread = None
        self.predict_calls = []
        self.predict_gate = None
        self.model_path = ""
        self.class_names = {0: "异物"}
        self.load_ms = 1.0
        self.warmup_ms = 2.0

    def load(self, path, **kwargs):
        self.load_thread = QThread.currentThread()
        self.load_native_thread = threading.current_thread()
        if self.fail_load:
            raise RuntimeError("load failed")
        self.loaded = True
        self.model_path = path

    def predict(self, image, **kwargs):
        self.predict_thread = QThread.currentThread()
        self.predict_native_thread = threading.current_thread()
        self.predict_calls.append((image, kwargs))
        call_number = len(self.predict_calls)
        if self.predict_gate is not None:
            self.predict_gate.wait(timeout=2.0)
        if self.fail_predict or call_number in self.fail_predict_calls:
            self.fail_predict = False
            raise RuntimeError("predict failed")
        return [SegmentationInstance(
            class_id=0,
            class_name="异物",
            confidence=0.9,
            polygon=[(1, 1), (2, 1), (2, 2)],
            bbox=(1, 1, 2, 2),
            pixel_area=1,
        )]

    def unload_for_shutdown(self):
        self.unload_native_thread = threading.current_thread()
        self.loaded = False


class FakeCircleDetector:
    def __init__(self):
        self.calls = []
        self.detect_gate = None
        self.detect_thread = None
        self.detect_native_thread = None
        self.return_value = None

    def detect(self, image, config):
        self.detect_thread = QThread.currentThread()
        self.detect_native_thread = threading.current_thread()
        self.calls.append((image, config))
        if self.detect_gate is not None:
            self.detect_gate.wait(timeout=2.0)
        if self.return_value is not None:
            return self.return_value
        image_height, image_width = image.shape[:2]
        return [CircleCandidate(
            center_x=image_width / 2.0,
            center_y=image_height / 2.0,
            radius_px=max(1.0, min(image_width, image_height) / 4.0),
            score=0.9,
        )], 0, True, []


class FakeRuleEngine:
    def __init__(self):
        self.calls = []
        self.statuses = []

    def evaluate(self, **kwargs):
        self.calls.append(kwargs)
        status = (
            self.statuses.pop(0)
            if self.statuses
            else InspectionStatus.PASS
        )
        return InspectionResult(
            status=status,
            image_width=kwargs["image_width"],
            image_height=kwargs["image_height"],
            instances=list(kwargs["instances"]),
            circle_candidates=list(kwargs["circle_candidates"]),
            selected_circle_index=kwargs["selected_circle_index"],
            circle_confirmed=kwargs["circle_confirmed"],
        )


class InspectionServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.segmentation = FakeSegmentationService()
        self.circle = FakeCircleDetector()
        self.engine = FakeRuleEngine()
        self.service = InspectionService(
            segmentation_service=self.segmentation,
            circle_model_service=FakeSegmentationService(),
            circle_detector=self.circle,
            rule_engine=self.engine,
        )
        self.config = InspectionConfig(
            inference_imgsz=640,
            inference_confidence_floor=0.01,
            mm_per_pixel=0.1,
        )

    def tearDown(self):
        if self.segmentation.predict_gate is not None:
            self.segmentation.predict_gate.set()
        if self.circle.detect_gate is not None:
            self.circle.detect_gate.set()
        self.service.begin_shutdown()
        self.assertTrue(self._wait_until(
            lambda: self.service.is_shutdown_complete,
            timeout=3.0,
        ))

    def _wait_until(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        self.app.processEvents()
        return predicate()

    def _load_model(self):
        self.assertTrue(self.service.load_model(
            "fake.pt", "circle.pt", self.config
        ))
        self.assertTrue(self._wait_until(
            lambda: self.service.state == InspectionState.READY,
        ))

    def test_model_load_runs_in_worker_thread(self):
        main_thread = QThread.currentThread()
        self._load_model()

        self.assertTrue(self.service.is_model_loaded)
        self.assertIsNot(self.segmentation.load_thread, main_thread)

    def test_model_load_and_predict_share_dedicated_python_thread(self):
        self._load_model()
        completed = []
        self.service.inspection_finished.connect(
            lambda task_id, result: completed.append((task_id, result))
        )

        self.service.submit_image(
            np.zeros((20, 30, 3), dtype=np.uint8),
            self.config,
        )
        self.assertTrue(self._wait_until(lambda: len(completed) == 1))

        gpu_thread = self.segmentation.load_native_thread
        self.assertIsNotNone(gpu_thread)
        self.assertTrue(gpu_thread.name.startswith("inspection-gpu"))
        self.assertIs(self.segmentation.predict_native_thread, gpu_thread)
        self.assertIs(self.circle.detect_native_thread, gpu_thread)

    def test_model_load_failure_returns_to_not_loaded_and_can_retry(self):
        failures = []
        self.service.model_load_failed.connect(failures.append)
        self.segmentation.fail_load = True
        self.assertTrue(self.service.load_model(
            "fake.pt", "circle.pt", self.config
        ))
        self.assertTrue(self._wait_until(lambda: len(failures) == 1))

        self.assertEqual(self.service.state, InspectionState.NOT_LOADED)
        self.assertFalse(self.service.is_model_loaded)
        self.segmentation.fail_load = False
        self._load_model()
        self.assertTrue(self.service.is_model_loaded)

    def test_pending_image_runs_automatically_after_model_load(self):
        results = []
        self.service.inspection_finished.connect(
            lambda task_id, result: results.append((task_id, result))
        )
        image = np.zeros((20, 30, 3), dtype=np.uint8)
        task_id = self.service.submit_image(image, self.config)

        self.assertTrue(self.service.has_pending_image)
        self._load_model()
        self.assertTrue(self._wait_until(lambda: len(results) == 1))

        self.assertEqual(results[0][0], task_id)
        self.assertEqual(results[0][1].status, InspectionStatus.PASS)
        self.assertEqual(results[0][1].image_width, 30)
        self.assertIn("inference", results[0][1].timings_ms)
        self.assertIsNot(self.segmentation.predict_thread, QThread.currentThread())

    def test_visual_ready_returns_the_image_for_the_same_task(self):
        self._load_model()
        completed = []
        multi_completed = []
        self.service.inspection_visual_ready.connect(
            lambda task_id, image, result, config: completed.append(
                (task_id, image, result, config)
            )
        )
        self.service.image_inspection_visual_ready.connect(
            lambda task_id, image, result, config: multi_completed.append(
                (task_id, image, result, config)
            )
        )
        image = np.zeros((12, 18, 3), dtype=np.uint8)

        task_id = self.service.submit_image(image, self.config)
        self.assertTrue(self._wait_until(lambda: len(completed) == 1))
        self.assertTrue(self._wait_until(lambda: len(multi_completed) == 1))

        self.assertEqual(completed[0][0], task_id)
        self.assertIs(completed[0][1], image)
        self.assertEqual(completed[0][2].status, InspectionStatus.PASS)
        self.assertEqual(completed[0][3].inference_imgsz, 640)
        self.assertEqual(multi_completed[0][0], task_id)
        self.assertIs(multi_completed[0][1], image)
        self.assertIsInstance(multi_completed[0][2], ImageInspectionResult)
        self.assertEqual(multi_completed[0][3].inference_imgsz, 640)

    def test_duplicate_is_ignored_and_busy_queue_keeps_latest_image(self):
        self._load_model()
        gate = threading.Event()
        self.segmentation.predict_gate = gate
        completed = []
        self.service.inspection_finished.connect(
            lambda task_id, result: completed.append(task_id)
        )
        first = np.zeros((10, 10), dtype=np.uint8)
        second = np.ones((10, 10), dtype=np.uint8)
        latest = np.full((10, 10), 2, dtype=np.uint8)

        first_id = self.service.submit_image(first, self.config)
        self.assertTrue(self._wait_until(lambda: len(self.segmentation.predict_calls) == 1))
        self.assertEqual(self.service.submit_image(first, self.config), "")
        second_id = self.service.submit_image(second, self.config)
        latest_id = self.service.submit_image(latest, self.config)
        self.assertNotEqual(second_id, latest_id)
        gate.set()

        self.assertTrue(self._wait_until(lambda: len(completed) == 2))
        self.assertEqual(completed, [first_id, latest_id])
        np.testing.assert_array_equal(
            self.segmentation.predict_calls[1][0],
            latest,
        )

    def test_predict_error_returns_error_and_service_recovers(self):
        self._load_model()
        results = []
        self.service.inspection_finished.connect(
            lambda _task_id, result: results.append(result)
        )
        self.segmentation.fail_predict = True
        self.service.submit_image(np.zeros((10, 10), dtype=np.uint8), self.config)
        self.assertTrue(self._wait_until(lambda: len(results) == 1))

        self.assertEqual(results[0].status, InspectionStatus.ERROR)
        self.assertIn("predict failed", results[0].error)
        self.assertEqual(self.service.state, InspectionState.READY)

        self.service.submit_image(np.ones((10, 10), dtype=np.uint8), self.config)
        self.assertTrue(self._wait_until(lambda: len(results) == 2))
        self.assertEqual(results[1].status, InspectionStatus.PASS)

    def test_configuration_is_deep_copied(self):
        self._load_model()
        gate = threading.Event()
        self.segmentation.predict_gate = gate
        self.service.submit_image(np.zeros((10, 10), dtype=np.uint8), self.config)
        self.config.inference_imgsz = 2048
        gate.set()
        self.assertTrue(self._wait_until(lambda: len(self.engine.calls) == 1))

        self.assertEqual(
            self.segmentation.predict_calls[0][1]["imgsz"],
            640,
        )

    def test_circle_redetection_rebuilds_roi_and_predicts_again(self):
        self._load_model()
        completed = []
        self.service.inspection_visual_ready.connect(
            lambda task_id, image, result, config: completed.append(
                (task_id, image, result, config)
            )
        )
        image = np.zeros((20, 30, 3), dtype=np.uint8)
        task_id = self.service.submit_image(image, self.config)
        self.assertTrue(self._wait_until(lambda: len(completed) == 1))
        source_result = completed[0][2]
        predict_count = len(self.segmentation.predict_calls)
        circle_count = len(self.circle.calls)
        redetected = []
        self.service.circle_redetection_finished.connect(
            lambda result_task_id, result: redetected.append(
                (result_task_id, result)
            )
        )

        accepted = self.service.redetect_circle(
            task_id,
            image,
            source_result,
            self.config,
        )
        self.assertTrue(accepted)
        self.assertTrue(self._wait_until(
            lambda: len(redetected) == 1
            and self.service.state == InspectionState.READY
        ))

        self.assertEqual(
            len(self.segmentation.predict_calls),
            predict_count + 1,
        )
        self.assertEqual(len(self.circle.calls), circle_count + 1)
        self.assertEqual(redetected[0][0], task_id)
        self.assertIsNot(
            redetected[0][1].instances[0],
            source_result.instances[0],
        )
        self.assertIn(
            "circle_redetection_total",
            redetected[0][1].timings_ms,
        )
        self.assertIsNot(self.circle.detect_thread, QThread.currentThread())

    def test_multiple_circles_are_cropped_in_order_and_restored(self):
        self._load_model()
        self.config.roi_size_px = 40
        self.config.circle.expected_circle_count = 2
        self.circle.return_value = (
            [
                CircleCandidate(
                    center_x=40,
                    center_y=50,
                    radius_px=15,
                    score=0.8,
                ),
                CircleCandidate(
                    center_x=140,
                    center_y=50,
                    radius_px=15,
                    score=0.9,
                ),
            ],
            1,
            True,
            [],
        )
        image = np.zeros((100, 200), dtype=np.uint8)
        image[30:70, 20:60] = 10
        image[30:70, 120:160] = 20
        multi_results = []
        compatibility_results = []
        self.service.image_inspection_finished.connect(
            lambda _task_id, result: multi_results.append(result)
        )
        self.service.inspection_finished.connect(
            lambda _task_id, result: compatibility_results.append(result)
        )

        self.service.submit_image(image, self.config)
        self.assertTrue(self._wait_until(lambda: len(multi_results) == 1))
        self.assertTrue(self._wait_until(
            lambda: len(compatibility_results) == 1
        ))

        result = multi_results[0]
        self.assertIsInstance(result, ImageInspectionResult)
        self.assertEqual(result.status, InspectionStatus.PASS)
        self.assertTrue(result.is_complete)
        self.assertEqual(result.detected_circle_count, 2)
        self.assertEqual(result.completed_circle_count, 2)
        self.assertEqual(
            [item.circle_id for item in result.circle_results],
            ["circle-001", "circle-002"],
        )
        self.assertEqual(len(self.segmentation.predict_calls), 2)
        self.assertEqual(self.segmentation.predict_calls[0][0].shape, (40, 40))
        self.assertEqual(self.segmentation.predict_calls[1][0].shape, (40, 40))
        self.assertTrue(np.all(self.segmentation.predict_calls[0][0] == 10))
        self.assertTrue(np.all(self.segmentation.predict_calls[1][0] == 20))
        self.assertEqual(
            result.circle_results[0].instances[0].bbox,
            (21.0, 31.0, 22.0, 32.0),
        )
        self.assertEqual(
            result.circle_results[1].instances[0].bbox,
            (121.0, 31.0, 122.0, 32.0),
        )
        self.assertEqual(compatibility_results[0].selected_circle_index, 1)
        self.assertEqual(
            compatibility_results[0].instances[0].bbox,
            (121.0, 31.0, 122.0, 32.0),
        )

    def test_missing_circle_creates_pending_placeholder(self):
        self._load_model()
        self.config.circle.expected_circle_count = 2
        self.circle.return_value = (
            [CircleCandidate(
                center_x=10,
                center_y=10,
                radius_px=5,
                score=0.9,
            )],
            0,
            False,
            ["预期检测到 2 个圆，轮廓去重后检测到 1 个"],
        )
        results = []
        compatibility_results = []
        self.service.image_inspection_finished.connect(
            lambda _task_id, result: results.append(result)
        )
        self.service.inspection_finished.connect(
            lambda _task_id, result: compatibility_results.append(result)
        )

        self.service.submit_image(np.zeros((20, 20), dtype=np.uint8), self.config)
        self.assertTrue(self._wait_until(lambda: len(results) == 1))
        self.assertTrue(self._wait_until(
            lambda: len(compatibility_results) == 1
        ))

        result = results[0]
        self.assertEqual(result.status, InspectionStatus.PENDING)
        self.assertFalse(result.is_complete)
        self.assertEqual(result.detected_circle_count, 1)
        self.assertEqual(result.completed_circle_count, 1)
        self.assertEqual(len(result.circle_results), 2)
        self.assertTrue(result.circle_results[0].completed)
        self.assertFalse(result.circle_results[1].completed)
        self.assertIsNone(result.circle_results[1].circle_candidate)
        self.assertFalse(compatibility_results[0].circle_confirmed)
        self.assertEqual(
            compatibility_results[0].status,
            InspectionStatus.PENDING,
        )

    def test_low_score_circle_prevents_image_pass(self):
        self._load_model()
        self.config.circle.min_candidate_score = 0.5
        self.circle.return_value = (
            [CircleCandidate(
                center_x=10,
                center_y=10,
                radius_px=5,
                score=0.2,
            )],
            0,
            False,
            ["候选圆评分低于自动确认阈值"],
        )
        results = []
        self.service.image_inspection_finished.connect(
            lambda _task_id, result: results.append(result)
        )

        self.service.submit_image(np.zeros((20, 20), dtype=np.uint8), self.config)
        self.assertTrue(self._wait_until(lambda: len(results) == 1))

        self.assertEqual(results[0].status, InspectionStatus.PENDING)
        self.assertFalse(results[0].is_complete)
        self.assertFalse(results[0].circle_results[0].circle_confirmed)

    def test_one_roi_error_does_not_stop_later_circles(self):
        self._load_model()
        self.config.roi_size_px = 20
        self.config.circle.expected_circle_count = 2
        self.circle.return_value = (
            [
                CircleCandidate(center_x=20, center_y=20, score=0.9),
                CircleCandidate(center_x=60, center_y=20, score=0.8),
            ],
            0,
            True,
            [],
        )
        self.segmentation.fail_predict_calls = {1}
        results = []
        self.service.image_inspection_finished.connect(
            lambda _task_id, result: results.append(result)
        )

        self.service.submit_image(np.zeros((40, 80), dtype=np.uint8), self.config)
        self.assertTrue(self._wait_until(lambda: len(results) == 1))

        result = results[0]
        self.assertEqual(len(self.segmentation.predict_calls), 2)
        self.assertEqual(result.status, InspectionStatus.ERROR)
        self.assertFalse(result.is_complete)
        self.assertEqual(result.completed_circle_count, 2)
        self.assertEqual(
            [item.status for item in result.circle_results],
            [InspectionStatus.ERROR, InspectionStatus.PASS],
        )
        self.assertIn("circle-001", result.error)
        self.assertIn("predict failed", result.error)

    def test_any_failed_circle_makes_whole_image_fail(self):
        self._load_model()
        self.config.roi_size_px = 20
        self.config.circle.expected_circle_count = 2
        self.circle.return_value = (
            [
                CircleCandidate(center_x=20, center_y=20, score=0.9),
                CircleCandidate(center_x=60, center_y=20, score=0.8),
            ],
            0,
            True,
            [],
        )
        self.engine.statuses = [
            InspectionStatus.PASS,
            InspectionStatus.FAIL,
        ]
        results = []
        self.service.image_inspection_finished.connect(
            lambda _task_id, result: results.append(result)
        )

        self.service.submit_image(np.zeros((40, 80), dtype=np.uint8), self.config)
        self.assertTrue(self._wait_until(lambda: len(results) == 1))

        self.assertEqual(results[0].status, InspectionStatus.FAIL)
        self.assertTrue(results[0].is_complete)

    def test_busy_circle_redetection_rejects_duplicate_and_shutdown_waits(self):
        self._load_model()
        completed = []
        self.service.inspection_visual_ready.connect(
            lambda task_id, image, result, config: completed.append(
                (task_id, image, result)
            )
        )
        image = np.zeros((20, 30), dtype=np.uint8)
        task_id = self.service.submit_image(image, self.config)
        self.assertTrue(self._wait_until(lambda: len(completed) == 1))
        source_result = completed[0][2]
        gate = threading.Event()
        self.circle.detect_gate = gate

        self.assertTrue(self.service.redetect_circle(
            task_id, image, source_result, self.config
        ))
        self.assertTrue(self._wait_until(lambda: len(self.circle.calls) == 2))
        self.assertFalse(self.service.redetect_circle(
            task_id, image, source_result, self.config
        ))
        self.assertFalse(self.service.begin_shutdown())
        self.app.processEvents()
        self.assertFalse(self.service.is_shutdown_complete)
        gate.set()
        self.assertTrue(self._wait_until(
            lambda: self.service.is_shutdown_complete,
            timeout=3.0,
        ))

    def test_shutdown_rejects_new_work_and_waits_for_running_task(self):
        self._load_model()
        gate = threading.Event()
        self.segmentation.predict_gate = gate
        self.service.submit_image(np.zeros((10, 10), dtype=np.uint8), self.config)
        self.assertTrue(self._wait_until(lambda: len(self.segmentation.predict_calls) == 1))

        self.assertFalse(self.service.begin_shutdown())
        self.assertEqual(
            self.service.submit_image(np.ones((10, 10), dtype=np.uint8), self.config),
            "",
        )
        self.app.processEvents()
        self.assertFalse(self.service.is_shutdown_complete)
        gate.set()
        self.assertTrue(self._wait_until(
            lambda: self.service.is_shutdown_complete,
            timeout=3.0,
        ))

    def test_service_source_never_terminates_qthread(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "gui"
            / "app"
            / "services"
            / "inspection_service.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn(".terminate(", source)


if __name__ == "__main__":
    unittest.main()
