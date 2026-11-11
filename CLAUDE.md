# JacarandaParse：给 AI 助手的项目说明

每次开始工作前先读完这份文件。它说明这个项目是什么、代码怎么组织、哪些规则不能破坏。

## 项目是什么

完全离线、本地部署的文档结构化工具：把收据、发票等 PDF 和图片抽取成结构化字段。
**每个值都要能回到原文找到出处**，找不到的标红，交给人工确认。

- 产品名 JacarandaParse；仓库名 JacarandaPharse；Python 包与命令行名 `jacara`
- 规划报告、PRD 和设计稿在 claude.ai 的 artifact 里，本仓库 `docs/` 只放 ADR 和开发文档

## 绝对不能破坏的规则

1. **离线**：运行时不访问任何外部网络。不引入需要联网的依赖、遥测或在线 API（包括短信、邮件验证）。
   接口文档页（/docs）的界面文件随包提供，不从 CDN 加载。前端也不从 CDN 加载字体、图标或脚本。
2. **原始值不覆盖**：模型抽出的原始值永远保留，人工修改另存一条修改记录（`value_revisions`）。
3. **审计日志只追加**：不提供修改或删除审计记录的代码路径。
4. **每次抽取都记录来源**：模型名、模型版本、量化方式、提示词版本、模板版本、时间。
5. **每个操作都过权限检查**：service 的每个公开方法先调用 `core/permissions.require()`。个人模式下总是通过，但调用不能省。
   值得追查的操作（登录、上传、审核、导出、管理用户、改密码）还要调用 `modules/audit/service.record()` 记审计日志。
   审计日志里不记密码、token、重设码。
6. **数据带归属**：每条业务数据都有 user 和 team（个人模式用 `PERSONAL_OWNER`）。查询必须按 team 过滤。
7. **插件不碰数据库**：插件只接收数据、返回结果，由 pipeline 负责保存。
8. **前端只调用 `/api/v1`**：不直连数据库或对象存储。
9. **接口只加字段、不删字段**：插件接口和 API 都有版本号。不兼容的改动走新版本（`/api/v2`）。
10. **授权**：项目为 Apache 2.0。不引入 GPL/AGPL 依赖（例如 PyMuPDF）。
11. **只监听本机**：所有服务（API、llama-server、前端开发服务器、Docker 里的数据库等）只绑定 `127.0.0.1`。

## 代码结构

```text
backend/src/jacara/
  core/         公共底座
    config.py       配置（从 .env 读取，pydantic-settings）
    db.py           数据库：Base、混入类、session_scope
    owner.py        当前用户（Owner：user、team、角色、开通的权限；个人模式是 PERSONAL_OWNER）
    permissions.py  权限规则和 require()：管理员全部；使用者 = 基本权限 + 管理员开通的
    security.py     密码哈希（Argon2id）、登录 token、一次性重设码
    storage.py      对象存储（S3 接口，SeaweedFS）
    queue.py        任务队列（Celery + Valkey）：API 只负责把任务放进去
    deps.py         FastAPI 依赖：数据库会话、存储、任务队列、当前用户（团队模式从 token 认人）
    health.py       jacara doctor 的各项检查
  plugins/      流水线插件，一个扩展点一个子包，各有 base.py（接口）和注册表
                ocr model_backends extractors aligners validators exporters tables
  pipeline/     流水线
    run.py          组装插件，一个文件 → 每页的 DocumentReport（命令行和 worker 共用）
    process.py      后台处理一个上传的文档，负责状态变化 uploaded → processing → done / failed
    store.py        把结果存进数据库
    load.py         从数据库读回 DocumentReport（带人工审核结果），给导出器用
  modules/      业务功能，一个功能一个子包
    documents/      schemas → models → repository → service → router
    fields/         字段值与人工审核：修改、确认、修改历史（/api/v1/values）
    auth/           团队、用户、登录会话、重设码（/api/v1/auth、/api/v1/users）
    audit/          审计日志：只追加（/api/v1/audit-logs）
  models.py     汇总所有数据库表（给 Alembic 用）
  app.py        FastAPI 应用：create_app()，挂载 /api/v1
  worker.py     后台 worker：Celery 任务定义，jacara worker 启动
  cli.py        命令行入口
backend/migrations/   Alembic 数据库迁移
backend/tests/        自动测试（pytest）
web/          前端（React + TypeScript + Vite + Ant Design + react-router + TanStack Query）
  vite.config.ts    开发服务器：只监听 127.0.0.1:5173，/api 转发给后端 127.0.0.1:8000
  src/main.tsx      入口：中文、主题色、QueryClient
  src/App.tsx       路由：哪个网址显示哪个页面
  src/api/          调用后端的唯一地方：client.ts（api() 读 JSON、apiBlob() 读图片、apiDownload() 下载文件）、
                    一个功能一个文件、types.ts
  src/auth/         登录：AuthProvider（全站共用"现在是谁"）、useAuth()、RequireAuth（守门员）、登录页、忘记密码页
  src/components/   共用组件：AppLayout（菜单、用户名、修改密码、退出）、ChangePassword、NewPasswordFields
  src/features/     按功能分的页面：documents（列表、上传、导出）、review（审核工作台）、admin（用户管理、审计日志）
  src/utils/        小工具：文件大小、时间格式、saveFile()
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

**页面图片和出处坐标：** 字段的 `evidence.bbox` 是 [左, 上, 右, 下]，单位是 OCR 当时那张图上的像素。
`GET /documents/{id}/pages/{n}/image` 由 `plugins/ocr/loader.page_png()` 生成同一张图：PDF 用同一个 `PDF_SCALE` 渲染，
图片先按 EXIF 方向转正（RapidOCR 识别前也会转正，`rapid.exif_size()` 按转正后的算宽高）。
改 `PDF_SCALE` 或 OCR 前的图片处理时，两边必须一起改，`test_page_image.py` 会检查尺寸。
数据库里没有存页面宽高，前端用图片的实际像素大小当坐标系。

**导出：** 导出器是插件，只认 `DocumentReport`。API 导出时由 `pipeline/load.py` 从数据库拼回来，
`reviews` 里带着每个字段最新的审核结果；格子写审核之后的值，改过的标蓝、批注写原值和原因。
多个文档默认每份一个 Excel、打包成 zip（`export.zip`，重名加 (2)）；也可以汇总到一个 Excel（`export.xlsx`）。
两种导出共用 `_exportable()` 的检查，都记审计日志（detail 里有 format）。

**账号和权限：** `JACARA_MODE=personal`（默认）不用登录；`team` 模式必须登录，请求头带 `Authorization: Bearer <token>`。
只有两种角色：管理员（admin）和使用者（member）。使用者默认能看、能下载、能导出，其余权限（`GRANTABLE`）
由管理员勾选开通，`ADMIN_ONLY` 里的永远只有管理员有。token 和密码都只存哈希；用户只停用、不删除。
第一个管理员用 `jacara user add 用户名 --role admin` 建，之后在网页"用户管理"里建人、开通权限、停用。
**密码只有本人知道**：自己改（`POST /auth/password`，要输原密码）；忘了的话，管理员生成一次性重设码
（`POST /users/{id}/reset-code`，15 分钟、只能用一次、只存哈希），本人凭码自己设新密码（`POST /auth/reset-password`）。
管理员没有"直接设别人密码"的接口；唯一的例外是能操作服务器的人用命令行 `jacara user password`。
不用短信或邮件验证：要联网，违反规则 1（ADR 0021）。

**前端：** 页面不直接写 `fetch`，都通过 `web/src/api/`。后端出错返回 `{"detail": "..."}`，
`client.ts` 统一变成 `ApiError`，页面直接显示它的 message。`types.ts` 和后端的 schemas 字段名保持一样。
登录后 token 存在 localStorage（`api/token.ts`），每个请求自动带上；带着 token 收到 401 就清掉 token、回登录页。
"现在是谁"打开网页时问一次（`AuthProvider`），页面用 `useAuth()` 拿 `me` 和 `can('权限名')`。
个人模式 `/auth/me` 直接成功，不显示登录页。权限变了前端要刷新才更新菜单，但后端每个请求都现查。
页面取数据用 TanStack Query：读用 `useQuery`，改用 `useMutation`，改完 `invalidateQueries` 让相关列表重新取。
文档列表有"排队中/处理中"时每 3 秒刷新一次，全部处理完就停。
审核页的图片要带 token，所以用 `apiBlob()` 取回来再变成 `blob:` 地址，不能直接 `<img src="/api/...">`。
出处框用盖在图片上的 SVG 画，`viewBox` 设成图片的真实像素，`bbox` 拿来直接用。
选中和悬停的字段放在 `ReviewPage`，左边 `PageViewer` 和右边 `FieldPanel` 都读它，所以两边联动。
审核：选中字段展开 `ReviewForm`，"值是对的"发 confirm，"保存修改"发 edit（原因必填、原文没有发 null）；
规则全在后端，前端只显示后端的错误原话。`useRevise()` 成功后刷新这个文档、文档列表和修改历史。
下载（导出）同样要带 token：`apiDownload()` 取回文件和 Content-Disposition 里的文件名，`utils/download.saveFile()` 保存。

**做的顺序：** 从里往外。先 plugins（流水线），再 core、pipeline、modules（后端），再 web（前端），最后 deploy。

## 技术栈

Python 3.13（`.python-version`）、uv、FastAPI + fastapi-offline + uvicorn、Pydantic v2、SQLAlchemy 2、Alembic、
Celery + Valkey、argon2-cffi（密码哈希）、PostgreSQL 18、SeaweedFS（只用 S3 接口，boto3）、pypdfium2 + pdfplumber、
RapidOCR（运行 PP-OCR 模型）、llama-server + Qwen3.5-9B（Q4_K_M）、openpyxl、pytest。
前端：Node.js 26、npm、Vite、React、TypeScript、Ant Design、react-router、TanStack Query、oxlint。

## 工作方式

- 小步提交：一个任务只改一件事，在分支上做，开 PR，CI 通过后再合并到 main。
- 提交前运行下面"检查"里的整条命令，必须全部通过；改了前端还要跑前端检查。
- 新功能要带测试：放在 `backend/tests/`，共用的假数据写在 `conftest.py`。
  测试不依赖 Docker 和模型：数据库用内存 SQLite，对象存储、任务队列、OCR、模型都用假的
  （`MemoryStorage`、`FakeQueue` 在 conftest.py），接口测试用 `dependency_overrides` 替换依赖。
  测试默认是个人模式（conftest 的 `personal_mode`），不受自己 `.env` 影响；要测团队模式的文件自己切过去。
- 评测方案（准确率打分）尚未确定，暂不加入；不要自行添加 eval/。
- 改了数据库表就生成一条迁移，检查后再提交；需要改已有数据时在迁移里手写 SQL。
- 重要的技术选择写一条 ADR（`docs/adr/NNNN-标题.md`），格式参考 0002。
- 不提交 `.env`、数据集、模型文件、`node_modules/`、`web/dist/`。密钥只放在 `.env`。要提交 `web/package-lock.json`。
- 前端调用后端只通过 `web/src/api/`，页面里不直接写 `fetch`。按钮显不显示看 `/auth/me` 的 `permissions`，但真正的权限检查在后端。
- **npm 命令只在 `web/` 里运行。** 根目录不应该有 `package.json`、`package-lock.json`、`node_modules/`，
  否则网页里会有两份 React（白屏、`Invalid hook call`）。`web/` 里只放前端文件，不要有 `__init__.py`。

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

# 运行完整服务：docker compose 之外，再开四个终端
uv run jacara serve --model <模型文件>.gguf          # 终端 1：本地大模型
uv run jacara worker                                 # 终端 2：后台任务，处理上传的文件
uv run jacara api --reload                           # 终端 3：API，打开 http://127.0.0.1:8000/docs
cd web && npm run dev                                # 终端 4：前端，打开 http://127.0.0.1:5173

# 团队模式：.env 里 JACARA_MODE=team，然后建第一个管理员（密码会提示输入）
uv run jacara user add admin --role admin
uv run jacara user add xiaowang --allow document:create   # 新建使用者并开通"上传文件"
uv run jacara user password admin                          # 唯一的管理员也忘了密码时（能操作服务器的人用）

# 前端（在 web/ 文件夹里运行；后端要先启动）
cd web && npm install                     # 第一次，或者 package.json 变了之后
npm install 包名                          # 加新包：一定要在 web/ 里
npm run dev -- --force                    # 装了新包后页面异常：清掉 Vite 缓存再启动
npm run lint && npm run build             # 前端检查（提交前）

# 不用 API，直接在命令行处理一个文件
uv run jacara ocr <图片或PDF>                        # 只做文字识别
uv run jacara extract <图片或PDF> --excel out.xlsx --save   # 抽取字段，导出并存库

# 检查（提交前，在根目录）
uv run ruff format . && uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
uv run pytest -vv -k upload               # 只跑名字里带 upload 的测试，并显示每个测试名
```
