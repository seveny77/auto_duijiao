from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QFormLayout,
    QLabel, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox,
    QPushButton, QCheckBox, QHBoxLayout,
)

class ParamPanel(QWidget):
    """左侧参数面板：创建并持有所有参数控件。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(320)
        layout = QVBoxLayout(self)
        layout.addWidget(self._build_plc_group())
        layout.addWidget(self._build_camera_group())
        layout.addWidget(self._build_flow_group())
        layout.addWidget(self._build_search_group())
        layout.addStretch()

    # ---------- PLC 参数 ----------
    def _build_plc_group(self) -> QGroupBox:
        group = QGroupBox("PLC 参数")
        form = QFormLayout(group)
        self.plc_ip_edit = QLineEdit("192.168.100.88")
        self.plc_port_spin = QSpinBox()
        self.plc_port_spin.setRange(1, 65535)
        self.plc_port_spin.setValue(502)
        self.plc_connect_btn = QPushButton("PLC连接测试")
        self.plc_stroke_label = QLabel("未连接")
        form.addRow("IP 地址:", self.plc_ip_edit)
        form.addRow("端口:", self.plc_port_spin)
        form.addRow(self.plc_connect_btn)
        form.addRow("行程范围:", self.plc_stroke_label)
        return group

    # ---------- 相机参数 ----------
    def _build_camera_group(self) -> QGroupBox:
        group = QGroupBox("相机参数")
        form = QFormLayout(group)
        self.exposure_spin = QSpinBox()
        self.exposure_spin.setRange(10, 100000)
        self.exposure_spin.setValue(3000)
        self.exposure_spin.setSuffix(" µs")
        self.gain_spin = QDoubleSpinBox()
        self.gain_spin.setRange(0.0, 30.0)
        self.gain_spin.setDecimals(1)
        self.gain_spin.setValue(0.0)
        self.gain_spin.setSuffix(" dB")
        self.camType_combo = QComboBox()
        self.camType_combo.addItems(["软件触发", "硬件触发", "连续触发"])
        self.decimation_combo = QComboBox()
        self.decimation_combo.addItems(["1x1", "2x2", "4x4"])
        form.addRow("触发模式:", self.camType_combo)
        form.addRow("曝光时间:", self.exposure_spin)
        form.addRow("增益:", self.gain_spin)
        form.addRow("下采样(dec):", self.decimation_combo)
        return group

    # ---------- 流程控制 ----------
    def _build_flow_group(self) -> QGroupBox:
        group = QGroupBox("流程控制")
        form = QFormLayout(group)
        self.action_combo = QComboBox()
        self.action_combo.addItems(["搜索对焦", "图像标定"])
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["真实", "仿真"])
        self.skip_confirm_check = QCheckBox("跳过飞拍确认")
        self.skip_confirm_check.setChecked(True)
        form.addRow("动作:", self.action_combo)
        form.addRow("模式:", self.mode_combo)
        form.addRow(self.skip_confirm_check)
        return group

    # ---------- 搜索参数 ----------
    def _build_search_group(self) -> QGroupBox:
        group = QGroupBox("搜索参数")
        form = QFormLayout(group)
        # 搜索起点
        self.search_start_spin = QSpinBox()
        self.search_start_spin.setRange(0, 50000)
        self.search_start_spin.setValue(9500)
        # 搜索跨度
        self.search_span_spin = QSpinBox()
        self.search_span_spin.setRange(100, 50000)
        self.search_span_spin.setValue(2000)
        #粗扫步距
        self.coarse_step_spin = QSpinBox()
        self.coarse_step_spin.setRange(1, 300)
        self.coarse_step_spin.setValue(100)
        #精扫步距
        self.fine_step_spin = QSpinBox()
        self.fine_step_spin.setRange(1, 100)
        self.fine_step_spin.setValue(5)
        #精扫半宽
        self.fine_half_spin = QSpinBox()
        self.fine_half_spin.setRange(1, 100)
        self.fine_half_spin.setValue(5)

        #对焦过程存图开启
        self.save_edit = QLineEdit()
        self.save_edit.setPlaceholderText("留空 = 不保存")
        #标定模板文件
        self.template_edit = QLineEdit("data/template_sim.json")

        #标定步长
        self.calibrate_step_spin = QSpinBox()
        self.calibrate_step_spin.setRange(1, 100)
        self.calibrate_step_spin.setValue(20)
        self.calibrate_step_spin.setSuffix(" µm")
        self.calibrate_step_spin.setToolTip("标定飞拍步距（别用 5µm 全幅）")
        #标定降采样
        self.calibrate_ds_combo = QComboBox()
        self.calibrate_ds_combo.addItems(["decimation 4", "decimation 2"])

        # 标定模板加载按钮
        self.template_load_btn = QPushButton("加载模板")
        # 开始执行按钮
        self.start_btn = QPushButton("开始执行")
        # 停止按钮
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)

        form.addRow("搜索起点(um):", self.search_start_spin)
        form.addRow("搜索跨度(um):", self.search_span_spin)
        form.addRow("粗扫步长:", self.coarse_step_spin)
        form.addRow("精扫步距(um):", self.fine_step_spin)
        form.addRow("精扫半宽(步):", self.fine_half_spin)
        form.addRow("标定模板文件:", self.template_edit)
        form.addRow("标定步距(um):", self.calibrate_step_spin)
        form.addRow("标定降采样倍率:", self.calibrate_ds_combo)
        form.addRow("保存图片目录:", self.save_edit)
        form.addRow(self.template_load_btn)
        form.addRow(btn_row)
        return group
        # ---------- 运行时要锁定的控件 ----------

    def lock_widgets(self) -> list:
        return [
            self.action_combo, self.mode_combo, self.skip_confirm_check,
            self.exposure_spin, self.gain_spin, self.decimation_combo,
            self.camType_combo,
            self.plc_ip_edit, self.plc_port_spin, self.plc_connect_btn,
            self.search_start_spin, self.search_span_spin,
            self.fine_step_spin, self.fine_half_spin, self.coarse_step_spin,
            self.save_edit, self.template_edit,
            self.template_load_btn, self.calibrate_step_spin, self.calibrate_ds_combo,
        ]