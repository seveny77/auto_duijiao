# -*- coding: utf-8 -*-
"""检测页配置回填、编辑和保存请求测试。"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QTableWidgetItem

from backend.inspection_config import InspectionConfig
from backend.inspection_types import (
    CircleCandidate,
    InspectionRegionRule,
    InspectionResult,
)
from gui.app.widgets.inspection_panel import InspectionPanel


def _valid_config():
    config = InspectionConfig(mm_per_pixel=0.0125)
    config.model_path = r"models\best.pt"
    config.circle.min_radius_px = 800
    config.circle.max_radius_px = 2400
    config.circle.expected_circle_count = 1
    config.circle.hough_param1 = 77.0
    for region_id, name, inner, outer in (
        ("center", "中心区", 0.0, 10.0),
        ("outer", "外环区", 10.0, 20.0),
    ):
        for class_id, class_name in ((0, "异物"), (1, "脏污")):
            config.region_rules.append(InspectionRegionRule(
                region_id=region_id,
                region_name=name,
                inner_radius_mm=inner,
                outer_radius_mm=outer,
                class_id=class_id,
                class_name=class_name,
                min_confidence=0.3 + class_id * 0.1,
                min_instance_area_mm2=0.01,
                max_instance_count=class_id,
            ))
    return config


class InspectionPanelConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.panel = InspectionPanel()

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()

    def test_config_round_trip_preserves_hidden_values(self):
        config = _valid_config()
        self.panel.set_inspection_config(config)

        rebuilt = self.panel.build_inspection_config()

        self.assertEqual(rebuilt.model_path, config.model_path)
        self.assertEqual(rebuilt.circle.hough_param1, 77.0)
        self.assertEqual(rebuilt.circle.min_radius_px, 800)
        self.assertEqual(len(rebuilt.region_rules), 4)
        self.assertEqual(rebuilt.validate(), [])

    def test_visible_edits_are_collected(self):
        self.panel.set_inspection_config(_valid_config())
        self.panel.mm_per_pixel_spin.setValue(0.02)
        self.panel.min_radius_spin.setValue(900)
        self.panel.rule_table.item(0, 2).setText("0.55")
        self.panel.rule_table.item(0, 4).setText("3")

        rebuilt = self.panel.build_inspection_config()

        self.assertAlmostEqual(rebuilt.mm_per_pixel, 0.02)
        self.assertEqual(rebuilt.circle.min_radius_px, 900)
        self.assertAlmostEqual(rebuilt.region_rules[0].min_confidence, 0.55)
        self.assertEqual(rebuilt.region_rules[0].max_instance_count, 3)

    def test_add_and_remove_region_rebuild_rules(self):
        self.panel.set_inspection_config(_valid_config())
        self.panel._add_region()

        self.assertEqual(self.panel.region_table.rowCount(), 3)
        self.assertEqual(self.panel.rule_table.rowCount(), 6)
        self.assertEqual(self.panel.region_table.item(2, 1).text(), "20")
        self.assertEqual(self.panel.region_table.item(2, 2).text(), "30")

        self.panel.region_table.selectRow(2)
        self.panel._remove_region()
        self.assertEqual(self.panel.region_table.rowCount(), 2)
        self.assertEqual(self.panel.rule_table.rowCount(), 4)

        self.panel.region_table.selectRow(0)
        self.panel._remove_region()
        self.assertEqual(self.panel.region_table.item(0, 1).text(), "0")
        self.assertEqual(self.panel.build_inspection_config().validate(), [])

    def test_model_classes_rebuild_region_class_cross_product(self):
        self.panel.set_inspection_config(_valid_config())
        self.panel.set_model_loaded(
            "best.pt",
            {"class_names": {0: "划伤", 2: "颗粒"}},
        )

        self.assertEqual(self.panel.rule_table.rowCount(), 4)
        class_ids = {
            self.panel.rule_table.item(row, 1).data(Qt.UserRole)
            for row in range(self.panel.rule_table.rowCount())
        }
        self.assertEqual(class_ids, {0, 2})

    def test_invalid_cell_emits_error_without_save_request(self):
        self.panel.set_inspection_config(_valid_config())
        self.panel.rule_table.setItem(0, 2, QTableWidgetItem("not-a-number"))
        errors = []
        saves = []
        self.panel.inspection_config_invalid.connect(errors.append)
        self.panel.inspection_config_save_requested.connect(saves.append)

        self.panel._request_config_save()

        self.assertEqual(len(errors), 1)
        self.assertEqual(saves, [])

    def test_valid_edit_requests_lightweight_recalculation(self):
        self.panel.set_inspection_config(_valid_config())
        self.panel._inspection_result = InspectionResult()
        requests = []
        self.panel.inspection_recalculate_requested.connect(requests.append)
        self.panel.rule_table.item(0, 2).setText("0.66")

        QTest.qWait(300)

        self.assertEqual(len(requests), 1)
        self.assertAlmostEqual(
            requests[0].region_rules[0].min_confidence,
            0.66,
        )
        self.assertEqual(requests[0].circle.hough_param1, 77.0)

    def test_invalid_edit_does_not_request_recalculation(self):
        self.panel.set_inspection_config(_valid_config())
        self.panel._inspection_result = InspectionResult()
        requests = []
        self.panel.inspection_recalculate_requested.connect(requests.append)
        self.panel.mm_per_pixel_spin.setValue(0.0)

        self.panel._emit_recalculation_request()

        self.assertEqual(requests, [])

    def test_circle_controls_show_best_candidate_and_request_redetection(self):
        self.panel.set_inspection_config(_valid_config())
        self.panel._original_image = object()
        result = InspectionResult(
            circle_candidates=[CircleCandidate(
                center_x=100.0,
                center_y=120.0,
                radius_px=800.0,
                score=0.42,
                source="hough",
            )],
            selected_circle_index=0,
            circle_confirmed=False,
        )
        self.panel._inspection_result = result
        self.panel._update_circle_controls(result)
        requests = []
        self.panel.circle_redetection_requested.connect(requests.append)

        self.assertTrue(self.panel.find_circle_btn.isEnabled())
        self.assertTrue(self.panel.confirm_circle_btn.isEnabled())
        self.assertIn("评分=0.420", self.panel.circle_candidate_combo.currentText())
        self.panel._request_circle_redetection()
        self.assertEqual(len(requests), 1)

        self.panel.set_circle_redetecting()
        self.assertFalse(self.panel.find_circle_btn.isEnabled())
        self.assertFalse(self.panel.confirm_circle_btn.isEnabled())

    def test_confirmed_circle_disables_confirmation_button(self):
        self.panel._original_image = object()
        result = InspectionResult(
            circle_candidates=[CircleCandidate(score=0.9)],
            selected_circle_index=0,
            circle_confirmed=True,
        )

        self.panel._update_circle_controls(result)

        self.assertFalse(self.panel.confirm_circle_btn.isEnabled())
        self.assertEqual(self.panel.confirm_circle_btn.text(), "当前圆心已确认")


if __name__ == "__main__":
    unittest.main()
