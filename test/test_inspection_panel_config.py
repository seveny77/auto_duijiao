# -*- coding: utf-8 -*-
"""检测页配置回填、编辑和保存请求测试。"""

import os
import sys
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QTableWidgetItem

from backend.inspection_config import InspectionConfig
from backend.inspection_types import (
    CircleCandidate,
    CircleInspectionResult,
    ImageInspectionResult,
    InspectionRegionRule,
    InspectionResult,
    InspectionStatus,
    RegionInspectionResult,
    RoiRegion,
    SegmentationInstance,
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

    def test_multi_circle_result_has_no_default_selection_and_shows_all_rows(self):
        config = _valid_config()
        config.circle.expected_circle_count = 2
        result = ImageInspectionResult(
            image_id="inspection-000001",
            image_width=200,
            image_height=100,
            expected_circle_count=2,
            detected_circle_count=2,
            completed_circle_count=2,
            is_complete=True,
            status=InspectionStatus.FAIL,
            timings_ms={"inference": 12.0, "total": 20.0},
            circle_results=[
                CircleInspectionResult(
                    circle_id="circle-001",
                    circle_candidate=CircleCandidate(
                        center_x=40,
                        center_y=50,
                        radius_px=15,
                        score=0.9,
                    ),
                    roi=RoiRegion(x=20, y=30, width=40, height=40),
                    completed=True,
                    circle_confirmed=True,
                    status=InspectionStatus.PASS,
                    instances=[SegmentationInstance(
                        polygon=[(25, 35), (55, 35), (55, 65)],
                    )],
                    region_results=[RegionInspectionResult(
                        region_id="center",
                        region_name="中心区",
                        class_id=0,
                        class_name="异物",
                        valid_instance_count=1,
                    )],
                ),
                CircleInspectionResult(
                    circle_id="circle-002",
                    circle_candidate=CircleCandidate(
                        center_x=140,
                        center_y=50,
                        radius_px=15,
                        score=0.8,
                    ),
                    roi=RoiRegion(x=120, y=30, width=40, height=40),
                    completed=True,
                    circle_confirmed=True,
                    status=InspectionStatus.FAIL,
                    instances=[SegmentationInstance(
                        polygon=[(125, 35), (155, 35), (155, 65)],
                    )],
                    region_results=[RegionInspectionResult(
                        region_id="center",
                        region_name="中心区",
                        class_id=0,
                        class_name="异物",
                        valid_instance_count=3,
                        passed=False,
                    )],
                ),
            ],
        )

        self.panel.present_image_inspection_result(
            result.image_id,
            np.zeros((100, 200, 3), dtype=np.uint8),
            result,
            config,
        )

        self.assertEqual(self.panel.circle_result_table.rowCount(), 2)
        self.assertEqual(self.panel.circle_result_table.currentRow(), -1)
        self.assertEqual(
            self.panel.circle_detail_label.text(),
            "请选择端面查看详细结果",
        )
        self.assertEqual(self.panel.verdict_label.text(), "不合格")
        self.assertEqual(self.panel.rule_table.item(0, 5).text(), "--")
        self.assertEqual(
            self.panel.circle_result_table.item(0, 0).text(),
            "circle-001",
        )
        self.assertEqual(
            self.panel.circle_result_table.item(1, 1).text(),
            "不合格",
        )

    def test_multi_circle_selection_only_updates_right_side_details(self):
        config = _valid_config()
        config.circle.expected_circle_count = 2
        result = ImageInspectionResult(
            image_id="inspection-000002",
            image_width=100,
            image_height=60,
            expected_circle_count=2,
            detected_circle_count=2,
            completed_circle_count=2,
            is_complete=True,
            status=InspectionStatus.FAIL,
            circle_results=[
                CircleInspectionResult(
                    circle_id="circle-001",
                    status=InspectionStatus.PASS,
                    completed=True,
                ),
                CircleInspectionResult(
                    circle_id="circle-002",
                    status=InspectionStatus.FAIL,
                    completed=True,
                    region_results=[RegionInspectionResult(
                        region_id="center",
                        region_name="中心区",
                        class_id=0,
                        class_name="异物",
                        valid_instance_count=3,
                        passed=False,
                    )],
                ),
            ],
        )
        self.panel.present_image_inspection_result(
            result.image_id,
            np.zeros((60, 100, 3), dtype=np.uint8),
            result,
            config,
        )
        pixmap_key = self.panel._image_item.pixmap().cacheKey()

        self.panel.circle_result_table.selectRow(1)
        self.app.processEvents()

        self.assertIn("circle-002", self.panel.circle_detail_label.text())
        self.assertEqual(self.panel.rule_table.item(0, 5).text(), "3")
        self.assertEqual(
            self.panel._image_item.pixmap().cacheKey(),
            pixmap_key,
        )


if __name__ == "__main__":
    unittest.main()
