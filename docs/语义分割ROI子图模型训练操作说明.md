# 检测 ROI 子图语义分割模型训练操作说明

## 1. 文档目的

本流程用于训练后续检测模块导入的 YOLO-Seg 语义分割模型。模型输入不是整张最终成像，而是由找圆结果生成的检测 ROI 子图。

训练和在线推理必须尽量保持一致：

```text
最终图 → 找圆 → 以圆心裁切固定 ROI → YOLO-Seg 分割
```

因此，训练图片应尽量使用和在线检测相同尺寸、相同裁切范围、相同成像条件的 ROI 子图。

本项目的训练脚本不会被 GUI 自动调用，也不会在导入脚本时加载模型或初始化 CUDA。

## 2. 环境准备

建议使用当前项目验证过的 Python 环境：

```powershell
E:\Users\Administrator\miniconda3\python.exe
```

确认 Ultralytics 可用：

```powershell
E:\Users\Administrator\miniconda3\python.exe -c "from ultralytics import YOLO; print('ultralytics OK')"
```

确认 GPU 可用：

```powershell
E:\Users\Administrator\miniconda3\python.exe -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

如果只想先检查命令参数，不会真正训练：

```powershell
E:\Users\Administrator\miniconda3\python.exe tools\train_segmentation.py --help
```

## 3. 原始数据目录要求

数据准备脚本要求源目录固定包含 `img` 和 `label` 两个子目录：

```text
roi_source_v1/
├─ img/
│  ├─ roi_000001.jpg
│  ├─ roi_000002.jpg
│  └─ ...
└─ label/
   ├─ roi_000001.json
   ├─ roi_000002.json
   └─ ...
```

有缺陷的图片应有一个同名 JSON：

```text
img/roi_000001.jpg
label/roi_000001.json
```

JSON 使用 LabelMe polygon 格式，至少包含：

```json
{
  "shapes": [
    {
      "label": "异物",
      "shape_type": "polygon",
      "points": [[100, 120], [140, 120], [140, 160], [100, 160]]
    }
  ],
  "imageWidth": 1024,
  "imageHeight": 1024
}
```

当前脚本默认类别为：

```text
异物 脏污
```

如果你的标注只有一个类别，必须在转换时显式指定类别，例如：

```powershell
--classes 异物
```

没有缺陷的图片可以不提供 JSON。数据转换脚本会将这类图片自动作为负样本处理，并在输出数据集的 `labels/<split>/` 下生成同名空 `.txt` 文件。例如：

```text
img/roi_000002.jpg                 # 没有对应 JSON
→ labels/train/roi_000002.txt      # 自动生成，内容为空
```

这种空 `.txt` 是 YOLO/YOLO-Seg 表示“图片中没有目标”的标准方式。源目录不会被修改，也不会自动伪造 LabelMe JSON。

注意事项：

- `shape_type` 必须是 `polygon`；矩形、圆形等其他 LabelMe 类型会被拒绝。
- JSON 的 `imageWidth`、`imageHeight` 必须和实际图片一致。
- 对于有缺陷的图片，图片和 JSON 的文件名主干必须完全相同。
- 多边形至少需要 3 个点，点坐标必须在图片范围内。
- 不要把整图标注和 ROI 子图标注混在同一个数据集里。
- 每个 ROI 子图应尽量保持在线推理时的固定边长，例如当前配置使用的 `roi_size_px=1024`。

## 4. 将 LabelMe 数据转换为 YOLO-Seg 数据集

转换脚本会完成以下工作：

1. 检查有标注图片的 JSON 是否一一对应，并将缺少 JSON 的图片记录为负样本；
2. 将 polygon 坐标归一化为 YOLO-Seg 格式；
3. 按固定随机种子划分 train、val、test；
4. 生成 `data.yaml`、类别表、划分清单和数据摘要。

例如，源数据位于 `F:\项目\自动对焦\code\yoloSegSourceROI`，输出到新目录 `yoloSegData\roi_v1`：

```powershell
E:\Users\Administrator\miniconda3\python.exe tools\prepare_segmentation_dataset.py `
  --source "F:\项目\自动对焦\code\yoloSegSourceROI" `
  --output "F:\项目\自动对焦\code\yoloSegData\roi_v1" `
  --classes 异物 脏污 `
  --train-ratio 0.8 `
  --val-ratio 0.1 `
  --test-ratio 0.1 `
  --seed 42
```

输出目录会类似于：

```text
yoloSegData/roi_v1/
├─ images/train、val、test/
├─ labels/train、val、test/
├─ data.yaml
├─ classes.json
├─ split_manifest.json
└─ dataset_summary.json
```

输出目录必须不存在。脚本为了避免覆盖旧数据，已存在时会直接报错；请使用新的版本目录，例如 `roi_v2`、`roi_v3`。

转换完成后先检查摘要：

```powershell
Get-Content "F:\项目\自动对焦\code\yoloSegData\roi_v1\dataset_summary.json"
Get-Content "F:\项目\自动对焦\code\yoloSegData\roi_v1\data.yaml"
```

重点确认：

- 图片总数是否正确；
- train、val、test 是否都有数据；
- 各类别数量是否符合预期；
- `data.yaml` 中的类别编号是否正确。
- `unannotated_image_count` 是否符合预期；
- 没有 JSON 的图片是否在输出 `labels` 目录中生成了空 `.txt` 文件。

如果 JSON 文件与图片同名但内容格式错误，脚本仍会报错。这和“完全没有 JSON”的无缺陷负样本是两种情况：前者需要修正标注，后者会自动处理。

## 5. 开始训练

训练入口是：

```text
tools/train_segmentation.py
```

推荐先使用一个较小配置做冒烟检查：

```powershell
E:\Users\Administrator\miniconda3\python.exe tools\train_segmentation.py `
  --data "F:\项目\自动对焦\code\yoloSegData\roi_v1\data.yaml" `
  --base-model yolo11n-seg.pt `
  --epochs 1 `
  --imgsz 1024 `
  --batch 1 `
  --device 0 `
  --workers 0 `
  --project "F:\项目\自动对焦\code\yoloSegRuns" `
  --name roi_seg_smoke `
  --smoke
```

正式训练示例：

```powershell
 C:\Autofocus\runtime\venvs\autofocus\Scripts\python.exe tools\train_segmentation.py `
  --data "C:\Seg\yoloSegDataMTF\roi_v2\data.yaml" `
  --base-model yolo11n-seg.pt `
  --epochs 100 `
  --imgsz 1024 `
  --batch 4 `
  --device 0 `
  --workers 4 `
  --project "F:\项目\自动对焦\code\yoloSegRuns" `
  --name roi_seg_v1 `
  --patience 30 `
  --seed 42
```

如果显存不足，按以下顺序降低压力：

```text
batch 4 → 2 → 1
workers 4 → 2 → 0
imgsz 1024 → 768 或 640
```

`imgsz` 是训练时模型输入尺寸，不是当前 GUI 的 `inference_imgsz` 配置。上线推理时应根据训练效果和显存情况设置相同或兼容的尺寸。

## 6. 使用已有权重接续训练

“接续训练”有两种含义，命令不要混用。

### 6.1 将已有 best.pt 作为新一轮训练的初始权重

适用于：新增数据、重新随机划分数据集、希望重新开始 epoch 计数。

```powershell
E:\Users\Administrator\miniconda3\python.exe tools\train_segmentation.py `
  --data "F:\项目\自动对焦\code\yoloSegData\roi_v2\data.yaml" `
  --base-model "F:\项目\自动对焦\code\yoloSegRuns\roi_seg_v1\weights\best.pt" `
  --epochs 100 `
  --imgsz 1024 `
  --batch 4 `
  --device 0 `
  --workers 4 `
  --project "F:\项目\自动对焦\code\yoloSegRuns" `
  --name roi_seg_v2
```

这种方式会加载模型权重，但不是恢复原训练任务的优化器、epoch 和学习率状态。

### 6.2 从中断的训练目录恢复

适用于训练中断、电脑重启或进程异常退出，希望继续原任务。

```powershell
E:\Users\Administrator\miniconda3\python.exe tools\train_segmentation.py `
  --data "F:\项目\自动对焦\code\yoloSegData\roi_v1\data.yaml" `
  --base-model "F:\项目\自动对焦\code\yoloSegRuns\roi_seg_v1\weights\last.pt" `
  --epochs 100 `
  --imgsz 1024 `
  --batch 4 `
  --device 0 `
  --workers 4 `
  --project "F:\项目\自动对焦\code\yoloSegRuns" `
  --name roi_seg_v1_resume `
  --resume
```

只有在确实要恢复原训练状态时才使用 `--resume`。如果只是把旧模型用于新数据训练，不要加 `--resume`。

## 7. 训练结果和验收

训练结果通常位于：

```text
F:\项目\自动对焦\code\yoloSegRuns\roi_seg_v1\
├─ weights/best.pt
├─ weights/last.pt
├─ results.csv
├─ results.png
├─ confusion_matrix.png
└─ val_batch*.jpg
```

重点检查：

- `weights/best.pt` 是否生成；
- `results.png` 中训练损失和验证指标是否趋于稳定；
- 验证图中的多边形是否贴合缺陷，而不是只输出矩形框；
- 小缺陷是否被大量漏检；
- 背景颗粒是否被误检为缺陷；
- `异物` 和 `脏污` 是否出现类别混淆。

正式导入 GUI 前，至少应使用几张未参与训练的 ROI 子图做离线推理验证。确认模型任务确实是 `segment`，且输出包含 masks；只有检测框没有 masks 的模型不能用于本检测模块。

## 8. 导入检测程序前的对应关系

训练好的权重文件只需要作为检测页的模型路径选择，不需要复制进 Python 包。当前检测程序读取的是用户手动选择的单个 `.pt` 文件。

在线推理要注意以下对应关系：

| 训练阶段 | 在线检测阶段 |
|---|---|
| ROI 子图边长 | `roi_size_px` |
| 训练输入尺寸 | `inference_imgsz` |
| 类别顺序 | `data.yaml` 中的 names 和模型 names |
| 训练标注坐标 | ROI 子图坐标 |
| 推理结果坐标 | 程序会平移回原图坐标 |
| 训练模型 | 检测页手动选择并加载的 `.pt` |

当前检测服务每个 ROI 最多保留 20 个实例。这个限制是在推理调用和结果转换两处执行，不需要在训练脚本中额外设置。

## 9. 常见问题

### 输出目录已存在

使用新的输出目录，不要直接覆盖旧数据：

```text
roi_v1 → roi_v2 → roi_v3
```

### 图片和 JSON 尺寸不一致

检查 JSON 中的 `imageWidth`、`imageHeight`，必须与实际图片尺寸完全一致。不要用整图尺寸去填写已经裁切后的 ROI 标注。

### 显存不足

优先降低 `--batch`，再降低 `--imgsz`。如果同时运行 GUI 检测程序，应先关闭正在占用 GPU 的推理进程。

### 模型能输出框但没有多边形

确认使用的是 `yolo11n-seg.pt` 或其他 `*-seg.pt` 分割基础模型，并检查训练输出模型的 task 是否为 `segment`。普通检测模型不能直接替代分割模型。

### 新模型导入后效果异常

优先检查 ROI 尺寸、类别顺序和 `inference_imgsz`。如果训练图片是固定 ROI 子图，而在线程序送入的是整图或不同大小的裁切区域，模型效果会明显下降。

## 10. 推荐的完整操作顺序

```text
准备 ROI 子图和同名 LabelMe JSON
        ↓
检查类别名称、尺寸和多边形标注
        ↓
prepare_segmentation_dataset.py 转换
        ↓
检查 dataset_summary.json 和 data.yaml
        ↓
运行 --smoke 做 1 epoch 冒烟训练
        ↓
正式训练并保留独立版本目录
        ↓
检查 best.pt、results.png 和验证图片
        ↓
用未参与训练的 ROI 子图离线测试
        ↓
在检测结果页手动选择 best.pt 加载
        ↓
再进行实际整图 → 找圆 → ROI → 分割验证
```
