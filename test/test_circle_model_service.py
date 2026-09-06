# -*- coding: utf-8 -*-
"""找圆模型服务的设备选择和输出转换测试。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from backend.circle_model_service import CircleModelService


class FakeBoxes:
    def __init__(self):
        self.xyxy = np.asarray([[10, 20, 50, 60]], dtype=float)
        self.conf = np.asarray([0.9], dtype=float)
        self.cls = np.asarray([0], dtype=float)

    def __len__(self):
        return 1


class FakeResult:
    boxes = FakeBoxes()


class FakeCircleModel:
    task = "detect"

    def __init__(self):
        self.calls = []

    def predict(self, **kwargs):
        self.calls.append(kwargs)
        return [FakeResult()]


class MixedShapeBoxes:
    def __init__(self):
        self.xyxy = np.asarray([
            [10, 10, 50, 50],
            [60, 10, 160, 30],
        ], dtype=float)
        self.conf = np.asarray([0.8, 0.95], dtype=float)
        self.cls = np.asarray([0, 0], dtype=float)

    def __len__(self):
        return 2


class MixedShapeResult:
    boxes = MixedShapeBoxes()


class CircleModelServiceTest(unittest.TestCase):
    def test_device_is_used_for_warmup_and_regular_prediction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "circle.pt"
            path.write_bytes(b"fake")
            model = FakeCircleModel()
            service = CircleModelService(lambda _path: model)
            with patch(
                "backend.circle_model_service.resolve_yolo_device",
                return_value="cpu",
            ):
                service.load(str(path), confidence_floor=0.25)
            circles, selected, confirmed, warnings = service.predict_circles(
                np.zeros((100, 100, 3), dtype=np.uint8),
                expected_count=1,
                confidence_floor=0.25,
            )

        self.assertEqual(service.device, "cpu")
        self.assertEqual([call["device"] for call in model.calls], ["cpu", "cpu"])
        self.assertEqual(len(circles), 1)
        self.assertEqual((circles[0].center_x, circles[0].center_y), (30.0, 40.0))
        self.assertEqual(selected, 0)
        self.assertTrue(confirmed)
        self.assertEqual(warnings, [])

    def test_prediction_filters_narrow_boxes_by_aspect_ratio(self):
        model = FakeCircleModel()
        model.predict = lambda **_kwargs: [MixedShapeResult()]
        service = CircleModelService(lambda _path: model)
        service._model = model

        circles, selected, confirmed, warnings = service.predict_circles(
            np.zeros((200, 200, 3), dtype=np.uint8),
            expected_count=1,
            confidence_floor=0.25,
            min_box_aspect_ratio=0.75,
        )

        self.assertEqual(len(circles), 1)
        self.assertEqual((circles[0].center_x, circles[0].center_y), (30.0, 30.0))
        self.assertEqual(selected, 0)
        self.assertTrue(confirmed)
        self.assertTrue(any("长宽比过滤 1 个" in item for item in warnings))


if __name__ == "__main__":
    unittest.main()
