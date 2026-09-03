# -*- coding: utf-8 -*-
"""清晰度 ROI 在 GUI 配置中的保存、恢复和任务配置传递测试。"""

import json
import tempfile
import unittest
from pathlib import Path

from gui.app.services.config_service import ConfigService


class _ValueWidget:
    def __init__(self, value=None):
        self._value = value

    def value(self):
        return self._value

    def setValue(self, value):
        self._value = value

    def text(self):
        return self._value

    def setText(self, value):
        self._value = value

    def currentText(self):
        return self._value

    def setCurrentText(self, value):
        self._value = value

    def isChecked(self):
        return bool(self._value)

    def setChecked(self, value):
        self._value = bool(value)


class _Tabs:
    def __init__(self):
        self._index = 0

    def currentIndex(self):
        return self._index

    def setCurrentIndex(self, value):
        self._index = value


class _Panel:
    ncc_tab_index = 0
    ai_tab_index = 1

    def __init__(self):
        self.strategy_tabs = _Tabs()
        self.action_combo = _ValueWidget("搜索对焦")
        self.ncc_action_combo = _ValueWidget("NCC搜索")
        self.mode_combo = _ValueWidget("真实")
        self.skip_confirm_check = _ValueWidget(True)
        self.exposure_spin = _ValueWidget(3000)
        self.gain_spin = _ValueWidget(0.0)
        self.decimation_combo = _ValueWidget("1x1")
        self.work_roi_width_spin = _ValueWidget(640)
        self.work_roi_height_spin = _ValueWidget(480)
        self.search_start_spin = _ValueWidget(9500)
        self.search_span_spin = _ValueWidget(2000)
        self.fine_step_spin = _ValueWidget(5)
        self.fine_half_spin = _ValueWidget(5)
        self.coarse_step_spin = _ValueWidget(100)
        self.save_edit = _ValueWidget("")
        self.template_edit = _ValueWidget("data/template_sim.json")
        self.calibrate_step_spin = _ValueWidget(20)
        self.calibrate_ds_combo = _ValueWidget("decimation 4")
        self.dl_model_edit = _ValueWidget("assets/models/ai/best_resnet.pt")
        self.shot_position_spin = _ValueWidget(12000)


class _ImageWidget:
    def __init__(self):
        self.evaluation_roi = None

    def set_evaluation_roi(self, roi, emit_signal=True):
        self.evaluation_roi = roi


class ConfigServiceEvaluationRoiTest(unittest.TestCase):
    def test_save_load_and_build_focus_config_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "config.json")
            panel = _Panel()
            image_widget = _ImageWidget()
            image_widget.evaluation_roi = (10, 20, 300, 200)
            service = ConfigService(path, panel, directory, image_widget)

            self.assertTrue(service.save())
            stored = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(stored["evaluation_roi"], [10, 20, 300, 200])

            restored_image_widget = _ImageWidget()
            restored = ConfigService(
                path,
                _Panel(),
                directory,
                restored_image_widget,
            )
            self.assertTrue(restored.load())
            self.assertEqual(
                restored_image_widget.evaluation_roi,
                (10, 20, 300, 200),
            )
            self.assertEqual(
                restored.build_focus_config().evaluation_roi,
                (10, 20, 300, 200),
            )

    def test_invalid_stored_roi_is_reset_to_pending_full_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps({"evaluation_roi": [0, 0, 0, 100]}),
                encoding="utf-8",
            )
            image_widget = _ImageWidget()
            service = ConfigService(
                str(path),
                _Panel(),
                directory,
                image_widget,
            )
            self.assertTrue(service.load())
            self.assertIsNone(image_widget.evaluation_roi)


if __name__ == "__main__":
    unittest.main()
