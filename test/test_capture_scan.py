# -*- coding: utf-8 -*-
"""全图采集脚本测试：参数换算 + sim 模式保存 200 张 JPG"""

import os
import sys

import cv2
import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from capture_scan import compute_capture_params, main  # noqa: E402


class TestComputeParams:
    def test_default_200_frames(self):
        assert compute_capture_params(10000, 11000, 5) == (10000, 11000, 5, 200)

    def test_floor_when_not_divisible(self):
        assert compute_capture_params(10000, 11001, 5) == (10000, 11000, 5, 200)

    def test_invalid_args(self):
        with pytest.raises(ValueError):
            compute_capture_params(11000, 10000, 5)
        with pytest.raises(ValueError):
            compute_capture_params(10000, 11000, 0)


class TestSimCapture:
    def test_sim_saves_200_jpgs(self, tmp_path):
        rc = main(
            [
                "--mode", "sim",
                "--out-dir", str(tmp_path),
                "--yes",
                "--frame-wait-timeout", "30",
            ]
        )
        assert rc == 0
        files = sorted(os.listdir(tmp_path))
        assert len(files) == 200
        assert files[0] == "img_0000.jpg"
        assert files[-1] == "img_0199.jpg"
        arr = np.fromfile(str(tmp_path / "img_0000.jpg"), dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        assert img is not None

    def test_sim_saves_with_start_index(self, tmp_path):
        rc = main(
            [
                "--mode", "sim",
                "--out-dir", str(tmp_path),
                "--yes",
                "--start-index", "200",
                "--frame-wait-timeout", "30",
            ]
        )
        assert rc == 0
        files = sorted(os.listdir(tmp_path))
        assert len(files) == 200
        assert files[0] == "img_0200.jpg"
        assert files[-1] == "img_0399.jpg"
