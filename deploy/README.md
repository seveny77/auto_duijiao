# 工控机部署说明

目标目录：`C:\Autofocus\app`。

## Python 环境

在项目根目录执行：

```powershell
conda env create -f deploy\environment.yml
conda run -n autofocus python -m pip install -r deploy\requirements-runtime.txt
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
