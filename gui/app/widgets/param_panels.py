from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QFormLayout,
    QLabel, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox,
    QPushButton, QCheckBox, QHBoxLayout,QTabWidget,
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

        # 旧action_combo暂时负责维持现有ConfigService兼容。
        self.action_combo.currentTextChanged.connect(
            self._update_action_widgets
        )
        self.action_combo.currentTextChanged.connect(
            self._sync_ncc_action_from_legacy
        )

        # 用户操作新的NCC动作控件时，同步到旧action_combo。
        self.ncc_action_combo.currentTextChanged.connect(
            self._sync_legacy_action_from_ncc
        )

        # 根据旧配置初始化NCC动作和控件状态。
        self._sync_ncc_action_from_legacy(
            self.action_combo.currentText()
        )
        self._update_action_widgets(
            self.action_combo.currentText()
        )

        self.mode_combo.currentTextChanged.connect(
            self._update_mode_widgets
        )
        self._update_mode_widgets(
            self.mode_combo.currentText()
        )

    def _sync_legacy_action_from_ncc(
            self,
            ncc_action_text: str,
    ):
        """把新的NCC动作同步到旧action_combo。"""

        action_map = {
            "NCC搜索": "搜索对焦",
            "NCC模板标定": "图像标定",
        }

        legacy_action = action_map.get(ncc_action_text)

        if (
                legacy_action is not None
                and self.action_combo.currentText() != legacy_action
        ):
            self.action_combo.setCurrentText(legacy_action)

    def _sync_ncc_action_from_legacy(
            self,
            legacy_action_text: str,
    ):
        """把旧action_combo的值同步到新的NCC动作。"""

        action_map = {
            "搜索对焦": "NCC搜索",
            "图像标定": "NCC模板标定",
        }

        ncc_action = action_map.get(legacy_action_text)

        if (
                ncc_action is not None
                and self.ncc_action_combo.currentText() != ncc_action
        ):
            self.ncc_action_combo.setCurrentText(ncc_action)
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
        self.trigger_mode_label = QLabel("自动切换")
        self.trigger_mode_label.setToolTip(
            "实时预览使用连续取流；"
            "标定、粗扫、精扫和定点拍照使用硬件触发"
        )
        self.decimation_combo = QComboBox()
        self.decimation_combo.addItems(["1x1", "2x2", "4x4"])
        form.addRow(
            "触发模式:",
            self.trigger_mode_label,
        )
        form.addRow("曝光时间:", self.exposure_spin)
        form.addRow("增益:", self.gain_spin)
        form.addRow("下采样(dec):", self.decimation_combo)
        return group

    # ---------- 流程控制 ----------
    def _build_flow_group(self) -> QGroupBox:
        """创建NCC和AI共用的运行模式参数。"""

        group = QGroupBox("运行模式")
        form = QFormLayout(group)

        # 旧动作控件暂时保留，供ConfigService兼容使用。
        #
        # 它不再显示在界面上；
        # 后续ConfigService迁移完成后再彻底删除。
        self.action_combo = QComboBox(group)
        self.action_combo.addItems([
            "搜索对焦",
            "图像标定",
        ])

        self.mode_combo = QComboBox()
        self.mode_combo.addItems([
            "真实",
            "仿真",
        ])

        self.skip_confirm_check = QCheckBox(
            "跳过运动确认"
        )
        self.skip_confirm_check.setChecked(True)

        # action_combo不再加入form，因此用户看不到旧动作选择。
        form.addRow(
            "模式:",
            self.mode_combo,
        )
        form.addRow(
            self.skip_confirm_check,
        )

        return group

    # ---------- 搜索参数 ----------
    def _build_search_group(self) -> QGroupBox:
        """创建公共扫描参数以及NCC、AI策略页。"""

        group = QGroupBox("扫描参数")
        form = QFormLayout(group)

        # =================================================
        # 1. 公共搜索参数
        # =================================================
        self.search_start_spin = QSpinBox()
        self.search_start_spin.setRange(0, 50000)
        self.search_start_spin.setValue(9500)

        self.search_span_spin = QSpinBox()
        self.search_span_spin.setRange(100, 50000)
        self.search_span_spin.setValue(2000)

        self.fine_step_spin = QSpinBox()
        self.fine_step_spin.setRange(1, 100)
        self.fine_step_spin.setValue(5)

        self.fine_half_spin = QSpinBox()
        self.fine_half_spin.setRange(1, 100)
        self.fine_half_spin.setValue(5)

        self.save_edit = QLineEdit()
        self.save_edit.setPlaceholderText(
            "留空 = 不保存"
        )

        # =================================================
        # 2. NCC专用参数
        # =================================================
        self.ncc_action_combo = QComboBox()
        self.ncc_action_combo.addItems([
            "NCC搜索",
            "NCC模板标定",
        ])

        self.coarse_step_spin = QSpinBox()
        self.coarse_step_spin.setRange(1, 300)
        self.coarse_step_spin.setValue(100)

        self.template_edit = QLineEdit(
            "data/template_sim.json"
        )

        self.template_load_btn = QPushButton(
            "加载模板"
        )

        self.calibrate_step_spin = QSpinBox()
        self.calibrate_step_spin.setRange(1, 100)
        self.calibrate_step_spin.setValue(20)
        self.calibrate_step_spin.setSuffix(" µm")
        self.calibrate_step_spin.setToolTip(
            "标定飞拍步距（别用5µm全幅）"
        )

        self.calibrate_ds_combo = QComboBox()
        self.calibrate_ds_combo.addItems([
            "decimation 4",
            "decimation 2",
        ])

        # =================================================
        # 3. AI专用参数
        # =================================================
        self.shot_position_spin = QSpinBox()
        self.shot_position_spin.setRange(0, 50000)
        self.shot_position_spin.setValue(12000)
        self.shot_position_spin.setSuffix(" µm")
        self.shot_position_spin.setToolTip(
            "AI拍摄输入图时，PLC工艺必须保证丝杆位于这个物理位置"
        )

        self.dl_model_edit = QLineEdit(
            "assets/models/ai/best_resnet.pt"
        )
        self.dl_model_edit.setPlaceholderText(
            "请选择AI对焦模型文件"
        )
        self.dl_model_edit.setToolTip(
            "单帧对焦回归模型best_resnet.pt的路径"
        )

        self.dl_model_browse_btn = QPushButton(
            "选择AI模型"
        )
        self.dl_model_browse_btn.setToolTip(
            "选择PyTorch模型文件"
        )

        self.dl_model_status_label = QLabel(
            "尚未加载"
        )
        self.dl_model_status_label.setToolTip(
            "显示AI对焦模型的加载和预热状态"
        )

        # =================================================
        # 4. 策略选项卡
        # =================================================
        self.strategy_tabs = QTabWidget()

        self.ncc_tab = QWidget()
        self.ai_tab = QWidget()

        self.ncc_tab_form = QFormLayout(
            self.ncc_tab
        )
        self.ai_tab_form = QFormLayout(
            self.ai_tab
        )

        self.ncc_tab_index = self.strategy_tabs.addTab(
            self.ncc_tab,
            "NCC 对焦",
        )
        self.ai_tab_index = self.strategy_tabs.addTab(
            self.ai_tab,
            "AI 对焦",
        )

        self.strategy_tabs.setCurrentIndex(
            self.ncc_tab_index
        )

        # NCC页面。
        self.ncc_tab_form.addRow(
            "NCC操作:",
            self.ncc_action_combo,
        )
        self.ncc_tab_form.addRow(
            "粗扫步长:",
            self.coarse_step_spin,
        )
        self.ncc_tab_form.addRow(
            "NCC模板文件:",
            self.template_edit,
        )
        self.ncc_tab_form.addRow(
            self.template_load_btn
        )
        self.ncc_tab_form.addRow(
            "标定步距(um):",
            self.calibrate_step_spin,
        )
        self.ncc_tab_form.addRow(
            "标定降采样:",
            self.calibrate_ds_combo,
        )

        # AI页面。
        self.ai_tab_form.addRow(
            "AI拍摄位置:",
            self.shot_position_spin,
        )
        self.ai_tab_form.addRow(
            "AI模型文件:",
            self.dl_model_edit,
        )
        self.ai_tab_form.addRow(
            self.dl_model_browse_btn
        )
        self.ai_tab_form.addRow(
            "模型状态:",
            self.dl_model_status_label,
        )

        # =================================================
        # 5. 公共布局
        # =================================================
        self.start_btn = QPushButton(
            "开始执行"
        )

        self.stop_btn = QPushButton(
            "停止"
        )
        self.stop_btn.setEnabled(False)

        button_row = QHBoxLayout()
        button_row.addWidget(self.start_btn)
        button_row.addWidget(self.stop_btn)

        form.addRow(
            "扫描起点(um):",
            self.search_start_spin,
        )
        form.addRow(
            "扫描跨度(um):",
            self.search_span_spin,
        )
        form.addRow(
            self.strategy_tabs
        )
        form.addRow(
            "精扫步距(um):",
            self.fine_step_spin,
        )
        form.addRow(
            "精扫半宽(步):",
            self.fine_half_spin,
        )
        form.addRow(
            "保存图片目录:",
            self.save_edit,
        )
        form.addRow(
            button_row
        )

        return group
        # ---------- 运行时要锁定的控件 ----------

    def _update_action_widgets(self, action_text: str):
        """根据搜索/标定动作切换相关参数的可用状态。"""

        is_search = action_text == "搜索对焦"

        # 搜索对焦专用参数。
        search_widgets = (
            self.coarse_step_spin,
            self.fine_step_spin,
            self.fine_half_spin,
            self.template_load_btn,
        )

        # 图像标定专用参数。
        calibrate_widgets = (
            self.calibrate_step_spin,
            self.calibrate_ds_combo,
        )

        for widget in search_widgets:
            widget.setEnabled(is_search)

        for widget in calibrate_widgets:
            widget.setEnabled(not is_search)

    def _update_mode_widgets(self, mode_text: str):
        """根据真实/仿真模式更新相关控件状态。"""

        is_real = mode_text == "真实"

        # 仿真模式没有真实机械运动，不需要飞拍确认。
        self.skip_confirm_check.setEnabled(
            is_real
        )

    def lock_widgets(self) -> list:
        return [
            self.action_combo,
            self.ncc_action_combo,
            self.strategy_tabs,
            self.mode_combo,
            self.skip_confirm_check,
            self.exposure_spin,
            self.gain_spin,
            self.decimation_combo,
            self.plc_ip_edit,
            self.plc_port_spin,
            self.plc_connect_btn,
            self.search_start_spin,
            self.search_span_spin,
            self.fine_step_spin,
            self.fine_half_spin,
            self.coarse_step_spin,
            self.save_edit,
            self.template_edit,
            self.template_load_btn,
            self.calibrate_step_spin,
            self.calibrate_ds_combo,
            self.shot_position_spin,
            self.dl_model_edit,
            self.dl_model_browse_btn,
        ]
