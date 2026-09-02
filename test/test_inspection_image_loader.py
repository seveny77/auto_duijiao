# -*- coding: utf-8 -*-
"""离线质检图片读取测试。"""

import ast
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from gui.app.services.inspection_image_loader import load_inspection_image


class InspectionImageLoaderTest(unittest.TestCase):
    def test_unicode_path_is_loaded_as_bgr_uint8(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "端面测试图.png"
            source = np.zeros((24, 32, 3), dtype=np.uint8)
            source[:, :] = (12, 34, 56)
            success, encoded = cv2.imencode(".png", source)
            self.assertTrue(success)
            encoded.tofile(path)

            loaded = load_inspection_image(path)

        self.assertEqual(loaded.shape, (24, 32, 3))
        self.assertEqual(loaded.dtype, np.uint8)
        self.assertEqual(tuple(loaded[0, 0]), (12, 34, 56))
        self.assertTrue(loaded.flags.c_contiguous)

    def test_missing_empty_and_corrupt_files_raise_clear_errors(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            with self.assertRaisesRegex(ValueError, "无法读取"):
                load_inspection_image(root / "missing.png")

            empty_path = root / "empty.png"
            empty_path.touch()
            with self.assertRaisesRegex(ValueError, "图片为空"):
                load_inspection_image(empty_path)

            corrupt_path = root / "corrupt.png"
            corrupt_path.write_bytes(b"not an image")
            with self.assertRaisesRegex(ValueError, "格式无效|解码失败"):
                load_inspection_image(corrupt_path)

    def test_module_keeps_opencv_and_numpy_as_delayed_imports(self):
        module_path = (
            Path(__file__).resolve().parents[1]
            / "gui"
            / "app"
            / "services"
            / "inspection_image_loader.py"
        )
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        module_level_imports = {
            alias.name.split(".", 1)[0]
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertTrue({"cv2", "numpy"}.isdisjoint(module_level_imports))


if __name__ == "__main__":
    unittest.main()
