# JacarandaParse：给 AI 助手的项目说明

每次开始工作前先读完这份文件。它说明这个项目是什么、代码怎么组织、哪些规则不能破坏。

## 项目是什么

完全离线、本地部署的文档结构化工具：把收据、发票等 PDF 和图片抽取成结构化字段。
**每个值都要能回到原文找到出处**，找不到的标红，交给人工确认。

- 产品名 JacarandaParse；仓库名 JacarandaPharse；Python 包与命令行名 `jacara`
- 规划报告、PRD 和设计稿在 claude.ai 的 artifact 里，本仓库 `docs/` 只放 ADR 和开发文档

## 绝对不能破坏的规则

1. **离线**：运行时不访问任何外部网络。不引入需要联网的依赖、遥测或在线 API。
2. **原始值不覆盖**：模型抽出的原始值永远保留，人工修改另存一条修改记录。
3. **审计日志只追加**：不提供修改或删除审计记录的代码路径。
4. **每次抽取都记录来源**：模型名、模型版本、量化方式、提示词版本、模板版本、时间。
5. **每个操作都过权限检查**：个人模式下检查总是通过，但调用不能省。
6. **数据带归属**：每条业务数据都有 user 和 team（个人模式用默认团队）。
7. **插件不碰数据库**：插件只接收数据、返回结果，由 pipeline 负责保存。
8. **前端只调用 `/api/v1`**：不直连数据库或对象存储。
9. **接口只加字段、不删字段**：插件接口和 API 都有版本号。
10. **授权**：项目为 Apache 2.0。不引入 GPL/AGPL 依赖（例如 PyMuPDF）。

## 代码结构

```text
backend/src/jacara/
  core/       公共底座：配置（config.py）、健康检查（health.py）、数据库、权限、审计、对象存储
  plugins/    流水线插件，一个扩展点一个子包：ocr tables model_backends extractors aligners validators exporters
  pipeline/   流水线编排与后台任务
  modules/    业务功能，一个功能一个子包，内部 schemas → models → repository → service → router
  cli.py      命令行入口
backend/tests/  自动化测试（pytest）
web/            前端（React + TypeScript + Vite + Ant Design）
deploy/         正式部署：Docker Compose、Nginx、离线安装包
docs/adr/       架构决策记录
compose.yaml    开发用基础设施：PostgreSQL、Valkey、SeaweedFS
```

**处理流水线：** 文件 → 读取（`ocr/loader.py`：图片 OCR，电子 PDF 读文字层）→ 视觉行（`ocr/layout.py`）
→ 抽取（`extractors`，经 `model_backends` 调本地模型）→ 证据对齐（`aligners`）→ 规则校验（`validators`）
→ 导出（`exporters`）。各步之间只通过 `OcrPage`、`ExtractionResult`、`AlignedField`、`ValidationResult`、
`DocumentReport` 这些数据类型交接。

**配置：** 只在 `core/config.py` 的 `Settings` 里读配置（环境变量 > `.env` > 默认值），
其他地方用 `get_settings()`，不要直接读 `os.environ`。密码类字段用 `SecretStr`，不要打印或写进日志。

**分层规则：** router 不碰数据库，只调用 service；权限检查、审计、状态变化只写在 service 里；调用只能从上往下走。

**做的顺序：** 从里往外。先 plugins（流水线），再 core、pipeline、modules（后端），再 web（前端），最后 deploy。

## 技术栈

Python 3.13（`.python-version`）、uv、FastAPI、Pydantic v2、pydantic-settings、SQLAlchemy 2.0、Alembic、
Celery + Valkey、PostgreSQL（psycopg）、SeaweedFS（只用 S3 接口，boto3）、pypdfium2 + pdfplumber、
RapidOCR（运行 PP-OCR 模型）、llama-server + Qwen3.5、openpyxl、pytest。
开发环境用 Docker Desktop 运行数据库等服务。

## 工作方式

- 小步提交：一个任务只改一件事，在分支上做，通过检查后再合并到 main。
- 提交前运行 `uv run ruff check .`、`uv run ruff format --check .`、`uv run pyright`、`uv run pytest`，必须全部通过。
- 自动化测试用 pytest，放在 `backend/tests/`。改代码时同时加或改测试，`uv run pytest` 必须通过。
  测试不连外部服务：模型用假后端，OCR 用手写的 OcrPage，文件写到 `tmp_path`，配置测试不读真实的 `.env`。
- 评测（用数据集算准确率）尚未确定，暂不加入；不要自行添加 eval/。
- 重要的技术选择写一条 ADR（`docs/adr/NNNN-标题.md`），格式参考 0001。
- 不提交 `.env`、数据集、模型文件。密钥只放在 `.env`；新增配置项时同时更新 `.env.example`（只写假值）。

## 常用命令

```bash
uv sync                    # 安装依赖
uv run ruff check .        # 代码检查
uv run ruff format .       # 自动整理格式
uv run pyright             # 类型检查
uv run pytest              # 自动化测试

docker compose up -d       # 启动数据库、任务队列、对象存储（配置在 .env）
docker compose ps          # 看服务状态
docker compose down        # 停止（数据保留）

uv run jacara --version
uv run jacara doctor                                     # 检查所有服务是否连得上
uv run jacara ocr 文件                                   # 识别图片或 PDF 里的文字
uv run jacara serve --model models/Qwen3.5-9B-Q4_K_M.gguf  # 启动本地模型
uv run jacara extract 文件 --fields full --excel results/结果.xlsx
```
