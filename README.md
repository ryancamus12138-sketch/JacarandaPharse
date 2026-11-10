# JacarandaParse

完全离线、本地部署的文档结构化工具：把收据、发票等 PDF 和图片抽取成结构化字段，
**每个值都能回到原文找到出处**，找不到出处的值会被标红，交给人工确认。

## 它怎么工作

```text
图片 / PDF
  → 读取（PDF 有文字层直接读，没有就渲染成图片）
  → OCR 识别文字，按位置分行
  → 本地大模型抽取字段（每个值附带行号）
  → 证据对齐：在原文里找到每个值，记下坐标
  → 规则校验：总额为正、现金 − 总额 = 找零……
  → 导出 Excel / 存入数据库 / 通过 API 提供给前端
```

全程不联网：OCR 模型随包提供，大模型由本机的 llama-server 运行。

## 快速开始（开发）

需要 [uv](https://docs.astral.sh/uv/) 和 [Docker Desktop](https://www.docker.com/products/docker-desktop/)。Python 由 uv 自动下载。

```bash
uv sync                      # 创建虚拟环境并安装依赖
cp .env.example .env         # 复制配置，把 change-me 改成随机值（openssl rand -hex 16）
docker compose up -d         # 启动 PostgreSQL、Valkey、SeaweedFS
uv run alembic upgrade head  # 建表
uv run jacara doctor         # 检查各项服务
```

### 命令行

```bash
uv run jacara serve --model <模型文件>.gguf     # 启动本地大模型（单独一个终端）
uv run jacara ocr receipt.jpg                   # 只识别文字
uv run jacara extract receipt.jpg --excel out.xlsx --save
```

### Web API 和后台任务

在 `docker compose up -d` 之外，开三个终端：

```bash
uv run jacara serve --model <模型文件>.gguf     # 1. 本地大模型
uv run jacara worker                            # 2. 后台任务：处理上传的文件
uv run jacara api --reload                      # 3. Web API
```

打开 <http://127.0.0.1:8000/docs> 可以直接在浏览器里试每个接口（离线可用）。

上传后接口马上返回，文件在后台处理。状态变化：
`uploaded`（排队中）→ `processing`（处理中）→ `done`（完成）或 `failed`（失败，`error` 里写着原因）。
处理完后人工审核：修改或确认标黄的字段（原值保留，每次审核都有记录），全部处理完再"完成审核"，变成 `reviewed`。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/v1/health` | 服务是否在运行 |
| POST | `/api/v1/documents` | 上传文件（图片或 PDF，默认上限 50 MB） |
| GET | `/api/v1/documents` | 文档列表（分页：`limit`、`offset`） |
| GET | `/api/v1/documents/{id}` | 文档详情：每页的抽取结果、字段值和出处 |
| GET | `/api/v1/documents/{id}/pages/{n}/image` | 第 n 页的图片（PNG），出处框的坐标就是这张图上的像素 |
| POST | `/api/v1/documents/{id}/process` | 重新处理（失败后重试） |
| POST | `/api/v1/documents/{id}/review` | 完成审核（所有标黄的字段都处理过之后） |
| POST | `/api/v1/values/{id}/revisions` | 审核一个字段：修改（edit）或确认（confirm） |
| GET | `/api/v1/values/{id}/revisions` | 一个字段的修改历史 |

### 检查与测试

```bash
uv run ruff format . && uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
```

测试不需要 Docker、模型和 worker，几秒钟跑完。推到 GitHub 后 CI 会自动跑同样的检查。

## 目录

| 目录 | 内容 |
| --- | --- |
| `backend/src/jacara/plugins/` | 流水线插件：OCR、模型后端、抽取、对齐、校验、导出 |
| `backend/src/jacara/core/` | 配置、数据库、权限、对象存储 |
| `backend/src/jacara/modules/` | 业务功能（文档、字段……），每个功能五层 |
| `backend/src/jacara/pipeline/` | 流水线组装、后台处理、保存结果 |
| `backend/src/jacara/app.py` | Web API 入口 |
| `backend/src/jacara/worker.py` | 后台任务入口 |
| `backend/migrations/` | 数据库迁移 |
| `backend/tests/` | 自动测试 |
| `web/` | 前端 |
| `deploy/` | 部署 |
| `docs/adr/` | 架构决策记录 |

## 开发路线

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 环境与骨架 | 工具、目录结构、配置、Git、CI | 完成 |
| 处理流水线 | PDF/图片读取 → OCR → 字段抽取 → 证据对齐 → 规则校验 → 导出 | 完成 |
| 后端 | 基础设施、数据库、文档 API、后台任务、人工审核 | 完成 |
| 后端 | 权限与账号、导出 API | 进行中 |
| 前端 | 文档列表、审核工作台、导出、设置 | |
| 部署 | Docker Compose、离线安装包 | |

## 授权

Apache License 2.0，见 [LICENSE](LICENSE)。
