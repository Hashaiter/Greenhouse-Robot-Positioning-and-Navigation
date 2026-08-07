# YOLOv8 大创目标检测 Pipeline

这是根据 `greenhouse robot.v1i.darknet.zip` 定制的 YOLOv8 目标检测工程，适用于 Windows、VS Code、Conda 和 Python 3.11。

完整流程：

```text
Darknet ZIP
  → 自动转换为 Ultralytics YOLO 目录
  → 图片和标签检查
  → 下载 yolov8n.pt
  → 训练
  → 验证
  → 可选推理
  → 输出 best.pt 和 JSON 报告
```

## 1. 数据集结论

上传的数据包含 553 张 512×512 图片，共 1 类、553 个目标框。图片、标签和坐标格式均合法，可以训练 YOLOv8。

原类别名是 `label`，抽查画面显示目标为黑框三角形导航标志牌，因此本工程默认名称为：

```yaml
0: navigation_marker
```

更详细的数据问题见 [DATASET_AUDIT.md](DATASET_AUDIT.md)。当前验证集和测试集含有大量与训练集相邻的视频帧，适合试训，不适合作为大创最终泛化指标。

## 2. 创建独立环境

打开 VS Code 终端并进入本工程：

```powershell
cd "D:\Github\YOLOv8-greenhouse-pipeline"
conda create -n yolov8-greenhouse python=3.11 -y
conda activate yolov8-greenhouse
python -m pip install -r requirements.txt
```

## 3. 最简单的一键试训

把原始压缩包放到工程根目录，然后执行：

```powershell
python scripts\run_pipeline.py `
  --dataset-zip "greenhouse robot.v1i.darknet.zip" `
  --epochs 20 `
  --batch 16 `
  --imgsz 640 `
  --device 0 `
  --name greenhouse_smoke
```

该命令会自动完成：

1. 将 Darknet 格式转换成 `datasets/greenhouse_robot`；
2. 把 `valid` 正确映射成 `val`；
3. 排除三个 `_darknet.labels` 文件；
4. 生成 `configs/greenhouse_robot.yaml`；
5. 检查 553 张图片和标签；
6. 从 ModelScope 准备 `yolov8n.pt`；
7. 训练 20 轮；
8. 验证并输出 Precision、Recall、mAP50 和 mAP50-95。

转换器会把数据集的绝对路径写入 YAML，避免 Ultralytics 沿用其他
YOLO 工程的全局 `datasets_dir` 设置而找错目录。

如果电脑没有 NVIDIA 独立显卡或 CUDA 环境，把：

```powershell
--device 0
```

改成：

```powershell
--device cpu
```

CPU 可以运行，但训练会比较慢。

## 4. 第二次运行

数据已经转换后，不要再传 `--dataset-zip`：

```powershell
python scripts\run_pipeline.py `
  --epochs 100 `
  --batch 16 `
  --imgsz 640 `
  --device 0 `
  --name greenhouse_formal
```

如果确实要重新转换压缩包，应显式添加：

```powershell
--overwrite-dataset
```

旧数据集不会直接删除，而是被重命名为带时间戳的备份目录。

## 5. 训练后自动推理

可使用测试集进行效果图推理：

```powershell
python scripts\run_pipeline.py `
  --epochs 20 `
  --batch 16 `
  --device 0 `
  --name greenhouse_smoke_predict `
  --source "datasets\greenhouse_robot\images\test"
```

## 6. 分步骤运行

### 转换原始 Darknet 压缩包

```powershell
python scripts\prepare_dataset.py `
  --input "greenhouse robot.v1i.darknet.zip"
```

### 检查数据集

```powershell
python scripts\check_dataset.py `
  --data configs\greenhouse_robot.yaml `
  --report outputs\dataset_check_report.json
```

### 下载 YOLOv8n 权重

```powershell
python scripts\download_model.py
```

### 训练

```powershell
python scripts\train.py `
  --model models\pretrained\yolov8n.pt `
  --data configs\greenhouse_robot.yaml `
  --epochs 100 `
  --batch 16 `
  --imgsz 640 `
  --device 0 `
  --name greenhouse_formal
```

### 验证

将下面的 `best.pt` 路径替换为训练结束后终端显示的实际路径：

```powershell
python scripts\validate.py `
  --weights "runs\greenhouse_formal\weights\best.pt" `
  --data configs\greenhouse_robot.yaml `
  --report outputs\validation_metrics.json
```

### 单张图片推理

```powershell
python scripts\predict.py `
  --weights "runs\greenhouse_formal\weights\best.pt" `
  --source "test.jpg"
```

## 7. 主要输出

一键流程结束后重点检查：

```text
runs/<训练名称>/weights/best.pt
runs/<训练名称>/weights/last.pt
outputs/pipeline/<训练名称>/dataset_check_report.json
outputs/pipeline/<训练名称>/validation_metrics.json
outputs/pipeline/<训练名称>/pipeline_summary.json
```

正式交付使用 `best.pt`，并同时保留：

- Precision、Recall、mAP50、mAP50-95；
- 验证曲线和混淆矩阵；
- 推理效果图；
- 误检和漏检案例；
- `configs/greenhouse_robot.yaml`；
- 完整可复现命令。

## 8. 运行代码自检

无需下载模型即可测试转换器和检查器：

```powershell
python -m unittest discover -s tests -v
```

## 9. 与原 YOLO11 Pipeline 的关系

本工程沿用了原项目中可靠的训练、验证、推理和 JSON 摘要思路，但不是简单替换模型名。针对大创数据新增了：

- Darknet ZIP 自动适配；
- `valid → val` 映射；
- `_darknet.labels` 排除；
- 类别语义重命名；
- ZIP 路径安全检查；
- 原子式数据集替换和旧版本备份；
- 视频相邻帧泄漏分析；
- 面向大创数据的一键命令。

YOLOv8 通过 Ultralytics Python API 训练，默认使用 COCO 预训练的 `yolov8n.pt`。
