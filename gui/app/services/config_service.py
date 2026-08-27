# -*- coding: utf-8 -*-
"""配置持久化服务：界面参数与 config.json 的双向读写。"""

import json
import logging
import os
from backend.config import FocusConfig


logger = logging.getLogger(__name__)


DEFAULT_MOTION_CONFIG = {
    "m60_dll_path": (
        r"C:\Program Files (x86)\LCT\Pcie-M60\Sdk\lib\c++\x64"
        r"\ecat_motion.dll"
    ),
    "e4o4_dll_path": (
        r"C:\Program Files (x86)\LCT\MINI-BUS Setup\SDK\x64"
        r"\MiniEcatLib.dll"
    ),
    "eni_path": (
        r"C:\Program Files (x86)\LCT\Pcie-M60\ENI"
        r"\eni_expertmode_Card0.xml"
    ),
    "axis_param_path": (
        r"C:\Program Files (x86)\LCT\Pcie-M60\Motion_Assistant"
        r"\AxisParam\ParamCard0.ini"
    ),
    "card_no": 0,
    "axis_no": 1,
    "e4o4_slave_no": 1,
    "encoder_no": 0,
    "trigger_out_no": 0,
    "line_compare_no": 0,
    "precompare_no": 0,
    "counts_per_um": 100,
    "encoder_multiplier": 4,
    "encoder_direction": 1,
    "trigger_pulse_width_10ns": 2000,
    "trigger_polarity": 0,
    "positioning_velocity_um_s": 100.0,
    "scan_velocity_um_s": 100.0,
    "calibrate_scan_velocity_um_s": 100.0,
    "coarse_scan_velocity_um_s": 1000.0,
    "fine_scan_velocity_um_s": 50.0,
    "line_scan_overrun_um": 20.0,
    "single_capture_approach_um": 50,
    "single_capture_exit_um": 50,
    "position_tolerance_um": 1.0,
    "home_method": 33,
    "home_offset_counts": 0,
    "home_speed1_counts_s": 10000,
    "home_speed2_counts_s": 2000,
    "home_acceleration_counts_s2": 100000,
    "home_probe_function": 0,
    "home_position_tolerance_counts": 50,
    "home_timeout_s": 900.0,
    "home_poll_interval_s": 0.05,
}


class ConfigService:
    """把参数面板的值保存到 JSON，或从 JSON 恢复。"""

    def __init__(
            self,
            path: str,
            panel,
            project_root: str,
    ):
        self._path = path
        self._panel = panel
        self._project_root = project_root
        self._loaded_config = {}

    def _current_strategy(self) -> str:
        """根据当前策略选项卡返回后端策略名称。"""

        current_index = (
            self._panel.strategy_tabs.currentIndex()
        )

        if current_index == self._panel.ai_tab_index:
            return "dl"

        return "ncc"

    def collect(self) -> dict:
        """收集参数面板中的当前配置。"""

        return {
            "action": self._panel.action_combo.currentText(),
            "mode": self._panel.mode_combo.currentText(),
            "strategy": self._current_strategy(),
            "skip_confirm": self._panel.skip_confirm_check.isChecked(),
            "exposure_us": self._panel.exposure_spin.value(),
            "gain_db": self._panel.gain_spin.value(),
            "decimation": self._panel.decimation_combo.currentText(),
            # 轴卡和E4O4的SDK路径属于工控机本地配置，
            # 不在主界面上占用大量编辑控件。
            "motion": self._motion_values(),
            "search_start_um": self._panel.search_start_spin.value(),
            "search_span_um": self._panel.search_span_spin.value(),
            "fine_step_um": self._panel.fine_step_spin.value(),
            "fine_half_steps": self._panel.fine_half_spin.value(),
            "coarse_step_um": self._panel.coarse_step_spin.value(),
            "save_dir": self._panel.save_edit.text(),
            "template": self._panel.template_edit.text(),
            "calibrate_step_um": self._panel.calibrate_step_spin.value(),
            "calibrate_downsample": (
                self._panel.calibrate_ds_combo.currentText()
            ),
            "dl_model": self._panel.dl_model_edit.text(),
            "shot_position_um": (
                self._panel.shot_position_spin.value()
            ),
        }

    def build_focus_config(self) -> FocusConfig:
        """把参数面板的当前值转换成后端任务配置。"""

        cfg = FocusConfig()

        ncc_action_map = {
            "NCC搜索": "search",
            "NCC模板标定": "calibrate",
        }
        mode_map = {
            "真实": "real",
            "仿真": "sim",
        }
        decimation_map = {
            "1x1": 1,
            "2x2": 2,
            "4x4": 4,
        }

        cfg.strategy = self._current_strategy()

        if cfg.strategy == "dl":
            # AI 模型已经在外部完成训练，
            # 当前 GUI 没有 AI 标定流程。
            cfg.action = "search"
        else:
            cfg.action = ncc_action_map[
                self._panel.ncc_action_combo.currentText()
            ]

        cfg.mode = mode_map[
            self._panel.mode_combo.currentText()
        ]
        cfg.yes = (
            self._panel.skip_confirm_check.isChecked()
        )

        template_path = (
            self._panel.template_edit.text().strip()
        )
        if (
                template_path
                and not os.path.isabs(template_path)
        ):
            template_path = os.path.join(
                self._project_root,
                template_path,
            )

        dl_model_path = (
            self._panel.dl_model_edit.text().strip()
        )
        if (
                dl_model_path
                and not os.path.isabs(dl_model_path)
        ):
            dl_model_path = os.path.join(
                self._project_root,
                dl_model_path,
            )

        cfg.template = template_path
        cfg.dl_model = dl_model_path
        cfg.shot_position_um = (
            self._panel.shot_position_spin.value()
        )

        cfg.exposure_us = (
            self._panel.exposure_spin.value()
        )
        cfg.gain_db = (
            self._panel.gain_spin.value()
        )
        cfg.coarse_binning = decimation_map[
            self._panel.decimation_combo.currentText()
        ]
        cfg.coarse_downsample = "decimation"
        cfg.coarse_step_um = (
            self._panel.coarse_step_spin.value()
        )
        cfg.fine_step_um = (
            self._panel.fine_step_spin.value()
        )
        cfg.fine_half_steps = (
            self._panel.fine_half_spin.value()
        )
        cfg.search_start_um = (
            self._panel.search_start_spin.value()
        )
        cfg.search_span_um = (
            self._panel.search_span_spin.value()
        )
        cfg.save_images = (
                self._panel.save_edit.text().strip()
                or None
        )
        cfg.calibrate_step_um = (
            self._panel.calibrate_step_spin.value()
        )

        calibrate_parts = (
            self._panel.calibrate_ds_combo
            .currentText()
            .split()
        )
        cfg.calibrate_downsample = calibrate_parts[0]
        cfg.calibrate_factor = int(
            calibrate_parts[1]
        )

        return cfg

    def build_motion_config(self):
        """构造M60+E4O4运动后端配置。

        这些参数由工控机本地gui/config.json保存，
        因此笔记本可以编辑同一份代码，而不必具备厂家SDK。
        """

        from motion.lct import LctMotionConfig

        values = self._motion_values()
        return LctMotionConfig(**values)

    def _motion_values(self) -> dict:
        """返回补齐默认值后的本机运动配置。"""

        values = dict(DEFAULT_MOTION_CONFIG)
        stored = self._loaded_config.get("motion", {})
        if isinstance(stored, dict):
            values.update(stored)
        return values

    def apply(self, cfg: dict):
        """把配置字典恢复到参数面板。"""

        self._loaded_config = dict(cfg)

        mode = cfg.get("mode", "真实")
        mode = {
            "real": "真实",
            "sim": "仿真",
        }.get(mode, mode)

        action = cfg.get(
            "action",
            "搜索对焦",
        )
        action = {
            "search": "搜索对焦",
            "calibrate": "图像标定",
            "离线标定": "图像标定",
        }.get(action, action)

        strategy = cfg.get(
            "strategy",
            "ncc",
        )

        self._panel.action_combo.setCurrentText(
            action
        )
        self._panel.mode_combo.setCurrentText(
            mode
        )

        if strategy == "dl":
            strategy_tab_index = (
                self._panel.ai_tab_index
            )
        else:
            strategy_tab_index = (
                self._panel.ncc_tab_index
            )

        self._panel.strategy_tabs.setCurrentIndex(
            strategy_tab_index
        )

        self._panel.skip_confirm_check.setChecked(
            cfg.get("skip_confirm", True)
        )
        self._panel.exposure_spin.setValue(
            cfg.get("exposure_us", 3000)
        )
        self._panel.gain_spin.setValue(
            cfg.get("gain_db", 0.0)
        )
        self._panel.decimation_combo.setCurrentText(
            cfg.get("decimation", "2x2")
        )
        self._panel.search_start_spin.setValue(
            cfg.get("search_start_um", 9500)
        )
        self._panel.search_span_spin.setValue(
            cfg.get("search_span_um", 2000)
        )
        self._panel.fine_step_spin.setValue(
            cfg.get("fine_step_um", 5)
        )
        self._panel.fine_half_spin.setValue(
            cfg.get("fine_half_steps", 5)
        )
        self._panel.coarse_step_spin.setValue(
            cfg.get("coarse_step_um", 100)
        )
        self._panel.save_edit.setText(
            cfg.get("save_dir", "")
        )
        self._panel.template_edit.setText(
            cfg.get(
                "template",
                "data/template_sim.json",
            )
        )
        self._panel.calibrate_step_spin.setValue(
            cfg.get("calibrate_step_um", 20)
        )
        self._panel.calibrate_ds_combo.setCurrentText(
            cfg.get(
                "calibrate_downsample",
                "decimation 4",
            )
        )
        self._panel.dl_model_edit.setText(
            cfg.get(
                "dl_model",
                "assets/models/ai/best_resnet.pt",
            )
        )
        self._panel.shot_position_spin.setValue(
            cfg.get("shot_position_um", 12000)
        )

    def save(self) -> bool:
        """把当前参数保存到 JSON。"""

        try:
            config = self.collect()
            with open(self._path, "w", encoding="utf-8") as file:
                json.dump(
                    config,
                    file,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception:
            logger.exception("保存配置失败: path=%s", self._path)
            return False

        self._loaded_config = config
        logger.info("配置已保存: %s", self._path)
        return True

    def load(self) -> bool:
        """从 JSON 加载配置并恢复到参数面板。"""

        if not os.path.exists(self._path):
            logger.info("无配置文件，使用默认参数")
            return False

        try:
            with open(self._path, "r", encoding="utf-8") as file:
                config = json.load(file)

            self.apply(config)
        except Exception:
            logger.exception("加载配置失败: path=%s", self._path)
            return False

        logger.info("已加载配置: %s", self._path)
        return True
