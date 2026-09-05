# 工控机部署说明

目标目录：`C:\Autofocus\app`。

## 基础运行工具

第一次部署时执行：

```powershell
powershell -ExecutionPolicy Bypass -File deploy\bootstrap_runtime.ps1
```

脚本会下载并校验 MinGit 和 Miniconda，然后安装到
`C:\Autofocus\runtime`，不会修改系统默认 Python。

如果工控机不能直连 GitHub，可以先只安装 Miniconda：

```powershell
powershell -ExecutionPolicy Bypass -File deploy\bootstrap_runtime.ps1 -SkipGit
```

## Python 环境

在项目根目录执行：

```powershell
conda env create -f deploy\environment.yml
conda run -n autofocus python -m pip install -r deploy\requirements-runtime.txt
conda run -n autofocus python -m pip install -r deploy\requirements-torch-cu128.txt
```

部署环境固定为 Python 3.13。找圆模型和分割模型统一在专用 CPython 线程中
加载和推理，避免从 Windows Qt 原生工作线程直接调用 CUDA。未指定设备时由
Ultralytics 自动选择；生产启动入口显式使用首张 CUDA 设备：

```powershell
deploy\start_gui_py313.cmd
```

也可以在独立诊断时显式指定设备：

```powershell
$env:AUTOFOCUS_YOLO_DEVICE = "0"    # 首张 CUDA 设备
$env:AUTOFOCUS_YOLO_DEVICE = "cpu"  # 强制 CPU
```

Ultralytics 联网统计由应用在模型导入时关闭；工控机推理不依赖外网。

## Python 3.12 CUDA 回退环境

需要对比运行时，可退出自动对焦 GUI，在项目根目录创建并验证独立的 Python
3.12 环境。两个模型参数必须填写工控机上的实际绝对路径：

```powershell
powershell -ExecutionPolicy Bypass `
  -File deploy\migrate_python312_cuda.ps1 `
  -SegmentationModel "C:\模型\分割\best.pt" `
  -CircleModel "C:\模型\找圆\best.pt"
```

脚本在 `runtime\venvs\autofocus-py312` 创建独立环境，依次验证 Python
3.12、CUDA 张量运算、找圆模型推理和分割模型推理。原来的
`runtime\venvs\autofocus` 不会被删除或修改。只有看到
`PYTHON312_CUDA_READY` 后，才使用下面的入口启动软件：

```powershell
deploy\start_gui_py312.cmd
```

普通依赖和 CUDA 依赖必须分开安装，避免 pip 用 PyTorch 索引查询
PyQt5、OpenCV 等普通包。

如果工控机不能访问 Anaconda 仓库，可以离线克隆 base：

```powershell
conda create --offline --yes --name autofocus --clone base
```

## 运行资产

模型文件不提交到 Git，需要单独放到：

```text
assets\models\ai\best_resnet.pt
assets\models\ai\results.json
assets\models\yolo\best.pt
```

`dlfocus_out\preprocessed.pt` 是训练缓存，不部署到工控机。

部署后可在 PowerShell 中校验模型：

```powershell
Get-Content deploy\ASSETS.sha256 | ForEach-Object {
    $expected, $relativePath = $_ -split "  ", 2
    $actual = (Get-FileHash $relativePath -Algorithm SHA256).Hash
    [PSCustomObject]@{
        File = $relativePath
        Passed = $actual -eq $expected
    }
}
```
