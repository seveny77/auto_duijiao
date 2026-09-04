import os
from datetime import datetime
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from backend import pipeline
from backend.config import FocusConfig


class FinalImageSaveTests(unittest.TestCase):
    def test_existing_two_argument_call_saves_unique_timestamped_images(self):
        image = np.full((12, 18), 37, dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "中文原图"
            with patch.object(pipeline, "datetime") as clock:
                clock.now.side_effect = [
                    datetime(2026, 9, 4, 10, 13, 9, 141000),
                    datetime(2026, 9, 4, 10, 13, 9, 142000),
                ]
                first = pipeline.save_timestamped_final_image(image, str(output_dir))
                second = pipeline.save_timestamped_final_image(image, str(output_dir))

            self.assertNotEqual(first, second)
            self.assertEqual(Path(first).name, "20260904_101309_141000.jpg")
            self.assertEqual(Path(second).name, "20260904_101309_142000.jpg")
            for filename in (first, second):
                self.assertTrue(os.path.isabs(filename))
                decoded = cv2.imdecode(np.fromfile(filename, dtype=np.uint8), 0)
                np.testing.assert_array_equal(decoded, image)

    def test_preselected_path_is_used_without_generating_another_timestamp(self):
        image = np.zeros((8, 8), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "原图" / "20260904_101309_141000.jpg"
            with patch.object(pipeline, "datetime") as clock:
                saved = pipeline.save_timestamped_final_image(
                    image, str(target.parent), output_path=str(target)
                )

            self.assertEqual(saved, str(target))
            self.assertTrue(target.is_file())
            clock.now.assert_not_called()


class ContinuousFinalImagePathTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.directory.cleanup)
        self.image = np.full((8, 12), 25, dtype=np.uint8)
        self.events = []
        self.motion = Mock()
        self.motion.is_connected.return_value = True
        self.motion.continuous_scan.return_value = SimpleNamespace(actual_end_um=300.0)
        self.collector = Mock()
        self.collector.result.return_value = SimpleNamespace(
            best_image=self.image,
            best_index=2,
            best_score=25.0,
            evaluation_roi_local=(0, 0, 12, 8),
            processed_count=3,
        )
        self.cfg = FocusConfig(
            search_start_um=100,
            search_span_um=200,
            save_dir=self.directory.name,
            camera=Mock(),
            motion_backend=self.motion,
            best_frame_ready_callback=self.events.append,
        )
        collector_patch = patch.object(
            pipeline, "SoftwareBestFrameCollector", return_value=self.collector
        )
        collector_patch.start()
        self.addCleanup(collector_patch.stop)

    def test_event_precedes_write_and_return_and_shares_the_absolute_path(self):
        self.cfg.save_dir = os.path.relpath(self.directory.name)
        sequence = []
        real_save = pipeline.save_jpg

        def ready(event):
            self.events.append(event)
            sequence.append("ready")
            self.assertTrue(os.path.isabs(event.final_image_path))
            self.assertFalse(Path(event.final_image_path).exists())

        def save(image, path):
            sequence.append("save")
            self.assertIs(image, self.image)
            self.assertEqual(path, self.events[0].final_image_path)
            real_save(image, path)

        self.cfg.best_frame_ready_callback = ready
        self.motion.move_to_position.side_effect = lambda *args, **kwargs: sequence.append("move")
        with patch.object(pipeline, "save_jpg", side_effect=save):
            result = pipeline.run_search(self.cfg)

        self.assertEqual(result.rc, 0)
        self.assertEqual(sequence, ["move", "ready", "save", "move"])
        self.assertEqual(result.final_image_path, self.events[0].final_image_path)
        self.assertTrue(Path(result.final_image_path).is_file())
        self.assertIs(result.final_image, self.image)
        np.testing.assert_array_equal(self.image, np.full((8, 12), 25, dtype=np.uint8))

    def test_disabled_save_has_no_target_and_does_not_write(self):
        self.cfg.save_dir = None
        with patch.object(pipeline, "save_jpg") as save:
            result = pipeline.run_search(self.cfg)

        self.assertEqual(result.rc, 0)
        self.assertIsNone(result.final_image_path)
        self.assertIsNone(self.events[0].final_image_path)
        save.assert_not_called()

    def test_write_failure_preserves_pairing_path_and_still_returns_axis(self):
        with patch.object(pipeline, "save_jpg", side_effect=OSError("disk full")):
            with self.assertLogs(pipeline.logger, level="ERROR"):
                result = pipeline.run_search(self.cfg)

        self.assertEqual(result.rc, 0)
        self.assertEqual(result.final_image_path, self.events[0].final_image_path)
        self.assertFalse(Path(result.final_image_path).exists())
        self.assertEqual(self.motion.move_to_position.call_count, 2)

    def test_axis_return_failure_keeps_early_event_and_saved_image_associated(self):
        self.motion.move_to_position.side_effect = [None, RuntimeError("return failed")]
        with self.assertLogs(pipeline.logger, level="ERROR"):
            result = pipeline.run_search(self.cfg)

        self.assertEqual(result.rc, 1)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(result.final_image_path, self.events[0].final_image_path)
        self.assertTrue(Path(result.final_image_path).is_file())
        self.motion.cancel_current_motion.assert_called_once()


if __name__ == "__main__":
    unittest.main()
