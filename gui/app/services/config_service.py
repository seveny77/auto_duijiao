"""对焦界面参数与 gui/config.json 的双向转换。"""

import json
import logging
import os

from backend.config import FocusConfig
from backend.focus_roi import normalize_evaluation_roi


logger = logging.getLogger(__name__)


DEFAULT_MOTION_CONFIG = {
    "m60_dll_path": r"C:\Program Files (x86)\LCT\Pcie-M60\Sdk\lib\c++\x64\ecat_motion.dll",
    "eni_path": r"C:\Program Files (x86)\LCT\Pcie-M60\ENI\eni_expertmode_Card0.xml",
    "axis_param_path": r"C:\Program Files (x86)\LCT\Pcie-M60\Motion_Assistant\AxisParam\ParamCard0.ini",
    "card_no": 0, "axis_no": 1, "counts_per_um": 100,
    "positioning_velocity_um_s": 100.0, "scan_velocity_um_s": 100.0,
    "position_tolerance_um": 1.0, "home_method": 33, "home_offset_counts": 0,
    "home_speed1_counts_s": 10000, "home_speed2_counts_s": 2000,
    "home_acceleration_counts_s2": 100000, "home_probe_function": 0,
    "home_position_tolerance_counts": 50, "home_timeout_s": 900.0,
    "home_poll_interval_s": 0.05,
}


class ConfigService:
    def __init__(self, path, panel, project_root, image_widget=None):
        self._path = path
        self._panel = panel
        self._project_root = project_root
        self._image_widget = image_widget
        self._loaded_config = {}

    def _current_evaluation_roi(self):
        if self._image_widget is not None:
            return self._image_widget.evaluation_roi
        try:
            return normalize_evaluation_roi(self._loaded_config.get("evaluation_roi"))
        except ValueError:
            return None

    def collect(self):
        return {
            "mode": self._panel.mode_combo.currentText(),
            "skip_confirm": self._panel.skip_confirm_check.isChecked(),
            "exposure_us": self._panel.exposure_spin.value(),
            "gain_db": self._panel.gain_spin.value(),
            "decimation": self._panel.decimation_combo.currentText(),
            "work_roi_width_px": self._panel.work_roi_width_spin.value(),
            "work_roi_height_px": self._panel.work_roi_height_spin.value(),
            "evaluation_roi": list(self._current_evaluation_roi()) if self._current_evaluation_roi() else None,
            "search_start_um": self._panel.search_start_spin.value(),
            "search_span_um": self._panel.search_span_spin.value(),
            "continuous_velocity_um_s": self._panel.continuous_velocity_spin.value(),
            "continuous_capture_fps": self._panel.soft_trigger_interval_spin.value(),
            "continuous_first_frame_timeout_s": self._panel.soft_trigger_timeout_spin.value(),
            "save_dir": self._panel.save_edit.text(),
        }

    def build_focus_config(self):
        values = self.collect()
        decimation = {"1x1": 1, "2x2": 2, "4x4": 4}[values["decimation"]]
        save_dir = values["save_dir"].strip()
        if save_dir and not os.path.isabs(save_dir):
            save_dir = os.path.join(self._project_root, save_dir)
        return FocusConfig(
            mode={"真实": "real", "仿真": "sim"}[values["mode"]],
            yes=bool(values["skip_confirm"]),
            exposure_us=int(values["exposure_us"]), gain_db=float(values["gain_db"]),
            camera_decimation=decimation,
            work_roi_width_px=int(values["work_roi_width_px"]),
            work_roi_height_px=int(values["work_roi_height_px"]),
            evaluation_roi=normalize_evaluation_roi(values["evaluation_roi"]),
            search_start_um=int(values["search_start_um"]),
            search_span_um=int(values["search_span_um"]),
            continuous_scan_velocity_um_s=float(values["continuous_velocity_um_s"]),
            continuous_capture_fps=float(values["continuous_capture_fps"]),
            continuous_first_frame_timeout_s=float(values["continuous_first_frame_timeout_s"]),
            save_dir=save_dir or None,
        )

    def build_motion_config(self):
        from motion.lct.config import LctMotionConfig
        values = dict(DEFAULT_MOTION_CONFIG)
        # 兼容既有 gui/config.json：其中旧 E4O4 字段会被有意忽略。
        values.update({
            key: value
            for key, value in self._motion_values().items()
            if key in values
        })
        return LctMotionConfig(**values)

    def _motion_values(self):
        return dict(self._loaded_config.get("motion", {}))

    def apply(self, cfg):
        self._loaded_config = dict(cfg)
        self._panel.mode_combo.setCurrentText(cfg.get("mode", "真实"))
        self._panel.skip_confirm_check.setChecked(bool(cfg.get("skip_confirm", True)))
        self._panel.exposure_spin.setValue(int(cfg.get("exposure_us", 3000)))
        self._panel.gain_spin.setValue(float(cfg.get("gain_db", 0.0)))
        self._panel.decimation_combo.setCurrentText(cfg.get("decimation", "1x1"))
        self._panel.work_roi_width_spin.setValue(int(cfg.get("work_roi_width_px", 0)))
        self._panel.work_roi_height_spin.setValue(int(cfg.get("work_roi_height_px", 0)))
        self._panel.search_start_spin.setValue(int(cfg.get("search_start_um", 9500)))
        self._panel.search_span_spin.setValue(int(cfg.get("search_span_um", 2000)))
        self._panel.continuous_velocity_spin.setValue(float(cfg.get("continuous_velocity_um_s", 50.0)))
        self._panel.soft_trigger_interval_spin.setValue(float(cfg.get("continuous_capture_fps", 20.0)))
        self._panel.soft_trigger_timeout_spin.setValue(float(cfg.get("continuous_first_frame_timeout_s", 1.0)))
        self._panel.save_edit.setText(cfg.get("save_dir", ""))
        try:
            roi = normalize_evaluation_roi(cfg.get("evaluation_roi"))
        except ValueError:
            logger.warning("配置中的清晰度 ROI 无效，已重置为整图")
            roi = None
        if self._image_widget is not None:
            self._image_widget.set_evaluation_roi(roi, emit_signal=False)

    def save(self):
        try:
            config = self.collect()
            config["motion"] = {
                key: value
                for key, value in self._motion_values().items()
                if key in DEFAULT_MOTION_CONFIG
            }
            with open(self._path, "w", encoding="utf-8") as file:
                json.dump(config, file, ensure_ascii=False, indent=2)
        except Exception:
            logger.exception("保存配置失败: path=%s", self._path)
            return False
        self._loaded_config = config
        logger.info("配置已保存: %s", self._path)
        return True

    def load(self):
        if not os.path.exists(self._path):
            return False
        try:
            with open(self._path, "r", encoding="utf-8") as file:
                self.apply(json.load(file))
        except Exception:
            logger.exception("加载配置失败: path=%s", self._path)
            return False
        logger.info("已加载配置: %s", self._path)
        return True
