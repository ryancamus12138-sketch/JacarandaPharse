# 0046 v4 第 4 批：在网页上换模型

状态：已采纳（2026-11）

系统信息页多一块"换模型"（`GET/PUT /models`）：大模型、视觉模型（mmproj）、OCR 文字检测、OCR 文字识别、
OCR 识别字典、表格结构模型，各自选用 `models/` 里的哪个文件。

- **离线，只能选已有的文件**：大模型和视觉模型在 `models/*.gguf`（文件名带 mmproj 的是视觉模型），
  自己训练的 OCR 模型放 `models/ocr/`（文件名带 det、rec 的 .onnx，字典是 .txt），表格模型放 `models/table/`。
  不在候选里的路径一律拒绝。
- **存在哪**：`models/selected.json`（models/ 不进仓库，每台电脑自己的）。
- **谁优先**：`.env` 里写了（JACARA_MODEL、JACARA_MMPROJ、JACARA_OCR_DET / _REC / _DICT、
  JACARA_TABLE_MODEL）> 网页上选的 > models/ 里自动找到的（大模型只有一个才自动用）> 自带的（OCR）。
  页面上每一项写着"谁定的"；.env 定的网页上改不了。
- **生效**：大模型、视觉模型在 `jacara start` 时加载，OCR 和表格模型在后台任务启动时加载，所以改完要
  `jacara stop`、`jacara start`。页面比较选的大模型和模型服务正在用的（llama-server `/props`），不一样就提醒。
  不在网页上直接重启：重启会中断正在处理的文档，交给人在终端里做。
- **权限**：看谁都能看；换只有管理员（新权限 `system:manage`，个人模式下就是自己）。换了记审计日志。
- OCR 识别模型和字典一般要配套：换了识别模型，字典也选对应的那个。
