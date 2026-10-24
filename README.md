# Greenhouse-Robot-Positioning-and-Navigation

大棚无人车定位与导航项目。目前仓库包含视觉识别和 STM32 运动控制两部分，具体进展、运行方法和测试结果分别记录在各模块中。

## 模块

| 模块 | 目录 | 当前内容 |
| --- | --- | --- |
| 视觉识别 | [vision/yolov8_navigation_marker](vision/yolov8_navigation_marker/README.md) | YOLOv8 数据处理、训练、验证、推理与初步训练结果 |
| 运动控制 | [motion/stm32_chassis_control](motion/stm32_chassis_control/README.md) | STM32 电机控制、编码器与轮速反馈、串口协议及架空测试记录 |

```text
vision/
  yolov8_navigation_marker/
motion/
  stm32_chassis_control/
```

## 当前运动控制进展

串口控制两轮前进、STOP 主动停车，以及停止续发命令后的自动超时停车已经完成短时架空测试。当前仍为占空比控制；轮速闭环、ROS 串口桥和落地带载验证尚未完成。

[运动控制详细记录](motion/stm32_chassis_control/docs/运动控制记录-20261024.md)包含已做的测试、固件版本和后续事项。
