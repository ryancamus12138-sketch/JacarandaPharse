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
  → 网页上人工审核：原图上框出出处，修改或确认
  → 导出 Excel / 存入数据库 / 通过 API 提供给前端
```

全程不联网：OCR 模型随包提供，大模型由本机的 llama-server 运行。

## 第一次准备

需要 [uv](https://docs.astral.sh/uv/)、[Docker Desktop](https://www.docker.com/products/docker-desktop/)、[Node.js](https://nodejs.org)（LTS）
和 llama.cpp（Mac：`brew install llama.cpp`）。Python 由 uv 自动下载。

```bash
uv sync                      # 创建虚拟环境并安装依赖
cp .env.example .env         # 复制配置，把 change-me 都改成随机值（openssl rand -hex 16）
docker compose up -d         # 启动 PostgreSQL、Valkey、SeaweedFS
uv run alembic upgrade head  # 建表
cd web && npm install && npm run build && cd ..   # 打包网页（只在 web/ 里运行 npm）
uv run jacara doctor         # 检查 .env、网页和各项服务
```

模型文件（`.gguf`）放在 `models/` 里，不进 Git。

## 日常使用：一个命令

```bash
docker compose up -d
uv run jacara start --model models/Qwen3.5-9B-Q4_K_M.gguf
```

打开 <http://127.0.0.1:8000>：上传、看处理进度、审核、导出、管理用户。按一次 **Ctrl + C** 全部停止。

`jacara start` 一起启动大模型、后台任务（worker）和 Web 服务，网页由 Web 服务直接提供。
启动前会检查：`.env` 里的密钥不能还是 `change-me`，8000、8080 端口不能被占用。
不写 `--model` 时只启动 worker 和 Web 服务，大模型要自己另外启动。

## 开发：四个终端

改代码时用这种方式，前端改了马上能看到：

```bash
uv run jacara serve --model models/<模型文件>.gguf  # 1. 本地大模型
uv run jacara worker                               # 2. 后台任务：处理上传的文件
uv run jacara api --reload                         # 3. Web API，改了后端代码自动重启
cd web && npm run dev                              # 4. 网页开发服务器
```

开发时打开 <http://127.0.0.1:5173>。<http://127.0.0.1:8000/docs> 可以直接在浏览器里试每个接口（离线可用）。
改完前端要给别人用时，运行 `cd web && npm run build`，再重启 `jacara start`。

### 命令行

```bash
uv run jacara ocr receipt.jpg                   # 只识别文字
uv run jacara extract receipt.jpg --excel out.xlsx --save   # 要先启动大模型
```

### 接口

上传后接口马上返回，文件在后台处理。状态变化：
`uploaded`（排队中）→ `processing`（处理中）→ `done`（待审核）或 `failed`（失败，`error` 里写着原因）。
处理完后人工审核：修改或确认标黄的字段（原值保留，每次审核都有记录），全部处理完再"完成审核"，变成 `reviewed`。

接口都在 `/api/v1` 下面，其他网址都是网页。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/api/v1/auth/login` | 登录（团队模式），返回 token |
| POST | `/api/v1/auth/logout` | 退出登录 |
| GET | `/api/v1/auth/me` | 当前是谁、能做哪些操作 |
| POST | `/api/v1/auth/password` | 修改自己的密码（要输原密码；别的设备上的登录退出） |
| POST | `/api/v1/auth/reset-password` | 忘记密码：用户名 + 重设码 + 新密码（不用登录） |
| GET | `/api/v1/health` | 服务是否在运行 |
| POST | `/api/v1/documents` | 上传文件（图片或 PDF，默认上限 50 MB） |
| GET | `/api/v1/documents` | 文档列表（分页：`limit`、`offset`） |
| GET | `/api/v1/documents/{id}` | 文档详情：每页的抽取结果、字段值和出处 |
| GET | `/api/v1/documents/{id}/file` | 下载原始文件 |
| GET | `/api/v1/documents/{id}/pages/{n}/image` | 第 n 页的图片（PNG），出处框的坐标就是这张图上的像素 |
| POST | `/api/v1/documents/{id}/process` | 重新处理（失败后重试） |
| POST | `/api/v1/documents/{id}/review` | 完成审核（所有标黄的字段都处理过之后） |
| POST | `/api/v1/values/{id}/revisions` | 审核一个字段：修改（edit）或确认（confirm） |
| GET | `/api/v1/values/{id}/revisions` | 一个字段的修改历史 |
| GET | `/api/v1/documents/{id}/export.xlsx` | 导出一个文档的 Excel（审核之后的值） |
| GET | `/api/v1/documents/export.zip?ids=…&ids=…` | 多个文档每个一个 Excel，打包成 zip（解压后是一个文件夹） |
| GET | `/api/v1/documents/export.xlsx?ids=…&ids=…` | 多个文档汇总到一个 Excel，一行一张单据 |
| GET | `/api/v1/users` | 用户列表（管理员） |
| POST | `/api/v1/users` | 新建用户并开通权限（管理员） |
| PATCH | `/api/v1/users/{id}` | 改权限、停用（管理员） |
| POST | `/api/v1/users/{id}/reset-code` | 给忘了密码的人生成一次性重设码（管理员；15 分钟有效，只能用一次） |
| GET | `/api/v1/users/permissions` | 可以开通的权限 |
| GET | `/api/v1/audit-logs` | 审计日志：谁、什么时候、做了什么（管理员） |

给别人用时，可以在 `.env` 里写 `JACARA_API_DOCS=false` 关掉 `/docs`。

### 个人模式和团队模式

默认是个人模式（`.env` 里 `JACARA_MODE=personal`）：不用登录，一个人用。

几个人一起用时改成 `JACARA_MODE=team`，重启，再建第一个管理员：

```bash
uv run jacara user add admin --role admin
```

团队模式下每个人用自己的账号登录。管理员能做所有事；使用者默认能看、能下载、能导出，
上传、重新处理、修改字段、完成审核由管理员在网页"用户管理"里按人勾选开通，改了马上生效。

**密码只有本人知道**：登录后自己改；忘了的话，管理员生成一次性重设码当面告诉本人，本人用它自己设新密码。
登录、上传、审核、导出、用户管理、改密码都会记进审计日志。

### 检查与测试

```bash
uv run ruff format . && uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
cd web && npm run lint && npm run build
```

后端测试不需要 Docker、模型和 worker，几秒钟跑完。推到 GitHub 后 CI 会自动跑同样的检查。

## 目录

| 目录 | 内容 |
| --- | --- |
| `backend/src/jacara/plugins/` | 流水线插件：OCR、模型后端、抽取、对齐、校验、导出 |
| `backend/src/jacara/core/` | 配置、数据库、权限、登录 token、对象存储、提供网页 |
| `backend/src/jacara/modules/` | 业务功能（文档、字段、账号、审计日志），每个功能五层 |
| `backend/src/jacara/pipeline/` | 流水线组装、后台处理、保存结果 |
| `backend/src/jacara/app.py` | Web 服务入口：接口 + 网页 |
| `backend/src/jacara/worker.py` | 后台任务入口 |
| `backend/migrations/` | 数据库迁移 |
| `backend/tests/` | 自动测试 |
| `web/` | 前端（React + TypeScript），见 [web/README.md](web/README.md) |
| `deploy/` | 部署 |
| `docs/adr/` | 架构决策记录 |

## 开发路线

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| 环境与骨架 | 工具、目录结构、配置、Git、CI | 完成 |
| 处理流水线 | PDF/图片读取 → OCR → 字段抽取 → 证据对齐 → 规则校验 → 导出 | 完成 |
| 后端 | 基础设施、数据库、文档 API、后台任务、人工审核、导出 API、账号与权限 | 完成 |
| 前端 | 登录、文档列表、审核工作台、导出、用户管理、审计日志 | 完成 |
| 部署 | 一个命令跑起来（完成）、Docker Compose、离线安装包 | 进行中 |

## 授权

Apache License 2.0，见 [LICENSE](LICENSE)。
