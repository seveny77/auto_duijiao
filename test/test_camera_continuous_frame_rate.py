"""连续采集帧率节点的纯模拟测试，不连接真实相机。"""

import unittest

from camera.camera_adapter import HikCamera


class _FakeCameraSdk:
    def __init__(self):
        self.acquisition_rate = 20.0
        self.resulting_rate = 18.5
        self.frame_rate_enabled = False
        self.last_set_rate = None

    def MV_CC_GetFloatValue(self, node_name, value):
        if node_name == "AcquisitionFrameRate":
            value.fMin = 1.0
            value.fMax = 60.0
            value.fInc = 0.5
            value.fCurValue = self.acquisition_rate
            return 0
        if node_name == "ResultingFrameRate":
            value.fMin = 0.0
            value.fMax = 60.0
            value.fInc = 0.0
            value.fCurValue = self.resulting_rate
            return 0
        return 1

    def MV_CC_SetBoolValue(self, node_name, enabled):
        if node_name != "AcquisitionFrameRateEnable":
            return 1
        self.frame_rate_enabled = bool(enabled)
        return 0

    def MV_CC_GetBoolValue(self, node_name, value):
        if node_name != "AcquisitionFrameRateEnable":
            return 1
        value.value = self.frame_rate_enabled
        return 0

    def MV_CC_SetFloatValue(self, node_name, value):
        if node_name != "AcquisitionFrameRate":
            return 1
        self.last_set_rate = float(value)
        self.acquisition_rate = float(value)
        return 0


class ContinuousFrameRateTests(unittest.TestCase):
    def setUp(self):
        self.sdk = _FakeCameraSdk()
        self.camera = HikCamera()
        self.camera._cam = self.sdk

    def test_reads_supported_range_and_actual_rate(self):
        info = self.camera.get_continuous_frame_rate_info()

        self.assertEqual(1.0, info.minimum_fps)
        self.assertEqual(60.0, info.maximum_fps)
        self.assertEqual(0.5, info.increment_fps)
        self.assertEqual(20.0, info.configured_fps)
        self.assertEqual(18.5, info.resulting_fps)

    def test_sets_enables_and_reads_back_frame_rate(self):
        actual = self.camera.set_continuous_frame_rate(30.0)

        self.assertTrue(self.sdk.frame_rate_enabled)
        self.assertEqual(30.0, self.sdk.last_set_rate)
        self.assertEqual(30.0, actual.configured_fps)

    def test_rejects_unsupported_rate_and_setting_while_grabbing(self):
        with self.assertRaises(ValueError):
            self.camera.set_continuous_frame_rate(61.0)

        self.camera._is_grabbing = True
        with self.assertRaises(RuntimeError):
            self.camera.set_continuous_frame_rate(30.0)


if __name__ == "__main__":
    unittest.main()
