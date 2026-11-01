# JacarandaParse：给 AI 助手的项目说明

每次开始工作前先读完这份文件。它说明这个项目是什么、代码怎么组织、哪些规则不能破坏。

## 项目是什么

完全离线、本地部署的文档结构化工具：把收据、发票等 PDF 和图片抽取成结构化字段。
**每个值都要能回到原文找到出处**，找不到的标红，交给人工确认。

- 产品名 JacarandaParse；仓库名 JacarandaPharse；Python 包与命令行名 `jacara`

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

    backend/src/jacara/
      core/       公共底座：配置、数据库、权限、审计、插件注册表、对象存储
      plugins/    流水线插件，一个扩展点一个子包：ocr model_backends extractors aligners validators exporters
      pipeline/   流水线编排与后台任务
      modules/    业务功能，一个功能一个子包，内部 schemas → models → repository → service → router
      cli.py      命令行入口
    web/          前端（React + TypeScript + Vite + Ant Design）
    deploy/       Docker Compose、Nginx、离线安装包
    docs/adr/     架构决策记录

**分层规则：** router 不碰数据库，只调用 service；权限检查、审计、状态变化只写在 service 里；调用只能从上往下走。

**做的顺序：** 从里往外。先 plugins（流水线），再 core、pipeline、modules（后端），再 web（前端），最后 deploy。

## 技术栈

Python 3.13（`.python-version`）、uv、FastAPI、Pydantic v2、SQLAlchemy 2.0、Alembic、Celery + Valkey、
PostgreSQL、SeaweedFS（只用 S3 接口）、pypdfium2 + pdfplumber、RapidOCR（运行 PP-OCR 模型）、
llama-server + Qwen3.5、openpyxl。

## 工作方式

- 小步提交：一个任务只改一件事，在分支上做，通过检查后再合并到 main。
- 提交前运行 `uv run ruff check .`、`uv run ruff format --check .`、`uv run pyright`，必须全部通过。
- 自动化测试与评测方案尚未确定，暂不加入；不要自行添加 tests/ 或 eval/。
- 重要的技术选择写一条 ADR（`docs/adr/NNNN-标题.md`），格式参考 0001。
- 不提交 `.env`、数据集、模型文件。密钥只放在 `.env`。
