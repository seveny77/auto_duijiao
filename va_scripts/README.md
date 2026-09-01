# VA 标定：保存绝对位置与清晰度

粗扫阶段另见 [粗扫与NCC接入说明](粗扫与NCC接入说明.md)：
`coarse_scan.py` 保存粗扫数据，`coarse_ncc.py` 将粗扫数据与本标定格式进行匹配。

精扫阶段见 [精扫接入说明](精扫接入说明.md)：`fine_scan.py` 根据中心、步距和单边
点数规划采样，最终选择本轮实拍最高分帧，不重新执行 NCC 或拟合。

`calibration_sharpness.py` 只使用标准库，接收普通 Python 参数并返回 JSON 字符串。
不包含软件变量名、变量读写接口或自动执行入口；这些部分由调用方自行接入。

## 采样规则

- 起点不触发，移动一个步距后采第一帧，终点触发最后一帧。
- 步距为正数，起点、终点、步距的单位均为 um（微米）。
- 支持正反方向，方向由起点和终点决定。
- 采样总数为 `abs(终点 - 起点) / 步距`，不额外加 1。
- 第 k 帧（从 1 开始）的位置为 `起点 + 方向 * 步距 * k`。
- 使用 Decimal 计算采样位置，避免逐帧浮点累加；输出位置仍为 JSON 数字。

用户已确认逐帧按触发顺序输入、每帧只执行一次，位置单位为 um。
**位置按帧顺序推算，是指令位置而非编码器实测位置。** 不要并行更新同一变量。
仅有起终点、步距和清晰度时，脚本无法识别漏掉了哪一帧或重复了哪一帧。
如将来存在丢帧、乱序或重复执行，需要增加触发序号或与图像同步的实际位置。

## 函数参数

起点、终点和步距的单位均为 um。下面是函数参数，不是预设的软件变量名。

| 参数 | 类型 | 用途 |
| --- | --- | --- |
| `start_position` | 数值 | 扫描起点的绝对位置 |
| `end_position` | 数值 | 扫描终点的绝对位置 |
| `step` | 数值 | 正步距 |
| `sharpness` | 数值 | 当前帧清晰度 |
| `history_json` | str | 上次返回的 JSON 字符串，首次可为空字符串 |
| `reset` | bool | 默认 False；True 时清空历史并记录当前帧 |

## 调用方式

将文件中的函数复制到脚本，或在 Python 导入路径配置好后导入使用。
加载本文件只定义函数，不会自动采样或保存数据。

新一轮扫描开始前初始化一次，返回空记录，不记录起点：

```python
result_json = initialize_calibration(start_position, end_position, step)
```

每帧清晰度计算成功后调用一次，传入此前保存的完整历史字符串：

```python
result_json = append_sharpness(
    sharpness=current_sharpness,
    start_position=start_position,
    end_position=end_position,
    step=step,
    history_json=previous_json,
)
```

两个函数的返回值均为 Python `str`。由调用方保存 `result_json`，
并在下一帧将其作为 `history_json` 传入。不要每帧清空历史或设置 `reset=True`。
函数报错时不产生新结果，应保留原数据并停止本轮标定。

扫描结束后解析最后保存的字符串并检查完整性：

```python
data = json.loads(result_json)
if not data["complete"] or len(data["samples"]) != data["expected_count"]:
    raise ValueError("标定采样数量不足")

positions = [sample["position"] for sample in data["samples"]]
scores = [sample["sharpness"] for sample in data["samples"]]
```

空字符串允许首帧自动初始化，但每一轮扫描仍建议显式调用初始化函数。

## 数据示例

起点 100、终点 130、步距 10（均为 um），三帧清晰度分别为 12.3、45.6、38.2：

```json
{
  "schema_version": 1,
  "position_unit": "um",
  "start_position": 100,
  "end_position": 130,
  "step": 10,
  "expected_count": 3,
  "samples": [
    {"position": 110, "sharpness": 12.3},
    {"position": 120, "sharpness": 45.6},
    {"position": 130, "sharpness": 38.2}
  ],
  "complete": true
}
```

函数返回紧凑的单行 JSON 字符串。用对象列表保存位置和值，
避免使用 JSON 对象的字符串键来表示数值坐标。
反向扫描 130 → 100、步距 10 时，依次保存 120、110、100。

## 校验和限制

- 拒绝零/负步距、相同起终点、行程不能整除步距及非有限数值。
- 记录中途改变扫描参数会报错，必须显式初始化后开始新一轮。
- 超出预期采样数会报错，不自动清空或覆盖完成的数据。
- 历史 JSON 损坏或位置错配会报错；不会修改传入的历史字符串。
- 旧版 `{"scores":[...]}` 不包含位置，不能直接续写；需要初始化新格式。
- 只保存原始清晰度，未进行 NCC、归一化或磁盘持久化。
- 软件变量的读写、字符串容量和重启后的持久化由调用方处理。
