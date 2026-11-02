# 0003 OCR：PaddleOCR 训练，RapidOCR 部署

- 状态：已采纳（细化 0002 中的 OCR 选择）
- 日期：2026-11-02

## 背景

产品要求完全离线，并且要能在没有独立显卡的电脑（包括 Mac）上运行。以后还要能换成自己微调的模型，
例如识别手写体或特定发票版式。

PaddleOCR 的模型效果好、训练工具完整，但运行需要 PaddlePaddle 框架：安装包大，不支持 Mac 的 GPU，
第一次运行还会联网下载模型。

## 决策

**训练和部署分开：**

- **训练、微调**：用 PaddleOCR 官方工具，训练完用 paddle2onnx 导出成 `.onnx` 文件。
- **部署、运行**：用 RapidOCR（Apache 2.0）在 ONNX Runtime 上运行这些模型。默认模型是
  PP-OCRv6 small 的检测和识别模型，随安装包分发，安装后完全离线可用。
- **设备**：默认 CPU；可选 CUDA（NVIDIA 显卡）和 CoreML（Mac）。

**可替换的接口：**

- `plugins/ocr/base.py` 定义 `OcrEngine` 接口和 `OcrConfig` 配置；任何引擎只要实现
  `recognize(image) -> OcrPage` 就能接入。
- `plugins/ocr/__init__.py` 是登记表，调用方只写 `create_ocr_engine(config)`。
- 换模型：只改配置（`det_model`、`rec_model`、`rec_dict`），命令行对应 `--rec-model` 等参数。
- 换引擎：在 `plugins/ocr/` 新增一个文件，再在登记表里加一行。
- 表格识别：`plugins/tables/` 已预留接口，以后接入 RapidTable。
- 每次识别结果都记录引擎版本和模型名（`OcrPage.engine`、`OcrPage.models`）。

## 第一次试用（SROIE 第 0 张收据，Mac CPU）

45 行，约 1 秒。大部分正确，但有高置信度的错误：地址 `NO.53` 读成 `NO.55`（0.96），
找零 `1.00` 读成 `100`（1.00）。淡色中文的置信度较低（0.64～0.86）。

结论：置信度不能单独作为判断依据，后面需要证据对齐和规则校验（例如 现金 − 总额 = 找零）。

## 放弃的方案

- **直接用 PaddleOCR 包运行**：依赖重，Mac 上只能用 CPU，首次运行要联网。保留为以后的可选插件。
- **只用 RapidOCR，不考虑训练**：以后无法针对手写体或特定发票提高准确率。

## 后果

- 安装简单，离线可用，Mac 和普通电脑都能跑。
- 自己微调的模型要多一步"导出 ONNX"，并确认 RapidOCR 能加载。
- 需要重新评估的情况：中文发票效果不足、需要版面分析，或 ONNX 导出不支持新模型。
