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
uv run pytest                # 自动化测试
```

本地模型需要 llama.cpp（Mac：`brew install llama.cpp`）和一个 GGUF 模型文件，放在 `models/`（不进 Git）。

后端需要的数据库、任务队列、对象存储用 Docker 运行（Mac 上装 Docker Desktop）：

```bash
cp .env.example .env         # 然后把 .env 里的 change-me 改成随机值：openssl rand -hex 16
docker compose up -d         # 启动 PostgreSQL、Valkey、SeaweedFS
uv run alembic upgrade head  # 建表（以后表结构有变化时也运行这一条）
uv run jacara doctor         # 检查所有服务是否连得上
```

`.env` 里是你自己的密码，不会进 Git。端口被占用时，改 `.env` 里对应的端口即可（例如 `POSTGRES_PORT=5433`）。

## 命令行

```bash
uv run jacara ocr 文件                                # 识别图片或 PDF 里的文字
uv run jacara serve --model models/模型.gguf          # 启动本地模型服务
uv run jacara extract 文件                            # 抽取字段、核对出处、规则校验
uv run jacara extract 文件 --fields full --excel results/结果.xlsx
uv run jacara extract 文件 --save                     # 同时存进数据库
uv run jacara doctor                                  # 检查数据库、队列、对象存储、本地模型
```

支持图片（jpg、png 等）和 PDF。有文字层的 PDF 直接读取，扫描件自动 OCR。

## 处理流水线

```text
文件 → 读取（文字层 / OCR）→ 抽取（本地大模型）→ 证据对齐 → 规则校验 → 导出（Excel）/ 存入数据库
```

| 步骤 | 位置 | 作用 |
| --- | --- | --- |
| 读取 | `plugins/ocr/` | 图片和扫描件用 RapidOCR；电子 PDF 用 pdfplumber 读文字层 |
| 抽取 | `plugins/model_backends/`、`plugins/extractors/` | llama-server + Qwen3.5，JSON Schema 约束输出 |
| 对齐 | `plugins/aligners/` | 每个值在原文里找出处（行号、坐标），找不到标红 |
| 校验 | `plugins/validators/` | 用数字互相验证（现金 − 总额 = 找零等） |
| 导出 | `plugins/exporters/` | Excel：汇总、明细、来源 |
| 保存 | `pipeline/store.py` | 存进 PostgreSQL：文档、抽取记录、字段值 |

## 后端服务

| 服务 | 作用 | 本机地址 |
| --- | --- | --- |
| PostgreSQL | 数据库：文档、字段、修改记录、审计日志 | `127.0.0.1:5432` |
| Valkey | 任务队列 | `127.0.0.1:6379` |
| SeaweedFS | 对象存储（S3 接口）：上传的原始文件 | `127.0.0.1:8333` |
| llama-server | 本地大模型（`jacara serve` 启动） | `127.0.0.1:8080` |

都只监听本机。配置在 `.env`，由 `backend/src/jacara/core/config.py` 统一读取。

## 数据库

| 表 | 内容 |
| --- | --- |
| `documents` | 上传的文件：文件名、指纹（sha256）、大小、页数 |
| `extractions` | 每一页的一次抽取：文字来源、模型、提示词版本、模型原始回答、规则校验结果 |
| `extracted_values` | 每个字段：值、出处（行号、坐标）、红黄绿状态。写入后不修改 |
| `value_revisions` | 人工修改记录：只新增，不覆盖原值 |

表结构的变化用 Alembic 管理（`backend/migrations/`）。查看数据：`docker compose exec postgres psql -U jacara`。

## 目录

| 目录 | 内容 |
| --- | --- |
| `backend/src/jacara/` | 产品代码：处理流水线、后端业务、命令行 |
| `backend/migrations/` | 数据库迁移 |
| `backend/tests/` | 自动化测试 |
| `web/` | 前端 |
| `deploy/` | 正式部署用的 Docker Compose 与离线安装包 |
| `docs/adr/` | 架构决策记录 |
| `compose.yaml` | 开发用的基础设施（数据库、队列、对象存储） |

## 开发路线

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 环境与骨架 | 工具、目录结构、配置、Git、CI | 完成 |
| 处理流水线 | 读取 → 抽取 → 对齐 → 校验 → 导出 | 完成（命令行版） |
| 后端 | 测试 ✓、基础设施与配置 ✓、数据库 ✓、API、后台任务、审核、权限 | 进行中 |
| 前端 | 文档列表、审核工作台、导出、设置 | |
| 部署 | Docker Compose、离线安装包 | |

## 授权

Apache License 2.0，见 [LICENSE](LICENSE)。
