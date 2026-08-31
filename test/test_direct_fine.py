# -*- coding: utf-8 -*-

import threading
import time

import numpy as np

from autofocus_sim import FakeMotionBackend
from backend.detection import detect_local_roi
from backend.direct_fine import DirectFineCollector


class TriggerCamera:
    def __init__(self):
        self.callback = None
        self.grabbing = False

    def set_trigger_mode(self, mode):
        assert mode == "hardware"

    def register_frame_callback(self, callback):
        self.callback = callback

    def start_grabbing(self):
        self.grabbing = True

    def stop_grabbing(self):
        self.grabbing = False

    def emit(self, images):
        assert self.grabbing
        for image in images:
            self.callback(image)


class MeanEvaluator:
    def __init__(self):
        self.rois = []

    def evaluate_image(self, image, roi=None):
        self.rois.append(roi)
        x, y, width, height = roi
        return float(image[y:y + height, x:x + width].mean())


def test_first_frame_detects_roi_and_also_participates():
    camera = TriggerCamera()
    evaluator = MeanEvaluator()
    detector_calls = []

    def detector(image):
        detector_calls.append(image)
        return (1, 1, 2, 2), "detect", (1.0, 1.0, 3.0, 3.0)

    collector = DirectFineCollector(
        camera,
        evaluator,
        detector,
        expected_count=3,
    )
    collector.start()
    images = [
        np.full((4, 4), value, dtype=np.uint8)
        for value in (30, 10, 20)
    ]
    camera.emit(images)
    assert collector.wait(2.0)
    assert collector.stop()

    result = collector.result()
    assert len(detector_calls) == 1
    assert result.processed_count == 3
    assert result.best_index == 0
    assert result.best_score == 30.0
    assert result.roi_source == "detect"
    assert evaluator.rois == [(1, 1, 2, 2)] * 3


def test_frames_queue_while_first_frame_detector_is_busy():
    camera = TriggerCamera()
    evaluator = MeanEvaluator()

    def slow_detector(image):
        time.sleep(0.05)
        return (0, 0, 4, 4), "fallback_preset", None

    collector = DirectFineCollector(
        camera,
        evaluator,
        slow_detector,
        expected_count=40,
    )
    collector.start()
    camera.emit([
        np.full((4, 4), index, dtype=np.uint8)
        for index in range(40)
    ])
    assert collector.wait(2.0)
    assert collector.stop()

    result = collector.result()
    assert result.received_count == 40
    assert result.enqueued_count == 40
    assert result.dropped_count == 0
    assert result.processed_count == 40
    assert result.best_index == 39
    assert result.roi_source == "fallback_preset"


def test_detect_local_roi_falls_back_to_whole_preset_image():
    image = np.zeros((80, 120, 3), dtype=np.uint8)
    roi, source, box = detect_local_roi(
        image=image,
        conf=0.5,
        model=None,
    )
    assert roi == (0, 0, 120, 80)
    assert source == "fallback_preset"
    assert box is None


def test_detect_local_roi_uses_highest_confidence_box_and_local_coordinates():
    class Boxes:
        conf = np.array([0.2, 0.9], dtype=np.float32)
        xyxy = np.array([
            [1.0, 2.0, 10.0, 12.0],
            [20.4, 30.6, 70.2, 90.8],
        ], dtype=np.float32)

        def __len__(self):
            return 2

    class Result:
        boxes = Boxes()

    class Model:
        def predict(self, **kwargs):
            return [Result()]

    image = np.zeros((100, 120, 3), dtype=np.uint8)
    roi, source, box = detect_local_roi(
        image=image,
        conf=0.5,
        model=Model(),
    )
    assert roi == (20, 31, 50, 60)
    assert source == "detect"
    assert box is not None
    assert np.allclose(box, (20.4, 30.6, 70.2, 90.8))


def test_explicit_flyscan_velocity_does_not_change_existing_signature():
    backend = FakeMotionBackend(0, 1000)
    count = backend.linear_fly_scan(
        100,
        300,
        5,
        timeout_s=1.0,
        velocity_um_s=500.0,
    )
    assert count == 40
    assert backend.last_flyscan == (100, 300, 5)
    assert backend.last_flyscan_velocity_um_s == 500.0
