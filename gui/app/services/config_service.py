# -*- coding: utf-8 -*-
"""配置持久化服务：界面参数与 config.json 的双向读写。"""

import json
import logging
import os
from backend.config import FocusConfig


logger = logging.getLogger(__name__)


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

    def collect(self) -> dict:
        """收集参数面板中的当前配置。"""

        return {
            "action": self._panel.action_combo.currentText(),
            "mode": self._panel.mode_combo.currentText(),
            "skip_confirm": self._panel.skip_confirm_check.isChecked(),
            "exposure_us": self._panel.exposure_spin.value(),
            "gain_db": self._panel.gain_spin.value(),
            "decimation": self._panel.decimation_combo.currentText(),
            "plc_host": self._panel.plc_ip_edit.text(),
            "plc_port": self._panel.plc_port_spin.value(),
            "search_start_um": self._panel.search_start_spin.value(),
            "search_span_um": self._panel.search_span_spin.value(),
            "fine_step_um": self._panel.fine_step_spin.value(),
            "fine_half_steps": self._panel.fine_half_spin.value(),
            "coarse_step_um": self._panel.coarse_step_spin.value(),
            "save_dir": self._panel.save_edit.text(),
            "template": self._panel.template_edit.text(),
            "calibrate_step_um": self._panel.calibrate_step_spin.value(),
            "calibrate_downsample": self._panel.calibrate_ds_combo.currentText(),
        }

    def build_focus_config(self) -> FocusConfig:
        """把参数面板的当前值转换成后端任务配置。"""

        cfg = FocusConfig()

        action_map = {"搜索对焦": "search", "图像标定": "calibrate"}
        mode_map = {"真实": "real", "仿真": "sim"}
        decimation_map = {"1x1": 1, "2x2": 2, "4x4": 4}

        cfg.action = action_map[self._panel.action_combo.currentText()]
        cfg.mode = mode_map[self._panel.mode_combo.currentText()]
        cfg.yes = self._panel.skip_confirm_check.isChecked()

        template_path = self._panel.template_edit.text().strip()
        if template_path and not os.path.isabs(template_path):
            template_path = os.path.join(self._project_root, template_path)

        cfg.template = template_path
        cfg.plc_host = self._panel.plc_ip_edit.text().strip()
        cfg.plc_port = self._panel.plc_port_spin.value()
        cfg.exposure_us = self._panel.exposure_spin.value()
        cfg.gain_db = self._panel.gain_spin.value()
        cfg.coarse_binning = decimation_map[
            self._panel.decimation_combo.currentText()
        ]
        cfg.coarse_downsample = "decimation"
        cfg.coarse_step_um = self._panel.coarse_step_spin.value()
        cfg.fine_step_um = self._panel.fine_step_spin.value()
        cfg.fine_half_steps = self._panel.fine_half_spin.value()
        cfg.search_start_um = self._panel.search_start_spin.value()
        cfg.search_span_um = self._panel.search_span_spin.value()
        cfg.save_images = self._panel.save_edit.text().strip() or None
        cfg.calibrate_step_um = self._panel.calibrate_step_spin.value()

        calibrate_parts = self._panel.calibrate_ds_combo.currentText().split()
        cfg.calibrate_downsample = calibrate_parts[0]
        cfg.calibrate_factor = int(calibrate_parts[1])
        return cfg

    def apply(self, cfg: dict):
        """把配置字典恢复到参数面板。"""

        mode = cfg.get("mode", "真实")
        mode = {"real": "真实", "sim": "仿真"}.get(mode, mode)

        action = cfg.get("action", "搜索对焦")
        action = {"离线标定": "图像标定"}.get(action, action)

        self._panel.action_combo.setCurrentText(action)
        self._panel.mode_combo.setCurrentText(mode)
        self._panel.skip_confirm_check.setChecked(cfg.get("skip_confirm", True))
        self._panel.exposure_spin.setValue(cfg.get("exposure_us", 3000))
        self._panel.gain_spin.setValue(cfg.get("gain_db", 0.0))
        self._panel.decimation_combo.setCurrentText(cfg.get("decimation", "2x2"))
        self._panel.plc_ip_edit.setText(cfg.get("plc_host", "192.168.100.88"))
        self._panel.plc_port_spin.setValue(cfg.get("plc_port", 502))
        self._panel.search_start_spin.setValue(cfg.get("search_start_um", 9500))
        self._panel.search_span_spin.setValue(cfg.get("search_span_um", 2000))
        self._panel.fine_step_spin.setValue(cfg.get("fine_step_um", 5))
        self._panel.fine_half_spin.setValue(cfg.get("fine_half_steps", 5))
        self._panel.coarse_step_spin.setValue(cfg.get("coarse_step_um", 100))
        self._panel.save_edit.setText(cfg.get("save_dir", ""))
        self._panel.template_edit.setText(
            cfg.get("template", "data/template_sim.json")
        )
        self._panel.calibrate_step_spin.setValue(
            cfg.get("calibrate_step_um", 20)
        )
        self._panel.calibrate_ds_combo.setCurrentText(
            cfg.get("calibrate_downsample", "decimation 4")
        )

    def save(self) -> bool:
        """把当前参数保存到 JSON。"""

        try:
            with open(self._path, "w", encoding="utf-8") as file:
                json.dump(
                    self.collect(),
                    file,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception:
            logger.exception("保存配置失败: path=%s", self._path)
            return False

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