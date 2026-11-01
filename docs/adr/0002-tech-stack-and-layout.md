# 0002 技术栈与代码组织

- 状态：已采纳
- 日期：2026-11-01

## 背景

约束：完全离线、只用本地模型；个人和团队都能用，并能升级到企业版；每个功能模块化，扩展时改动最小；
存储固定为 PostgreSQL、Valkey（兼容 Redis）和对象存储；项目授权 Apache 2.0。

## 决策

| 部分 | 选择 |
| --- | --- |
| 语言与工具 | Python 3.13，uv 管理依赖，ruff 代码检查，pyright 类型检查；开发环境 PyCharm |
| 后端 API | FastAPI + Pydantic v2 + SQLAlchemy 2.0 + Alembic |
| 后台任务 | Celery + Valkey；OCR、大模型、导出分三个队列 |
| PDF | pypdfium2（渲染）+ pdfplumber（原生文字与坐标） |
| OCR | RapidOCR 运行 PaddleOCR 的 PP-OCR 模型（模型随包，离线可用） |
| 抽取模型 | llama-server + Qwen3.5（无显卡 4B，有显卡 9B），按 JSON Schema 约束输出 |
| 存储 | PostgreSQL、Valkey、SeaweedFS（只通过 S3 接口访问） |
| 前端 | React + TypeScript + Vite + Ant Design + TanStack Query |
| 部署 | Docker Compose + Nginx，离线安装包 |

代码按功能分目录：`modules/<功能>/` 内分 schemas、models、repository、service、router；
流水线步骤是 `plugins/<扩展点>/` 下的插件，由注册表登记、配置选择。

目前仓库只有一个 Python 包（jacara，位于 `backend/src/jacara`）。自动化测试和评测方案尚未确定，暂不加入。

## 放弃的方案

- **Flet / PyQt 桌面程序**：多人使用需要每台安装；审核页面在浏览器里显示 PDF 和高亮更方便。
- **Java 后端**：OCR 和本地模型生态都在 Python，多一种语言只增加负担。
- **按层分目录**（所有 controller 放一起）：加一个功能要改五个目录，不符合"最小修改扩展"。
- **PyMuPDF**：AGPL 授权，与 Apache 2.0 冲突。
- **MinIO**：已停止维护，改用 SeaweedFS。
- **Redis**：授权变更，改用 Valkey（BSD-3）。
- **直接用 PaddleOCR 包**：需要 PaddlePaddle 框架且首次运行联网下载模型；需要表格识别时再作为插件加入。

## 后果

- 新增功能 = 新增一个 `modules/` 子包；新增 OCR、模型、导出格式 = 新增一个插件并改配置。
- 暂无自动化测试：改动后靠代码检查、类型检查和手动运行验证，风险较高，确定方案后尽快补上。
- 需要重新评估的情况：单机显卡不够用（考虑 vLLM）、需要多机部署（考虑 Kubernetes）。
