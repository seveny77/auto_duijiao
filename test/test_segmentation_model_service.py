# -*- coding: utf-8 -*-
"""单模型 YOLO-Seg 服务测试。"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

from backend.segmentation_model_service import (
    MAX_DETECTIONS_PER_IMAGE,
    SegmentationModelService,
)


class FakeBoxes:
    def __init__(self, classes=None, confidences=None, boxes=None):
        self.cls = np.asarray(classes or [], dtype=float)
        self.conf = np.asarray(confidences or [], dtype=float)
        self.xyxy = np.asarray(boxes or [], dtype=float).reshape((-1, 4))

    def __len__(self):
        return len(self.cls)


class FakeMasks:
    def __init__(self, polygons):
        self.xy = [np.asarray(item, dtype=float) for item in polygons]


class FakeResult:
    def __init__(self, boxes=None, masks=None):
        self.boxes = boxes
        self.masks = masks


class FakeModel:
    task = "segment"
    names = {0: "异物", 1: "脏污"}

    def __init__(self, result=None):
        self.result = result or FakeResult(FakeBoxes(), None)
        self.calls = []

    def predict(self, **kwargs):
        self.calls.append(kwargs)
        return [self.result]


class SegmentationModelServiceTest(unittest.TestCase):
    def create_model_file(self, directory, name="best.pt"):
        path = Path(directory) / name
        path.write_bytes(b"fake model")
        return path

    def test_import_does_not_load_torch_or_ultralytics(self):
        code = (
            "import json, sys; "
            "import backend.segmentation_model_service; "
            "print(json.dumps({'torch': 'torch' in sys.modules, "
            "'ultralytics': 'ultralytics' in sys.modules}))"
        )
        completed = subprocess.run(
            [sys.executable, "-B", "-c", code],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            json.loads(completed.stdout.strip()),
            {"torch": False, "ultralytics": False},
        )

    def test_load_warms_model_and_same_path_is_not_loaded_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.create_model_file(directory)
            model = FakeModel()
            factory_calls = []

            def factory(value):
                factory_calls.append(value)
                return model

            service = SegmentationModelService(factory)
            first = service.load(str(path), imgsz=1280)
            second = service.load(str(path), imgsz=1280)

        self.assertIs(first, model)
        self.assertIs(second, model)
        self.assertEqual(len(factory_calls), 1)
        self.assertEqual(len(model.calls), 1)
        self.assertEqual(model.calls[0]["imgsz"], 1280)
        self.assertEqual(model.calls[0]["max_det"], 20)
        self.assertEqual(service.class_names, {0: "异物", 1: "脏污"})

    def test_loaded_model_cannot_be_switched(self):
        with tempfile.TemporaryDirectory() as directory:
            first_path = self.create_model_file(directory, "first.pt")
            second_path = self.create_model_file(directory, "second.pt")
            service = SegmentationModelService(lambda _path: FakeModel())
            service.load(str(first_path))

            with self.assertRaisesRegex(RuntimeError, "重启软件"):
                service.load(str(second_path))

    def test_non_segmentation_model_is_rejected_without_changing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.create_model_file(directory)
            model = FakeModel()
            model.task = "detect"
            service = SegmentationModelService(lambda _path: model)

            with self.assertRaisesRegex(ValueError, "不是 YOLO-Seg"):
                service.load(str(path))

        self.assertFalse(service.is_loaded)

    def test_valid_result_is_converted_to_plain_instances(self):
        result = FakeResult(
            FakeBoxes([1], [0.875], [[10, 20, 30, 40]]),
            FakeMasks([[[10, 20], [30, 20], [30, 40], [10, 40]]]),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = self.create_model_file(directory)
            model = FakeModel(result)
            service = SegmentationModelService(lambda _path: model)
            service.load(str(path))
            instances = service.predict(np.zeros((100, 100, 3), dtype=np.uint8))

        self.assertEqual(len(instances), 1)
        instance = instances[0]
        self.assertEqual(instance.class_id, 1)
        self.assertEqual(instance.class_name, "脏污")
        self.assertAlmostEqual(instance.confidence, 0.875)
        self.assertEqual(instance.bbox, (10.0, 20.0, 30.0, 40.0))
        self.assertEqual(instance.pixel_area, 400)
        self.assertIsInstance(instance.polygon[0], tuple)
        self.assertEqual(model.calls[-1]["max_det"], 20)

    def test_predict_never_returns_more_than_twenty_instances(self):
        count = MAX_DETECTIONS_PER_IMAGE + 5
        boxes = [
            [index, index, index + 2, index + 2]
            for index in range(count)
        ]
        polygons = [
            [
                [index, index],
                [index + 2, index],
                [index + 2, index + 2],
                [index, index + 2],
            ]
            for index in range(count)
        ]
        result = FakeResult(
            FakeBoxes([0] * count, [0.9] * count, boxes),
            FakeMasks(polygons),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = self.create_model_file(directory)
            model = FakeModel(result)
            service = SegmentationModelService(lambda _path: model)
            service.load(str(path))
            instances = service.predict(
                np.zeros((100, 100, 3), dtype=np.uint8)
            )

        self.assertEqual(len(instances), MAX_DETECTIONS_PER_IMAGE)
        self.assertEqual(model.calls[-1]["max_det"], 20)

    def test_empty_detection_is_valid_but_boxes_without_masks_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.create_model_file(directory)
            model = FakeModel(FakeResult(FakeBoxes(), None))
            service = SegmentationModelService(lambda _path: model)
            service.load(str(path))
            self.assertEqual(
                service.predict(np.zeros((20, 20), dtype=np.uint8)),
                [],
            )

            model.result = FakeResult(
                FakeBoxes([0], [0.9], [[1, 1, 5, 5]]),
                None,
            )
            with self.assertRaisesRegex(RuntimeError, "没有分割 masks"):
                service.predict(np.zeros((20, 20), dtype=np.uint8))

    def test_invalid_paths_and_predict_before_load_fail(self):
        service = SegmentationModelService(lambda _path: FakeModel())
        with self.assertRaisesRegex(ValueError, "不能为空"):
            service.load("")
        with self.assertRaises(FileNotFoundError):
            service.load("missing.pt")
        with self.assertRaisesRegex(RuntimeError, "尚未加载"):
            service.predict(np.zeros((10, 10), dtype=np.uint8))


if __name__ == "__main__":
    unittest.main()
