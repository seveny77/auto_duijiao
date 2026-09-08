# -*- coding: utf-8 -*-
"""新版尺寸化工艺规则的数据与配置验证。"""

import json
import unittest

from backend.inspection_config import (
    InspectionConfig,
    default_inspection_size_rules,
    inspection_config_from_dict,
)
from backend.inspection_types import InspectionSizeRule, inspection_to_dict


class InspectionSizeRulesTest(unittest.TestCase):
    def test_default_rules_match_confirmed_process_table(self):
        rules = default_inspection_size_rules()
        self.assertEqual(len(rules), 13)

        by_id = {rule.rule_id: rule for rule in rules}
        self.assertIsNone(by_id["B-spot-small"].max_instance_count)
        self.assertEqual(by_id["B-spot-medium"].max_instance_count, 3)
        self.assertEqual(by_id["B-spot-large"].max_instance_count, 0)
        self.assertFalse(by_id["B-spot-small"].max_inclusive)
        self.assertTrue(by_id["B-spot-medium"].min_inclusive)
        self.assertTrue(by_id["B-spot-medium"].max_inclusive)
        self.assertFalse(by_id["B-spot-large"].min_inclusive)

        self.assertIsNone(by_id["D-spot-small"].max_instance_count)
        self.assertEqual(by_id["D-spot-medium"].max_instance_count, 3)
        self.assertEqual(by_id["D-spot-large"].max_instance_count, 0)
        self.assertIsNone(by_id["C-spot-any"].max_instance_count)
        self.assertIsNone(by_id["C-scratch-any"].max_instance_count)

    def test_rules_are_not_shared_between_configs(self):
        first = InspectionConfig()
        second = InspectionConfig()
        first.size_rules[0].region_name = "被修改的名称"

        self.assertEqual(second.size_rules[0].region_name, "关键区")

    def test_default_size_rules_validate(self):
        config = InspectionConfig(mm_per_pixel=0.242)
        self.assertEqual(config.validate_evaluation(), [])

    def test_unlimited_is_serialized_as_json_null(self):
        payload = inspection_to_dict(InspectionConfig())
        encoded = json.dumps(payload, ensure_ascii=False)

        self.assertIn('"max_instance_count": null', encoded)
        self.assertEqual(payload["size_rules"][2]["max_instance_count"], None)

    def test_loads_default_rules_for_legacy_config(self):
        restored = inspection_config_from_dict({"mm_per_pixel": 0.242})
        self.assertEqual(len(restored.size_rules), 13)
        self.assertEqual(restored.size_rules[0].rule_id, "A-spot-any")

    def test_rejects_overlap_at_size_boundary(self):
        rules = default_inspection_size_rules()
        for rule in rules:
            if rule.rule_id == "B-spot-large":
                rule.min_inclusive = True
        errors = InspectionConfig(mm_per_pixel=0.242, size_rules=rules).validate()

        self.assertTrue(any("10 µm 边界" in error for error in errors))

    def test_rule_is_directly_json_serializable(self):
        rule = InspectionSizeRule(
            rule_id="example",
            region_id="A",
            region_name="关键区",
            inner_radius_um=0.0,
            outer_radius_um=25.0,
            defect_class="污点",
            measurement="equivalent_diameter_um",
            max_instance_count=None,
        )
        self.assertEqual(
            json.loads(json.dumps(inspection_to_dict(rule), ensure_ascii=False)),
            {
                "rule_id": "example",
                "region_id": "A",
                "region_name": "关键区",
                "inner_radius_um": 0.0,
                "outer_radius_um": 25.0,
                "defect_class": "污点",
                "measurement": "equivalent_diameter_um",
                "min_size_um": 0.0,
                "max_size_um": None,
                "min_inclusive": True,
                "max_inclusive": True,
                "max_instance_count": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
