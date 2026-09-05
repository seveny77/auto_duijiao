# -*- coding: utf-8 -*-
"""Ultralytics 设备选择和联网统计关闭测试。"""

import os
import sys
import types
import unittest
from unittest.mock import patch

from backend.ultralytics_runtime import (
    DEVICE_ENVIRONMENT_VARIABLE,
    device_predict_kwargs,
    load_yolo_class,
    resolve_yolo_device,
)


class FakeSettings(dict):
    def __init__(self):
        super().__init__(sync=True)
        self.updates = []

    def update(self, values):
        self.updates.append(dict(values))
        super().update(values)


class ReadOnlySettings(FakeSettings):
    def update(self, values):
        raise OSError("read only")


class UltralyticsRuntimeTest(unittest.TestCase):
    def test_unconfigured_runtime_uses_normal_automatic_device_selection(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(resolve_yolo_device())
            self.assertEqual(device_predict_kwargs(None), {})

    def test_environment_setting_selects_explicit_device(self):
        with patch.dict(
            os.environ,
            {DEVICE_ENVIRONMENT_VARIABLE: " 0 "},
            clear=True,
        ):
            self.assertEqual(resolve_yolo_device(), "0")
            self.assertEqual(device_predict_kwargs("0"), {"device": "0"})

    def test_load_yolo_disables_persisted_and_current_process_events(self):
        settings = FakeSettings()
        yolo_class = object()
        events = types.SimpleNamespace(enabled=True, events=[{"name": "predict"}])
        package = types.ModuleType("ultralytics")
        package.SETTINGS = settings
        package.YOLO = yolo_class
        utils = types.ModuleType("ultralytics.utils")
        utils.__path__ = []
        events_module = types.ModuleType("ultralytics.utils.events")
        events_module.events = events

        with patch.dict(sys.modules, {
            "ultralytics": package,
            "ultralytics.utils": utils,
            "ultralytics.utils.events": events_module,
        }):
            self.assertIs(load_yolo_class(), yolo_class)

        self.assertEqual(settings.updates, [{"sync": False}])
        self.assertFalse(events.enabled)
        self.assertEqual(events.events, [])

    def test_read_only_settings_still_disable_current_process_events(self):
        settings = ReadOnlySettings()
        events = types.SimpleNamespace(enabled=True, events=[{"name": "predict"}])
        package = types.ModuleType("ultralytics")
        package.SETTINGS = settings
        package.YOLO = object()
        utils = types.ModuleType("ultralytics.utils")
        utils.__path__ = []
        events_module = types.ModuleType("ultralytics.utils.events")
        events_module.events = events

        with (
            patch.dict(sys.modules, {
                "ultralytics": package,
                "ultralytics.utils": utils,
                "ultralytics.utils.events": events_module,
            }),
            self.assertLogs("backend.ultralytics_runtime", level="WARNING"),
        ):
            load_yolo_class()

        self.assertFalse(events.enabled)
        self.assertEqual(events.events, [])


if __name__ == "__main__":
    unittest.main()
