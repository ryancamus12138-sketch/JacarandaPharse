# JacarandaParse

完全离线、本地部署的文档结构化工具：把收据、发票等 PDF 和图片抽取成结构化字段，
**每个值都能回到原文找到出处**，找不到出处的值会被标红，交给人工确认。

## 快速开始（开发）

需要 [uv](https://docs.astral.sh/uv/)。Python 由 uv 自动管理。

    uv sync                      # 创建虚拟环境并安装依赖
    uv run jacara --version
    uv run ruff check .          # 代码检查
    uv run pyright               # 类型检查

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
| 环境与骨架 | 工具、目录结构、配置、Git、CI | 进行中 |
| 处理流水线 | OCR → 字段抽取 → 证据对齐 → 规则校验 → 导出 | |
| 后端 | 数据格式、流水线编排、数据库、业务服务、API | |
| 前端 | 文档列表、审核工作台、导出、设置 | |
| 部署 | Docker Compose、离线安装包 | |
