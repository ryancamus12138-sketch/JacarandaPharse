# JacarandaParse

完全离线、本地部署的文档结构化工具：把收据、发票等 PDF 和图片抽取成结构化字段，
**每个值都能回到原文找到出处**，找不到出处的值会被标红，交给人工确认。

## 快速开始（开发）

需要 [uv](https://docs.astral.sh/uv/)。Python 由 uv 自动下载。

```bash
uv sync                      # 创建虚拟环境并安装依赖
uv run jacara --version
uv run ruff check .          # 代码检查
uv run pyright               # 类型检查
```

本地模型需要 llama.cpp（Mac：`brew install llama.cpp`）和一个 GGUF 模型文件，放在 `models/`（不进 Git）。

## 命令行

```bash
uv run jacara ocr 文件                                # 识别图片或 PDF 里的文字
uv run jacara serve --model models/模型.gguf          # 启动本地模型服务
uv run jacara extract 文件                            # 抽取字段、核对出处、规则校验
uv run jacara extract 文件 --fields full --excel results/结果.xlsx
```

支持图片（jpg、png 等）和 PDF。有文字层的 PDF 直接读取，扫描件自动 OCR。

## 处理流水线

```text
文件 → 读取（文字层 / OCR）→ 抽取（本地大模型）→ 证据对齐 → 规则校验 → 导出（Excel）
```

| 步骤 | 位置 | 作用 |
| --- | --- | --- |
| 读取 | `plugins/ocr/` | 图片和扫描件用 RapidOCR；电子 PDF 用 pdfplumber 读文字层 |
| 抽取 | `plugins/model_backends/`、`plugins/extractors/` | llama-server + Qwen3.5，JSON Schema 约束输出 |
| 对齐 | `plugins/aligners/` | 每个值在原文里找出处（行号、坐标），找不到标红 |
| 校验 | `plugins/validators/` | 用数字互相验证（现金 − 总额 = 找零等） |
| 导出 | `plugins/exporters/` | Excel：汇总、明细、来源 |

## 目录

| 目录 | 内容 |
| --- | --- |
| `backend/src/jacara/` | 产品代码：处理流水线、后端业务、命令行 |
| `web/` | 前端 |
| `deploy/` | Docker Compose 与离线安装包 |
| `docs/adr/` | 架构决策记录 |

## 开发路线

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 环境与骨架 | 工具、目录结构、配置、Git、CI | 完成 |
| 处理流水线 | 读取 → 抽取 → 对齐 → 校验 → 导出 | 完成（命令行版） |
| 后端 | 数据库、流水线编排、业务服务、API | |
| 前端 | 文档列表、审核工作台、导出、设置 | |
| 部署 | Docker Compose、离线安装包 | |

## 授权

Apache License 2.0，见 [LICENSE](LICENSE)。
