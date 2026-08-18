# GUI 开发课程计划（模块化重构）

> 目标：把当前 721 行的 `gui/main_window.py` 上帝对象和 824 行的 `verify_ncc_full.py` 单体，拆成分层、职责单一的模块，提升可读性与可维护性，并为未来引入深度学习方法留好扩展位。
>
> 教学方式：老师讲解知识点 → 布置任务 → 学生实操 → 老师代码审核与提示。

---

## 一、总体目标

- **GUI 与后端一起重构**：界面、服务、线程、算法、流程分层。
- **策略接口课程 11 落地**：早期"暂不建接口"以降低复杂度；结构成型后，课程 11 引入策略接口与注册表，为深度学习方法铺路。
- **备份后原地改**：每阶段开始前把要改的文件复制到 `backup/20260814_refactor/`，小步修改，随时可回退。

## 二、课程设计逻辑

```
认知层   课程1  重构思维与基线（为什么要重构、怎么保证安全）
结构层   课程2  Python 包结构（拆文件的前提）
界面层   课程3-4  拆控件（先搬"看得见"的，行为不变）
逻辑层   课程5-6  拆服务（配置/状态/CT）
契约层   课程7  dataclass（把 dict 通信换成类型化对象）
后端层   课程8-9  拆 verify_ncc_full（算法/采集/流程/CLI 分离）
收尾层   课程10  文档与真机回归
进阶层   课程11  策略接口与注册表（能力型接口，NCC/DL 统一）
        课程12  GUI 业务服务拆分（main_window 瘦身到接线板）
        课程13  遗留清理（常量/重复采集器/目录归类）
```

每课遵守同一纪律：**改前备份 → 小步改 → 冒烟验证**。

## 三、课程总览

| 课时 | 内容 | 核心知识点 | 验收 |
|---|---|---|---|
| 1 | 重构思维与基线建立（阶段 0） | 重构 vs 加功能；回归基线 | 备份完整，基线输出记录在案 |
| 2 | Python 包结构与导入体系 | 包、绝对/相对导入、循环导入 | 目录骨架建好，程序照常启动 |
| 3 | 拆控件（一）：图像/曲线/日志面板 | 控件类封装、信号通知 | 图像/曲线/日志与原来一致 |
| 4 | 拆控件（二）：左侧参数面板 | 控件归属、参数收集 | 参数读取与锁定功能不丢 |
| 5 | 拆服务（一）：配置持久化 | 服务类、依赖注入 | 改参数→重启→恢复 |
| 6 | 拆服务（二）：状态机与 CT 统计 | 状态机模式、CT 模块化 | 锁定/停止/CT 日志正常 |
| 7 | 数据契约：dataclass 替代 dict | dataclass、类型提示 | 全流程正常，无裸键访问 |
| 8 | 后端拆分（一）：常量/相机工具/采集器 | 常量单一来源、去重 | sim 行为一致，常量只剩一处 |
| 9 | 后端拆分（二）：检测/NCC/流程/CLI | 算法与流程分离、薄壳兼容 | CLI 与 GUI 照常工作 |
| 10 | 收尾：文档与真机回归 | 技术文档、真机回归流程 | 文档齐全，真机两次搜索通过 |
| 11 | 搜索策略接口与注册表（修正版） | 抽象基类、注册表、依赖倒置、能力型接口 | 新增策略只改一个文件，NCC/DL 同接口 |
| 12 | GUI 业务服务拆分 | 服务分层延续、信号/回调回传 | main_window 降到约 200 行 |
| 13 | 遗留清理 | 重复代码收敛、常量统一、目录归类 | `rg "5472"` 清零，无 broken import |

---

## 四、每课详情

### 课程 1：重构思维与基线建立（阶段 0）

- **课程目标**：理解重构的安全前提，建立可对照的回归基线。
- **知识点**：
  - 重构与"加功能"的区别：重构不改行为，只改结构；
  - 为什么"改前先备份"：重构最大的风险是改坏，备份 = 回退保险；
  - 什么是回归基线、怎么用基线验证"改坏了没有"。
- **任务布置**：
  - 建 `backup/20260814_refactor/`，备份 `gui/` 全部文件 + `verify_ncc_full.py`；
  - 跑通三个基线场景：sim 搜索、sim 标定、GUI 启动；
  - 把输出保存为 `baseline.txt`。
- **验收标准**：备份完整；基线文件记录了三个场景的输出，后续每课对照。

### 课程 2：Python 包结构与导入体系

- **课程目标**：掌握拆文件不破坏导入的前提。
- **知识点**：
  - 包（`__init__.py`）与模块；
  - 绝对导入 vs 相对导入，`from gui.xxx import ...` 的搜索路径原理；
  - 拆文件的三原则：包路径正确、避免循环导入、重模块延迟导入。
- **任务布置**：
  - 把 `gui/` 升级为 `gui/app/` 子包骨架（`widgets/`、`services/`、`workers/`、`base/`）；
  - 建好各 `__init__.py`，确认现有功能不受影响。
- **验收标准**：目录骨架建好，`python -m gui.main` 照常启动。

### 课程 3：拆控件（一）——图像/曲线/日志面板

- **课程目标**：把 MainWindow 中"构建 + 显示"逻辑整体搬进控件类。
- **知识点**：
  - 控件类封装（QWidget 子类）；
  - 如何迁移 `_numpy_to_pixmap` / `_show_image` / `_log` 等方法；
  - 控件内部信号如何通知窗口。
- **任务布置**：
  - 拆出 `app/widgets/image_view.py`（图像显示 + ROI）；
  - 拆出 `app/widgets/curve_panel.py`（迁入 `curve_widget.py`）；
  - 拆出 `app/widgets/log_panel.py`（日志）；
  - MainWindow 改为组装这些控件。
- **验收标准**：GUI 启动后图像、曲线、日志与原来一致；MainWindow 行数明显下降。

### 课程 4：拆控件（二）——左侧参数面板

- **课程目标**：解决"参数控件建在面板里，值如何被窗口读取"的归属问题。
- **知识点**：
  - 参数控件的归属与对象引用传递；
  - `_collect_params` 与控件解耦。
- **任务布置**：
  - 拆出 `app/widgets/param_panels.py`（流程/相机/PLC/搜索四个组）；
  - MainWindow 通过面板对象访问控件。
- **验收标准**：参数面板显示一致；`_collect_params` 仍能读到全部值；运行中锁定参数功能不丢。

### 课程 5：拆服务（一）——配置持久化

- **课程目标**：把无界面逻辑的代码抽成服务类。
- **知识点**：
  - 服务类（纯逻辑、不碰控件）；
  - 依赖注入：服务不自己拿控件/日志，由调用方传入；
  - 单一职责原则。
- **任务布置**：
  - 拆出 `app/services/config_service.py`（`collect/apply/save/load`）；
  - MainWindow 只调用服务接口。
- **验收标准**：改参数→关窗→重开恢复；`config.json` 内容与重构前一致。

### 课程 6：拆服务（二）——状态机与 CT 统计

- **课程目标**：把界面状态与业务状态分离，CT 统计模块化。
- **知识点**：
  - 状态机模式的工程价值（IDLE/RUNNING/DONE/ERROR）；
  - CT 统计与日志解耦。
- **任务布置**：
  - 拆出 `app/services/controller.py`（状态切换 + 控件锁定 + cancel 管理）；
  - 拆出 `app/services/ct_logger.py`（`_log_ct`）。
- **验收标准**：运行中锁定、停止按钮、CT 日志与原来一致。

### 课程 7：数据契约——dataclass 替代 dict

- **课程目标**：把 GUI ↔ 后端的裸 dict 通信换成类型化对象。
- **知识点**：
  - dataclass 与类型提示；
  - 裸 dict 通信的三大坑：键名拼错、缺字段、无自动补全；
  - 迁移策略：先建类型，再改消费端，最后改生产端。
- **任务布置**：
  - 建 `backend/config.py`（`SearchConfig` / `CalibrateConfig`）；
  - 建 `backend/result.py`（`SearchResult` / `CalibrateResult`）；
  - worker、流程、GUI 改用对象传递。
- **验收标准**：sim 搜索/标定全流程正常；代码中不再有 `.get("xxx")` 裸键访问。

### 课程 8：后端拆分（一）——常量/相机工具/采集器

- **课程目标**：统一常量、消除重复代码。
- **知识点**：
  - 常量单一来源（顺带修复 `SENSOR_W/H` 旧值 5472×3648 → 4096×3000）；
  - 工具函数模块化；
  - 三份采集器合并成一份。
- **任务布置**：
  - 建 `backend/constants.py`、`backend/camera_utils.py`、`backend/collector.py`；
  - 流程代码改为引用新模块。
- **验收标准**：sim 流程行为一致；`rg "SENSOR_W"` 只剩一个定义处。

### 课程 9：后端拆分（二）——检测/NCC/流程/CLI

- **课程目标**：算法与流程分离，保留 CLI 兼容。
- **知识点**：
  - 算法与流程分离（`ncc.py` 即未来深度学习方法的扩展位）；
  - 薄壳兼容模式（facade）：`verify_ncc_full.py` 保留为转发层。
- **任务布置**：
  - 建 `backend/detection.py`、`backend/ncc.py`、`backend/pipeline.py`、`backend/cli.py`；
  - `verify_ncc_full.py` 改为薄壳转发。
- **验收标准**：`python verify_ncc_full.py --action search/calibrate --mode sim` 照常；GUI 标定/搜索照常。

### 课程 10：收尾——文档与真机回归

- **课程目标**：沉淀文档，完成真机回归。
- **知识点**：
  - 技术文档怎么写（模块结构图、扩展指南）；
  - 真机回归的步骤与注意事项。
- **任务布置**：
  - 更新 `docs/`：模块结构说明 + "如何新增搜索方法"指南；
  - 真机标定 + 连续两次搜索回归。
- **验收标准**：文档齐全；真机流程无崩溃；CT 统计正常。

### 课程 11：搜索策略接口与注册表（修正版——能力型接口）

- **课程目标**：把 NCC 从"写死的流程"升级为"可插拔策略"，**NCC（粗扫分数预测）与 DL（单帧图像预测）都能接入同一接口**；新增搜索方法 = 实现接口 + 注册。
- **知识点**：
  - 抽象基类（ABC）与接口约定；
  - 注册表模式（装饰器 `register`）；
  - 依赖倒置：高层流程不依赖具体算法；
  - **接口的输入是"采图能力"而非"具体数据"**：NCC 输入粗扫分数、DL 输入单帧图像，接口不能绑死某一方的数据（第一版窄接口的教训）；
  - 流程重构：粗扫从公共流程移入 NCC 策略，"预测"阶段由策略自行决定采集方式。
- **任务布置**：
  - 建 `backend/strategies.py`：`PeakPrediction`（peak_um / quality / ncc_max / roi_frame / coarse_points / extra）、`SearchContext`（coarse_scan / capture_frame）、`FocusStrategy` 抽象基类、`STRATEGIES` 注册表 + `register` 装饰器；
  - 写 `NCCStrategy`：内部执行粗扫 + `ncc_predict_peak`，返回含 `roi_frame` 与 `coarse_points` 的 `PeakPrediction`；
  - 写 `DLStrategy`：内部 `capture_frame` + 推理 Δz（可先占位，验证接口可接入）；
  - `FocusConfig` 加 `strategy` 字段（默认 `ncc`），CLI 加 `--strategy`；
  - `pipeline.run_search` 重构：粗扫移入策略，流程 = `strategy.predict_peak(ctx)` → `detect_roi(pred.roi_frame)` → 精扫 → 定拍；
  - GUI 曲线数据改从 `PeakPrediction.coarse_points` 获取。
- **验收标准**：CLI `--strategy ncc` 结果与改前一致（预测峰、曲线数据）；`--strategy dl`（或占位）能跑通；未知策略返回 rc=1；新增策略不改 pipeline；`run_search` 内不再直接调用粗扫/`ncc_predict_peak`。

### 课程 12：GUI 业务服务拆分（main_window 瘦身到接线板）

- **课程目标**：把 PLC / 实时预览 / 结果展示三类业务搬进服务，main_window 只剩"组装 + 接线"。
- **知识点**：
  - 服务分层延续与依赖注入；
  - 服务如何通知窗口（信号 / 回调）；
  - "控制器持窗口引用 vs 回调注入"的设计取舍。
- **任务布置**：
  - 建 `gui/app/services/plc_service.py`（连接 / 断开 / 状态）；
  - 建 `gui/app/services/live_view_service.py`（预览生命周期）；
  - 建 `gui/app/services/result_presenter.py`（结果 → 曲线 / 图像 / CT / ROI 展示）；
  - main_window 对应方法删除，改为调用服务；
  - 处理空壳 `gui/app/base/`。
- **验收标准**：GUI sim 全流程正常；main_window 降到约 200 行；`rg` 无旧方法残留。

### 课程 13：遗留清理（常量 / 重复采集器 / 目录归类）

- **课程目标**：消除三份重复采集器、统一旧常量、归类实验脚本，完成工程收尾。
- **知识点**：
  - 重复代码收敛策略（以最小引用者为基准迁移）；
  - 常量统一收尾；
  - 目录组织（`experiments/`）。
- **任务布置**：
  - `capture_scan.py` / `verify_dl_hybrid.py` 旧常量 → `backend.constants`；
  - `CaptureCollector` / `FrameCollector` 收敛到 `backend/collector.py` 的 `PhaseCollector`；
  - DL 实验脚本归类到 `experiments/`；
  - 全项目 import 健康检查。
- **验收标准**：`rg "5472"` 全项目清零（backup 除外）；`capture_scan.py` sim 流程正常；无 broken import。

---

## 五、每课纪律与验证

1. **改前备份**：每阶段开始前，把要改的文件复制到 `backup/20260814_refactor/`；
2. **小步改**：一次只做一个拆分，不混入功能改动；
3. **冒烟验证**：每课完成后跑 GUI 离屏启动 + sim 搜索/标定，与基线对照；
4. **代码审核**：学生提交后，老师逐条审核正确性、线程安全、Qt 惯用法与模块边界。

## 六、目标目录结构（重构完成后）

```
gui/
├── main.py
└── app/
    ├── main_window.py        # 接线板：布局组装 + 信号连接（约 200 行）
    ├── widgets/              # 图像/曲线/日志/参数面板
    ├── workers/              # verify / live_view / plc_connect
    └── services/             # config / controller / ct_logger / plc / live_view / result_presenter
backend/
├── constants.py  config.py  result.py
├── camera_utils.py  detection.py  collector.py  ncc.py
├── strategies.py            # 策略接口 + 注册表（课程 11）
├── pipeline.py  cli.py
verify_ncc_full.py            # 薄壳：from backend.cli import main
experiments/                  # DL 实验脚本归类（课程 13）
```
