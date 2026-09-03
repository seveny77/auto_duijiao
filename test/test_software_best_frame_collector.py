import time
import unittest

import numpy as np

from backend.direct_fine import SoftwareBestFrameCollector


class _SoftwareCamera:
    def __init__(self, images):
        self.images = list(images)
        self.callback = None
        self.grabbing = False
        self.index = 0

    def set_trigger_mode(self, mode):
        assert mode == "software"

    def register_frame_callback(self, callback):
        self.callback = callback

    def start_grabbing(self):
        self.grabbing = True

    def stop_grabbing(self):
        self.grabbing = False

    def trigger_software(self):
        assert self.grabbing
        image = self.images[self.index]
        self.index += 1
        self.callback(image)


class _MeanEvaluator:
    def __init__(self):
        self.rois = []

    def evaluate_image(self, image, roi):
        self.rois.append(roi)
        x, y, width, height = roi
        return float(image[y:y + height, x:x + width].mean())


class SoftwareBestFrameCollectorTests(unittest.TestCase):
    def test_software_trigger_keeps_best_frame(self):
        images = [
            np.full((4, 4), value, dtype=np.uint8)
            for value in (10, 40, 20)
        ]
        camera = _SoftwareCamera(images)
        evaluator = _MeanEvaluator()
        collector = SoftwareBestFrameCollector(
            camera,
            evaluator,
            evaluation_roi=(1, 1, 2, 2),
            frame_limit=3,
        )

        collector.start()
        self.assertTrue(collector.wait(2.0))
        self.assertTrue(collector.stop())

        result = collector.result()
        self.assertEqual(camera.index, 3)
        self.assertEqual(result.processed_count, 3)
        self.assertEqual(result.best_index, 1)
        self.assertEqual(result.best_score, 40.0)
        self.assertEqual(result.evaluation_roi_local, (1, 1, 2, 2))
        self.assertEqual(evaluator.rois, [(1, 1, 2, 2)] * 3)
        self.assertTrue(np.all(result.best_image == 40))

    def test_invalid_roi_becomes_error(self):
        camera = _SoftwareCamera([np.zeros((4, 4), dtype=np.uint8)])
        collector = SoftwareBestFrameCollector(
            camera,
            _MeanEvaluator(),
            evaluation_roi=(3, 3, 2, 2),
            frame_limit=1,
        )
        collector.start()
        time.sleep(0.1)
        self.assertFalse(collector.wait(0.5))
        self.assertIn("清晰度 ROI 越界", collector.error)
        self.assertTrue(collector.stop())


if __name__ == "__main__":
    unittest.main()
