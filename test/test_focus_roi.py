# -*- coding: utf-8 -*-
"""清晰度评价 ROI 数据契约测试。"""

import unittest

from backend.config import FocusConfig
from backend.focus_roi import (
    evaluation_roi_fits_image,
    fit_evaluation_roi,
    full_frame_roi,
    normalize_evaluation_roi,
)
from backend.result import SearchResult


class FocusRoiTest(unittest.TestCase):
    def test_normalizes_json_list_to_tuple(self):
        self.assertEqual(
            normalize_evaluation_roi([10, 20, 300, 200]),
            (10, 20, 300, 200),
        )

    def test_rejects_invalid_values(self):
        invalid_values = (
            [1, 2, 3],
            [-1, 0, 10, 10],
            [0, 0, 0, 10],
            [True, 0, 10, 10],
            [0.5, 0, 10, 10],
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_evaluation_roi(value)

    def test_missing_or_out_of_bounds_roi_falls_back_to_full_frame(self):
        self.assertEqual(fit_evaluation_roi(None, 640, 480), (0, 0, 640, 480))
        self.assertEqual(
            fit_evaluation_roi((500, 400, 200, 100), 640, 480),
            (0, 0, 640, 480),
        )
        self.assertEqual(
            fit_evaluation_roi((20, 30, 200, 100), 640, 480),
            (20, 30, 200, 100),
        )

    def test_roi_contract_is_available_on_config_and_result(self):
        config = FocusConfig(evaluation_roi=(10, 20, 30, 40))
        result = SearchResult(evaluation_roi=(1, 2, 3, 4))
        self.assertEqual(config.evaluation_roi, (10, 20, 30, 40))
        self.assertEqual(result.evaluation_roi, (1, 2, 3, 4))
        self.assertEqual(full_frame_roi(100, 80), (0, 0, 100, 80))
        self.assertTrue(evaluation_roi_fits_image((1, 2, 3, 4), 100, 80))


if __name__ == "__main__":
    unittest.main()
