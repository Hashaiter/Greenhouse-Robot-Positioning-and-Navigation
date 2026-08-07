# YOLOv8n 导航标志检测初步模型 v0.1

## 训练配置

- 模型：YOLOv8n
- 类别：navigation_marker
- 数据量：553张图片、553个目标
- 训练轮数：20
- Batch Size：16
- 图片尺寸：640×640
- 训练设备：NVIDIA GeForce RTX 5060 Laptop GPU

## 验证结果

- Precision：99.90%
- Recall：100.00%
- mAP@50：99.50%
- mAP@50-95：78.78%

## 文件说明

- `results.png`：训练与验证曲线
- `validation_metrics.json`：验证集指标
- `pipeline_summary.json`：Pipeline运行摘要
- `predictions/`：测试集推理效果样例

## 注意事项

当前训练集、验证集和测试集包含较多相邻视频帧，验证指标可能偏高。
本模型属于第一版初步训练结果，后续需要增加真实大棚环境、负样本和独立测试数据。