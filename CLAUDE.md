# JacarandaParse：给 AI 助手的项目说明

每次开始工作前先读完这份文件。它说明这个项目是什么、代码怎么组织、哪些规则不能破坏。

## 项目是什么

完全离线、本地部署的文档结构化工具：把收据、发票等 PDF 和图片抽取成结构化字段。
**每个值都要能回到原文找到出处**，找不到的标红，交给人工确认。

- 产品名 JacarandaParse；仓库名 JacarandaPharse；Python 包与命令行名 `jacara`
- 规划报告、PRD 和设计稿在 claude.ai 的 artifact 里，本仓库 `docs/` 只放 ADR 和开发文档

## 绝对不能破坏的规则

1. **离线**：运行时不访问任何外部网络。不引入需要联网的依赖、遥测或在线 API。
   接口文档页（/docs）的界面文件随包提供，不从 CDN 加载。
2. **原始值不覆盖**：模型抽出的原始值永远保留，人工修改另存一条修改记录（`value_revisions`）。
3. **审计日志只追加**：不提供修改或删除审计记录的代码路径。
4. **每次抽取都记录来源**：模型名、模型版本、量化方式、提示词版本、模板版本、时间。
5. **每个操作都过权限检查**：service 的每个公开方法先调用 `core/permissions.require()`。个人模式下总是通过，但调用不能省。
6. **数据带归属**：每条业务数据都有 user 和 team（个人模式用 `PERSONAL_OWNER`）。查询必须按 team 过滤。
7. **插件不碰数据库**：插件只接收数据、返回结果，由 pipeline 负责保存。
8. **前端只调用 `/api/v1`**：不直连数据库或对象存储。
9. **接口只加字段、不删字段**：插件接口和 API 都有版本号。不兼容的改动走新版本（`/api/v2`）。
10. **授权**：项目为 Apache 2.0。不引入 GPL/AGPL 依赖（例如 PyMuPDF）。
11. **只监听本机**：所有服务（API、llama-server、Docker 里的数据库等）只绑定 `127.0.0.1`。

## 代码结构

```text
backend/src/jacara/
  core/         公共底座
    config.py       配置（从 .env 读取，pydantic-settings）
    db.py           数据库：Base、混入类、session_scope
    owner.py        数据归属（Owner、PERSONAL_OWNER）
    permissions.py  权限检查 require()
    storage.py      对象存储（S3 接口，SeaweedFS）
    queue.py        任务队列（Celery + Valkey）：API 只负责把任务放进去
    deps.py         FastAPI 依赖：数据库会话、存储、任务队列、当前用户
    health.py       jacara doctor 的各项检查
  plugins/      流水线插件，一个扩展点一个子包，各有 base.py（接口）和注册表
                ocr model_backends extractors aligners validators exporters tables
  pipeline/     流水线
    run.py          组装插件，一个文件 → 每页的 DocumentReport（命令行和 worker 共用）
    process.py      后台处理一个上传的文档，负责状态变化 uploaded → processing → done / failed
    store.py        把结果存进数据库
  modules/      业务功能，一个功能一个子包
    documents/      schemas → models → repository → service → router
    fields/         字段值与人工审核：修改、确认、修改历史（/api/v1/values）
  models.py     汇总所有数据库表（给 Alembic 用）
  app.py        FastAPI 应用：create_app()，挂载 /api/v1
  worker.py     后台 worker：Celery 任务定义，jacara worker 启动
  cli.py        命令行入口
backend/migrations/   Alembic 数据库迁移
backend/tests/        自动测试（pytest）
web/          前端（React + TypeScript + Vite + Ant Design）
deploy/       部署相关
docs/adr/     架构决策记录
compose.yaml  开发用基础设施：PostgreSQL、Valkey、SeaweedFS
```

### 业务模块的五层

| 层 | 文件 | 负责 | 不能做 |
| --- | --- | --- | --- |
| 数据格式 | `schemas.py` | 接口收发的 JSON 长什么样（Pydantic） | 不放内部字段（如 storage_key） |
| 表 | `models.py` | 数据库表（SQLAlchemy） | 不写业务逻辑 |
| 数据访问 | `repository.py` | 查询和写入，按 team 过滤 | 不检查权限、不处理 HTTP |
| 业务 | `service.py` | 权限检查、校验、状态变化、调用存储 | 不认识 HTTP 状态码 |
| 接口 | `router.py` | 收请求、调 service、把错误翻译成状态码 | 不碰数据库 |

调用只能从上往下走：router → service → repository → models。
service 抛出带 `code` 的业务错误（如 `DocumentError("not_found", ...)`），router 用一张表把 code 翻译成 HTTP 状态码。

**写入顺序：** 先存文件到对象存储，成功后再写数据库。存储失败就不留数据库记录。
**放进队列之前先提交：** worker 是另一个进程，用自己的连接查数据库，没提交的数据它看不到。
队列连不上不算上传失败，文档停在 uploaded，可以调用"重新处理"。

**后台任务：** 慢的事（OCR、大模型）不在 API 请求里做，放进队列交给 worker。
任务只传文档 id，worker 自己去数据库和对象存储取。任务要能重复执行而不出错（按状态认领，不是 uploaded 就跳过）。

**人工审核：** `extracted_values` 写入后不改。修改或确认都新增一条 `value_revisions`（seq 1、2、3……），
当前值取最新一条（`current_value`），原值永远在 `value`。界面显示 `current_value`，标黄看 `pending_review`。
所有需要确认的字段都处理完，调用"完成审核"，文档 done → reviewed；之后再改字段，文档回到 done。

**做的顺序：** 从里往外。先 plugins（流水线），再 core、pipeline、modules（后端），再 web（前端），最后 deploy。

## 技术栈

Python 3.13（`.python-version`）、uv、FastAPI + fastapi-offline + uvicorn、Pydantic v2、SQLAlchemy 2、Alembic、
Celery + Valkey、PostgreSQL 18、SeaweedFS（只用 S3 接口，boto3）、pypdfium2 + pdfplumber、
RapidOCR（运行 PP-OCR 模型）、llama-server + Qwen3.5-9B（Q4_K_M）、openpyxl、pytest。

## 工作方式

- 小步提交：一个任务只改一件事，在分支上做，开 PR，CI 通过后再合并到 main。
- 提交前运行下面"检查"里的整条命令，必须全部通过。
- 新功能要带测试：放在 `backend/tests/`，共用的假数据写在 `conftest.py`。
  测试不依赖 Docker 和模型：数据库用内存 SQLite，对象存储、任务队列、OCR、模型都用假的
  （`MemoryStorage`、`FakeQueue` 在 conftest.py），接口测试用 `dependency_overrides` 替换依赖。
- 评测方案（准确率打分）尚未确定，暂不加入；不要自行添加 eval/。
- 改了数据库表就生成一条迁移，检查后再提交；需要改已有数据时在迁移里手写 SQL。
- 重要的技术选择写一条 ADR（`docs/adr/NNNN-标题.md`），格式参考 0002。
- 不提交 `.env`、数据集、模型文件。密钥只放在 `.env`。

## 常用命令

```bash
# 环境
uv sync                                   # 安装依赖
cp .env.example .env                      # 第一次：复制配置后修改密码
docker compose up -d                      # 启动数据库、任务队列、对象存储
uv run jacara doctor                      # 检查各项服务是否连得上

# 数据库迁移
uv run alembic upgrade head               # 把数据库升级到最新
uv run alembic revision --autogenerate -m "说明"   # 改了表之后生成迁移
uv run alembic check                      # 确认表和迁移一致
uv run alembic current                    # 当前数据库在哪个版本

# 运行完整服务：docker compose 之外，再开三个终端
uv run jacara serve --model <模型文件>.gguf          # 终端 1：本地大模型
uv run jacara worker                                 # 终端 2：后台任务，处理上传的文件
uv run jacara api --reload                           # 终端 3：API，打开 http://127.0.0.1:8000/docs

# 不用 API，直接在命令行处理一个文件
uv run jacara ocr <图片或PDF>                        # 只做文字识别
uv run jacara extract <图片或PDF> --excel out.xlsx --save   # 抽取字段，导出并存库

# 检查（提交前）
uv run ruff format . && uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
uv run pytest -vv -k upload               # 只跑名字里带 upload 的测试，并显示每个测试名
```
