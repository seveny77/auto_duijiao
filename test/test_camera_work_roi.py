import unittest

from backend.camera_utils import (
    RoiAlignmentError,
    resolve_work_roi,
    set_coarse_frame,
    set_full_frame,
)


class _FakeCamera:
    def __init__(self):
        self.calls = []

    def set_binning(self, horizontal, vertical):
        self.calls.append(("binning", horizontal, vertical))

    def set_decimation(self, horizontal, vertical):
        self.calls.append(("decimation", horizontal, vertical))

    def set_roi(self, x, y, width, height):
        self.calls.append(("roi", x, y, width, height))


class CameraWorkRoiTests(unittest.TestCase):
    def test_zero_size_uses_5120_full_frame(self):
        self.assertEqual(
            resolve_work_roi(0, 0),
            (0, 0, 5120, 5120),
        )

    def test_work_roi_is_centered_in_full_resolution(self):
        self.assertEqual(
            resolve_work_roi(2048, 2048),
            (1536, 1536, 2048, 2048),
        )

    def test_non_aligned_size_is_aligned_down(self):
        self.assertEqual(
            resolve_work_roi(2050, 2050),
            (1536, 1536, 2048, 2048),
        )

    def test_only_one_zero_dimension_is_rejected(self):
        with self.assertRaises(RoiAlignmentError):
            resolve_work_roi(2048, 0)

    def test_coarse_decimation_uses_scaled_work_roi(self):
        cam = _FakeCamera()

        sensor_size = set_coarse_frame(
            cam,
            "decimation",
            4,
            2048,
            2048,
        )

        self.assertEqual(sensor_size, (5120, 5120))
        self.assertEqual(
            cam.calls[-1],
            ("roi", 384, 384, 512, 512),
        )

    def test_fine_and_final_use_full_resolution_work_roi(self):
        cam = _FakeCamera()

        sensor_size = set_full_frame(
            cam,
            1,
            2048,
            2048,
        )

        self.assertEqual(sensor_size, (5120, 5120))
        self.assertEqual(
            cam.calls[-1],
            ("roi", 1536, 1536, 2048, 2048),
        )


if __name__ == "__main__":
    unittest.main()
