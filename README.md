# JacarandaParse

完全离线、本地部署的文档结构化工具：把收据、发票等 PDF 和图片抽取成结构化字段，
**每个值都能回到原文找到出处**，找不到出处的值会被标红，交给人工确认。

## 下载和安装（个人版）

个人版装在你自己的电脑上，一个人用，**不用注册、不用登录**，数据只在本机。推荐 Apple 芯片的 Mac
（大模型文件 5.7 GB，内存建议 16 GB 以上）。

1. **先装三样东西**（只要一次）：
   - [Docker Desktop](https://www.docker.com/products/docker-desktop/)：装好后打开它；
   - [uv](https://docs.astral.sh/uv/)：Mac 上 `brew install uv`；
   - llama.cpp（运行大模型）：Mac 上 `brew install llama.cpp`。
2. **下载**：在 GitHub 的 [Releases](../../releases) 里下载最新的 `JacarandaParse-版本.zip`，解压到一个固定的地方
   （例如 `~/JacarandaParse`）。旁边的 `.sha256` 文件可以用来核对：`shasum -a 256 JacarandaParse-版本.zip`。
3. **安装**：打开"终端"，进到解压出来的文件夹，运行

   ```bash
   ./scripts/install.sh
   ```

   它会装好依赖、生成配置（`.env`，里面的密钥是随机的）、启动数据库、建表，然后逐项检查，缺什么告诉你去哪拿。
   第一次要联网（下载依赖和数据库镜像），装好以后用的时候不联网。
4. **下载大模型**：在浏览器里下载
   <https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf>（约 5.7 GB，Apache-2.0），
   放进文件夹里的 `models/`。视觉模型（可选，照片、扫描件认不准时让模型看原图）见下面"第一次准备"。
   放好后可以再运行一次 `uv run --frozen --no-dev jacara setup` 确认都齐了。
5. **启动**：双击 `JacarandaParse.command`，或者在终端里运行 `uv run --frozen --no-dev jacara start`。
   浏览器会自动打开 <http://127.0.0.1:8000>。关掉终端窗口或按 Ctrl + C 就全部停止。
   Mac 提示"无法打开"时，在终端里运行一次 `xattr -d com.apple.quarantine JacarandaParse.command`。

**升级到新版本**：先在"设置 → 数据和备份"里照着做一次备份；下载新版本解压到新文件夹，把旧文件夹里的 `.env`、`models/`、
`data/` 拷过去，再运行 `./scripts/install.sh`（会自动把数据库升级到新版本）。数据库里的数据在 Docker 里，不会丢。

**设置**：网页左边的"设置"里能改识别和处理的选项、换模型、看数据量和各项服务状态。

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

界面有中文和英文两种，右上角的按钮切换（第一次按浏览器的语言）；模板里的字段名用哪种语言另外设置，互不影响。

## 第一次准备（从源码装，开发用）

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

表格结构模型（可选，ADR 0034）：在浏览器里下载
<https://www.modelscope.cn/models/RapidAI/RapidTable/resolve/v2.0.0/slanet-plus.onnx>
（约 7 MB，Apache-2.0），放到 `models/table/slanet-plus.onnx`。SHA-256 应该是
`d57a942af6a2f57d6a4a0372573c696a2379bf5857c45e2ac69993f3b334514b`（`shasum -a 256 models/table/slanet-plus.onnx` 查看）。
没有这个文件时，表格的格子按文字位置划分。版面模型随 rapid-layout 一起安装，不用另外下载。
系统信息页能看到两个模型有没有用上。

视觉模型（可选，ADR 0038）：照片、扫描件上有手写、印章、模糊的地方，OCR 会认错或漏掉，
有了视觉模型就能让大模型看着原图读。在浏览器里下载
<https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/mmproj-F16.gguf>
（约 918 MB，Apache-2.0，和 Qwen3.5-9B 配套），放进 `models/`，重新 `jacara start` 就会一起加载。
默认只在 OCR 不可靠时看图（`.env` 的 `JACARA_VISION`），审核页"重新处理"里也可以选"每张都看"。
系统信息页能看到模型能不能看图。

批量导入（ADR 0036）：文档页可以一次拖进几百个文件，或者“选择整个文件夹”，列表按文件夹筛选。
要自动导入一个文件夹，在 `.env` 里写 `JACARA_WATCH_DIR=文件夹路径`，`jacara start` 会一起启动；
放进去的图片和 PDF 自动导入，不动、不删原文件，内容一样的只导入一次。
也可以单独运行 `uv run jacara watch 文件夹路径`。

## 日常使用：一个命令

```bash
docker compose up -d
uv run jacara start
```

浏览器会自动打开 <http://127.0.0.1:8000>：上传、看处理进度、审核、导出、管理用户。
按一次 **Ctrl + C**（或在别的终端运行 `uv run jacara stop`）全部停止。

`jacara start` 一起启动大模型、后台任务（worker）和 Web 服务，网页由 Web 服务直接提供。

- 模型文件：`models/` 里只有一个 `.gguf` 时自动用它（名字带 mmproj 的视觉模型不算）；有好几个时用 `--model` 或 `.env` 的 `JACARA_MODEL` 指定。
- 已经在运行了再执行一次：不会报错，直接打开浏览器。要重启就先 `jacara stop`。
- 8080 上已经有模型服务（例如另开的 `jacara serve`）：直接用它，不再启动一个。
- 启动前会检查 `.env` 里的密钥不能还是 `change-me`。
- 后台任务默认分两个工位（ADR 0051）：OCR 工位认字、模型工位抽取，模型在抽第 1 份时 OCR 已经在认第 2 份。`.env` 写 `JACARA_PIPELINE=false` 改回一个进程。工作台"每页用时"下面显示每份认字和模型各用了几秒。

## 开发：四个终端

改代码时用这种方式，前端改了马上能看到：

```bash
uv run jacara serve --model models/<模型文件>.gguf  # 1. 本地大模型
uv run jacara worker                               # 2. 后台任务：处理上传的文件（--stage read / extract 只做一个工位）
uv run jacara api --reload                         # 3. Web API，改了后端代码自动重启
cd web && npm run dev                              # 4. 网页开发服务器
```

开发时打开 <http://127.0.0.1:5173>。<http://127.0.0.1:8000/docs> 可以直接在浏览器里试每个接口（离线可用）。
改完前端要给别人用时，运行 `cd web && npm run build`，再重启 `jacara start`。

### 命令行

```bash
uv run jacara ocr receipt.jpg                   # 只识别文字
uv run jacara extract receipt.jpg --excel out.xlsx --save   # 要先启动大模型
uv run jacara extract receipt.jpg --template receipt_full   # 用另一个模板：收据上全部 14 个字段
uv run jacara templates                         # 列出所有模板
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
| POST | `/api/v1/documents/reprocess` | 勾选的几份用同样的设置一起重新处理（`{"ids": [...]}`，可以带模板、分单据、看原图、PDF 读法） |
| GET | `/api/v1/documents/export.json?ids=…` | 多个文档导出成一个 JSON（`jacarandaparse.export.v1`） |
| POST | `/api/v1/documents/{id}/review-time` | 审核页报审核用时（秒） |
| GET | `/api/v1/documents/{id}/extractions/{eid}/sent` | 这一轮发给模型的单据文字原文和文字块编号（ADR 0053） |
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
| GET | `/api/v1/audit-logs` | 审计日志：谁、什么时候、做了什么（管理员；按 `action`、`user_id`、`date_from`、`date_to` 筛，`/audit-logs/export` 导出） |
| GET | `/api/v1/insights/corrections` | 统计：人工修改率、哪种错、错在哪一步、平均审核用时 |
| PATCH | `/api/v1/insights/attributions/{id}` | 改判错在哪一步（`{"stage": "ocr_misread", "note": "…"}`，`null` 撤销） |
| GET | `/api/v1/templates` | 所有模板：字段、中文名、类型、规则 |
| GET | `/api/v1/templates/{name}` | 一个模板 |
| GET / PUT | `/api/v1/ocr-samples/settings` | 自动收集 OCR 纠错样本的开关（改要管理员） |
| GET | `/api/v1/ocr-samples` | OCR 样本列表（按分类、状态、来源筛选） |
| GET | `/api/v1/ocr-samples/export` | OCR 样本导出成 ZIP（PaddleOCR 识别训练格式，按单据分好训练集和验证集；`kind=test` 导出锁定测试集），`/export/preview` 先看会导出几条 |
| GET | `/api/v1/ocr-samples/groups` | 单据分组：每组有哪几张单据、分在哪边（`PUT /groups/{id}/split` 管理员改） |
| GET / POST | `/api/v1/ocr-samples/pairs` | 可能是同一张单据的（PDF 和照片），确认是不是同一张 |
| POST | `/api/v1/ocr-samples/backfill` | 从以前的修改补收样本，进"待复核"（`PATCH /ocr-samples/{id}` 改成 `ready` 是复核通过） |

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

### 评测

在有标准答案的收据（SROIE）上跑整条流水线，算准确率、出处覆盖率、错值拦截率和放行准确率：

```bash
uv run jacara eval download               # 第一次：下载评测集（要联网）
uv run jacara eval sroie --limit 20       # 要先启动大模型
```

指标的意思和历次结果见 [docs/eval/README.md](docs/eval/README.md)。

同一张单据的照片和 PDF 成对比较（ADR 0042）：清单写在 `data/eval/pairs.toml`（格式见 `backend/src/jacara/eval/pairs.py`，data/ 不进 Git）：

```bash
uv run jacara eval pairs                                   # 标准答案当回答，不用模型，只量 OCR 和核对
uv run jacara eval pairs --server http://127.0.0.1:8080    # 真的用模型抽，要先启动大模型
```

回放（ADR 0053）：审核完成的单据用当时的 OCR 快照换一种设置再抽，和人审核过的值比。只读，不改审核数据：

```bash
uv run jacara eval replay --b http://127.0.0.1:8080 --limit 5            # 先少量试跑：A 是当时存下的结果
uv run jacara eval replay --b http://127.0.0.1:8080 --b-learn            # B 带上以前改过的例子
uv run jacara eval replay --a http://127.0.0.1:8080 --a-learn recent --b http://127.0.0.1:8080 --b-learn curated --split test_locked  # 两种挑例子的办法比一比（ADR 0055）
uv run jacara eval replay --b http://127.0.0.1:8080 --split test_locked  # 只用锁定测试集
```

结果在 `data/eval/replay/<时间>/summary.md`。更新之前处理的单据没有快照，默认跳过，加 `--reread` 重新认字。

### 备份和恢复

```bash
jacara stop
uv run jacara backup                                   # 打进 backups/jacara-<时间>.tar
uv run jacara restore-drill backups/jacara-<时间>.tar  # 另起一套空环境实际恢复一次、逐样打开（要 Docker）
```

真要恢复时，先准备一套空的数据库和存储，再 `uv run jacara restore <包>`（只往空环境恢复）。备份包里是你的业务数据，不要放进仓库。

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
| `backend/src/jacara/eval/` | 评测（开发用） |
| `backend/src/jacara/templates/` | 模板：每种单据抽哪些字段、查哪些规则（.toml） |
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
| 部署 | 一个命令跑起来、个人版发布包和安装脚本（完成）；离线安装包 | 进行中 |

## 授权

Apache License 2.0，见 [LICENSE](LICENSE)。
