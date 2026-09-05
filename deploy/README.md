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

部署环境固定为 Python 3.12。已有 Python 3.13 环境会自动回退为 CPU 推理，
避免 PyTorch CUDA 在 Qt 工作线程中造成进程级崩溃。重建 3.12 环境后默认
恢复 Ultralytics 的 CUDA 自动选择。也可在完成独立诊断后临时显式指定设备：

```powershell
$env:AUTOFOCUS_YOLO_DEVICE = "0"    # 首张 CUDA 设备
$env:AUTOFOCUS_YOLO_DEVICE = "cpu"  # 强制 CPU
```

Ultralytics 联网统计由应用在模型导入时关闭；工控机推理不依赖外网。

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
