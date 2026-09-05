"""连续自由运行采集器的纯模拟测试，不连接相机或运动轴。"""

import threading
import time
import unittest

import numpy as np

from backend.direct_fine import ContinuousBestFrameCollector


class _FrameRateInfo:
    configured_fps = 25.0
    resulting_fps = 24.5


class _ContinuousCamera:
    def __init__(self):
        self.callback = None
        self.grabbing = False
        self.calls = []

    def set_trigger_mode(self, mode):
        self.calls.append(("trigger", mode))
        assert mode == "off"

    def set_continuous_frame_rate(self, fps):
        self.calls.append(("frame_rate", float(fps)))
        return _FrameRateInfo()

    def register_frame_callback(self, callback):
        self.calls.append(("callback",))
        self.callback = callback

    def start_grabbing(self):
        self.calls.append(("start",))
        self.grabbing = True

    def stop_grabbing(self):
        self.calls.append(("stop",))
        self.grabbing = False

    def emit(self, image):
        assert self.grabbing
        self.callback(image)


class _MeanEvaluator:
    def evaluate_image(self, image, roi):
        x, y, width, height = roi
        return float(image[y:y + height, x:x + width].mean())


class _BlockingEvaluator(_MeanEvaluator):
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def evaluate_image(self, image, roi):
        self.started.set()
        self.release.wait(1.0)
        return super().evaluate_image(image, roi)


class ContinuousBestFrameCollectorTests(unittest.TestCase):
    def test_continuous_capture_keeps_best_and_only_previews_new_best(self):
        camera = _ContinuousCamera()
        previews = []
        collector = ContinuousBestFrameCollector(
            camera,
            _MeanEvaluator(),
            evaluation_roi=(1, 1, 2, 2),
            target_fps=25.0,
            max_queue=4,
            preview_callback=lambda image, index, score: previews.append(
                (index, score, int(image[0, 0]))
            ),
        )

        collector.start()
        self.assertEqual(
            [
                ("trigger", "off"),
                ("frame_rate", 25.0),
                ("callback",),
                ("start",),
            ],
            camera.calls,
        )
        for value in (10, 40, 20):
            camera.emit(np.full((4, 4), value, dtype=np.uint8))

        self.assertTrue(collector.wait_for_first_frame())
        self.assertTrue(collector.stop_and_drain())
        result = collector.result()

        self.assertEqual(3, result.received_count)
        self.assertEqual(3, result.enqueued_count)
        self.assertEqual(3, result.processed_count)
        self.assertEqual(0, result.dropped_count)
        self.assertEqual(1, result.best_index)
        self.assertEqual(40.0, result.best_score)
        self.assertTrue(np.all(result.best_image == 40))
        self.assertEqual((1, 1, 2, 2), result.evaluation_roi_local)
        self.assertEqual(25.0, result.configured_fps)
        self.assertEqual(24.5, result.resulting_fps)
        self.assertEqual([(0, 10.0, 10), (1, 40.0, 40)], previews)

    def test_queue_full_is_an_explicit_error_not_a_silent_drop(self):
        camera = _ContinuousCamera()
        evaluator = _BlockingEvaluator()
        collector = ContinuousBestFrameCollector(
            camera,
            evaluator,
            target_fps=25.0,
            max_queue=1,
        )

        collector.start()
        camera.emit(np.full((4, 4), 10, dtype=np.uint8))
        self.assertTrue(evaluator.started.wait(0.5))
        camera.emit(np.full((4, 4), 20, dtype=np.uint8))
        camera.emit(np.full((4, 4), 30, dtype=np.uint8))

        self.assertIn("队列已满", collector.error)
        evaluator.release.set()
        self.assertFalse(collector.stop_and_drain())
        with self.assertRaisesRegex(RuntimeError, "队列已满"):
            collector.result()

    def test_missing_first_frame_becomes_an_explicit_error(self):
        camera = _ContinuousCamera()
        collector = ContinuousBestFrameCollector(
            camera,
            _MeanEvaluator(),
            target_fps=25.0,
            first_frame_timeout_s=0.05,
        )

        collector.start()
        self.assertFalse(collector.wait_for_first_frame())
        time.sleep(0.05)
        self.assertIn("没有收到图像", collector.error)
        self.assertFalse(collector.stop_and_drain())


if __name__ == "__main__":
    unittest.main()
