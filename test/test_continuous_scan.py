import threading
import unittest

from autofocus_sim import FakeMotionBackend
from motion import ContinuousScanResult


class ContinuousScanTests(unittest.TestCase):
    def test_fake_backend_uses_start_end_and_velocity_only(self):
        backend = FakeMotionBackend(0, 1000)

        result = backend.continuous_scan(
            100,
            300,
            timeout_s=1.0,
            velocity_um_s=500.0,
        )

        self.assertIsInstance(result, ContinuousScanResult)
        self.assertEqual(result.start_um, 100)
        self.assertEqual(result.end_um, 300)
        self.assertEqual(result.actual_end_um, 300.0)
        self.assertEqual(result.velocity_um_s, 500.0)
        self.assertEqual(
            backend.last_continuous_scan,
            (100, 300, 500.0),
        )
        self.assertEqual(backend.get_state().position_um, 300.0)

    def test_cancel_is_honored_before_motion(self):
        backend = FakeMotionBackend(0, 1000)
        cancel = threading.Event()
        cancel.set()

        with self.assertRaisesRegex(RuntimeError, "用户取消"):
            backend.continuous_scan(100, 300, 1.0, cancel_event=cancel)


if __name__ == "__main__":
    unittest.main()
