# -*- coding: utf-8 -*-
"""配置持久化服务：界面参数 ↔ config.json 的双向读写。"""

import json
import os

class ConfigService:
    """把参数面板的值保存到 JSON，或从 JSON 恢复。

    依赖注入：面板控件和日志函数由外部传入，服务不自己创建。
    """

    def __init__(self, path: str, panel, log_fn):
        self._path = path
        self._panel = panel      # ParamPanel：收集/恢复参数
        self._log = log_fn       # 日志函数（MainWindow._log）

    # ---------- 面板 → dict ----------
    def collect(self) -> dict:
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

    # ---------- dict → 面板 ----------
    def apply(self, cfg: dict):
        # 旧配置兼容：英文 mode → 新中文
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
        self._panel.template_edit.setText(cfg.get("template", "data/template_sim.json"))
        self._panel.calibrate_step_spin.setValue(cfg.get("calibrate_step_um", 20))
        self._panel.calibrate_ds_combo.setCurrentText(cfg.get("calibrate_downsample", "decimation 4"))

    # ---------- 文件读写 ----------
    def save(self):
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self.collect(), f, ensure_ascii=False, indent=2)
            self._log(f"配置已保存: {self._path}")
        except Exception as e:
            self._log(f"[错误] 保存配置失败: {e}")

    def load(self):
        if not os.path.exists(self._path):
            self._log("无配置文件，使用默认参数")
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.apply(cfg)
            self._log(f"已加载配置: {self._path}")
        except Exception as e:
            self._log(f"[错误] 加载配置失败: {e}")