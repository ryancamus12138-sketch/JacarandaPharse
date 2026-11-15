# 0068 桌面个人版：Tauri 外壳、内嵌数据库与队列、未签名发布

状态：已采纳（2026-11）

部分取代：ADR 0064 中"发布包 = 源码 + 网页"的做法（源码不再随发布包公开，见第 9 节）。ADR 0064 的 Docker 版保留，
供开发和以后的团队版使用。

## 背景

ADR 0064 的个人版是一个 zip：源码、打包好的网页、`JacarandaParse.command`。用户要自己安装 Docker（PostgreSQL、
Valkey、SeaweedFS）、uv 和 llama.cpp，并在终端执行 `jacara setup`、`jacara start`。这对普通用户门槛过高。

本 ADR 的目标：用户下载一个安装包（macOS `.dmg`、Windows `.exe`），双击安装，双击打开，不需要命令行，也不需要
安装任何其他软件。

已确定的前提（与作者讨论后确定）：

1. **只做桌面版，不做网页版。** 公开网址的在线服务需要访客之间的数据隔离、带 GPU 的服务器，并且要保管他人上传
   的单据，成本和责任都不适合当前阶段。
2. **源码只放在私有仓库。** 公开仓库只放说明、截图、演示视频和安装包。
3. **第一版不做代码签名。** 在公开仓库的说明里写清楚 macOS 和 Windows 上的放行步骤（第 8 节）。
4. **许可沿用 Apache 2.0，增加 NOTICE 文件署名。** 署名为笔名 Ryan Camus（第 9 节）。
5. **Windows 按自动检测使用显卡（Vulkan）或 CPU**（3.4 节）。
6. **模型在程序内下载**，用户少一步。为此修改规则 1（第 6 节"规则 1 的修改"）。
7. **允许在程序内检查和安装新版本**，由用户点击触发（第 13 节）。

## 决定一览

| 项目 | 决定 | 依据 |
|---|---|---|
| 代码组织 | 同一份代码，新增桌面运行方式；不建新仓库 | ADR 0063；GitLab 合并 CE/EE 仓库的经验 |
| 外壳 | Tauri 2 | 系统自带 WebView、最小程序不足 600 KB；官方支持随应用附带 Python 服务程序 |
| Python 打包 | PyInstaller，单文件夹（onedir）模式 | 单文件模式每次启动需解压且产生两个进程 |
| 数据库 | SQLite（WAL 模式） | 数据表已兼容 SQLite；Gitea 等成熟项目同时支持 SQLite 与 PostgreSQL |
| 文件存储 | 本地文件夹 | Gitea `STORAGE_TYPE=local` |
| 任务队列 | Huey（MIT），SQLite 存储 | 支持 SQLite 存储、线程工作者、重试 |
| 大模型服务 | 随安装包附带 llama.cpp 官方构建的 `llama-server` | MIT 许可；官方发布 macOS arm64、Windows CPU/Vulkan 构建 |
| 数据位置 | 系统的本地应用数据文件夹下的 `JacarandaParse`（不用标识符命名）；可在设置中把数据文件夹移到其他磁盘（4.1 节） | macOS `~/Library/Application Support/JacarandaParse`；Windows `%LOCALAPPDATA%\JacarandaParse`；以程序名命名同 Electron `userData` 的默认做法；移动参照 Docker Desktop 的"Disk image location"和 Zotero 的"Data Directory Location" |
| 本机安全 | 随机端口 + 每次启动的随机令牌 + Host 检查 + Origin 检查 | Jupyter 令牌机制；MCP 规范防 DNS 重绑定要求；OWASP |
| 支持平台 | macOS（Apple 芯片）、Windows 10/11 x64 | GitHub 已停用 Intel Mac 标准运行器；Python 已将 Intel Mac 降为二级支持 |
| 安装包 | macOS `.dmg`；Windows NSIS `-setup.exe`（按用户安装，无需管理员） | Tauri 安装包文档 |
| 签名 | macOS 仅做 ad-hoc 签名；Windows 不签名 | Apple 芯片要求程序至少有签名；第 8 节 |
| 模型文件 | 不放进安装包；首次启动向导中由用户点击"下载"，程序从 Hugging Face 下载并校验 | GitHub Releases 单个文件必须小于 2 GiB，模型约 5.68 GB；下载做法参照 huggingface_hub（固定提交、按 SHA-256 校验） |

## 1. 代码组织

- **不建新仓库，不另开文件夹。** 沿用 ADR 0063：个人版和团队版是同一份代码。桌面版是"另一种运行方式"，不是
  另一套代码。开发在新分支（如 `feature/desktop`）上进行，合并回主线后照常发布。
- 新增目录：
  - `desktop/`：Tauri 工程（`src-tauri/` 下的 Rust 代码和 `tauri.conf.json`）。
  - `packaging/desktop/`：PyInstaller 的构建配置（`.spec`）、llama.cpp 版本与校验值清单、许可清单生成脚本。
- **运行方式由三个设置分别决定**，参照 Gitea 的 `DB_TYPE`、`STORAGE_TYPE`、`[queue] TYPE` 三项配置：

  | 设置 | 可选值 | 默认值（服务器方式） | 桌面版 |
  |---|---|---|---|
  | `JACARA_DATABASE` | `postgres`、`sqlite` | `postgres` | `sqlite` |
  | `JACARA_STORAGE` | `s3`、`local` | `s3` | `local` |
  | `JACARA_QUEUE` | `celery`、`huey` | `celery` | `huey` |
  | `JACARA_HOME` | 数据根目录 | 项目根目录 | 第 4 节的数据目录 |

  三项分开而不合并为一个"桌面模式"开关，好处是测试可以逐项组合，开发时也能单独试用 SQLite。
- 桌面版固定为个人模式（`JACARA_MODE=personal`）。团队版仍按 Docker 方式部署，不做桌面版。

## 2. 进程结构

```
JacarandaParse（Tauri 主程序，Rust）
 └─ jacara-backend desktop          ← PyInstaller 打包的 Python 程序；负责启动、监视、关闭以下进程
     ├─ 网页和 API（uvicorn，在本进程内运行）
     ├─ jacara-backend worker --stage read      ← OCR 工位（Huey 消费者）
     ├─ jacara-backend worker --stage extract   ← 模型工位（Huey 消费者）
     ├─ jacara-backend watch                    ← 监视文件夹（设置了才启动，与 `jacara start` 相同）
     └─ llama-server                             ← 大模型服务
```

与现在 `jacara start` 的结构一致（API 在主进程，两个工位和模型服务是子进程），只是由 Tauri 而不是终端启动。

**启动顺序**

1. Tauri 用单实例插件保证只运行一个程序；再次双击时聚焦已有窗口。
2. Tauri 启动 `jacara-backend desktop`，通过环境变量传入数据目录（`JACARA_HOME`）、日志目录（`JACARA_LOG_DIR`）、
   网页目录（`JACARA_WEB_DIR`），并设置 `JACARA_WATCH_STDIN=1`：后端只在设置了它时才在标准输入关闭后退出
   （从终端手动运行时标准输入是终端，不受影响）。
3. 后端在 `127.0.0.1` 上选取空闲端口（向系统申请端口 0），生成本次启动的随机令牌，执行数据库迁移，
   将中断的文档标记为失败（沿用 `reset_interrupted`），启动两个工位、模型服务和（设置了时）监视文件夹，
   并把所有"排队中"的文档重新放进队列（Huey 取出任务后才执行，进程崩溃时手上的任务会丢失；重复的任务在领取时跳过）。
   之后由后端监管子进程：自己退出的子进程自动重新启动，并把退出原因写进它的日志；启动后 1 秒内就退出算启动失败，
   依次等 1、2、3 秒重试，连续失败超过 3 次不再启动（supervisord 的 `autorestart`、`startsecs`、`startretries`）。
   OCR 工位重新启动后，再把"排队中"的文档放回队列一次。
4. 后端就绪后，在标准输出打印一行 `JACARA_READY http://127.0.0.1:<端口>/desktop/enter?token=<令牌>`。
5. Tauri 读到这一行后，让窗口打开该地址（第 5 节）。启动期间窗口显示"正在启动"页面。

**关闭顺序和防止残留进程**

- 用户关闭窗口时，Tauri 通知后端退出；后端先停止接收新任务，再依次结束子进程，与现在 `jacara stop` 的做法相同。
- 父进程意外退出时，子进程也必须结束：
  - **后端监视 Tauri**：Tauri 持有后端的标准输入管道；Tauri 退出后管道关闭，后端读到结束即自行退出。
    两个工位以同样方式监视后端。
  - **Windows**：后端把所有子进程放进一个设置了 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 的作业对象。按微软文档，
    最后一个作业句柄关闭时，系统会结束作业中的全部进程；用 `CreateProcess` 创建的子进程默认也在作业中。
    这样即使后端被强制结束，`llama-server` 也不会残留。
  - **macOS**：后端把子进程编号写入数据目录下的 `.run/desktop.json`；下次启动时先清理上次残留的进程（只结束命令行
    仍与记录相符的进程，避免误伤复用了同一编号的其他程序）。
- 打包后 `sys.executable` 指向打包程序本身，不再是 Python 解释器。现在用 `[sys.executable, "-m", "jacara.cli", ...]`
  启动子进程的地方，改为调用打包程序的子命令（`jacara-backend worker ...`）。

## 3. 内嵌部件

### 3.1 数据库：SQLite

- 文件：`<数据目录>/jacara.db`。
- 连接设置：
  - `journal_mode=WAL`：读和写互不阻塞，适合 API 与两个工位同时访问。SQLite 文档说明 WAL 不能用于网络文件系统，
    所以数据目录必须在本机磁盘上（第 4 节的默认位置满足这一点）。
  - `foreign_keys=ON`：SQLite 默认不检查外键，需要在每次连接时打开（SQLAlchemy 文档给出的做法是在连接事件里执行该
    PRAGMA）。
  - 等待锁的时间设为 30 秒（`sqlite3.connect` 的 `timeout` 参数，默认 5 秒），避免工位写入时 API 立即报"数据库被锁"。
- 数据表定义已经兼容 SQLite（JSON 列在 PostgreSQL 上用 JSONB，在 SQLite 上用 JSON；测试一直用 SQLite）。
  模糊搜索用的 `ilike` 在 SQLite 上由 SQLAlchemy 转换为等价写法。
- **迁移**：现有 24 个迁移只在 PostgreSQL 上执行过。SQLite 不支持大部分 `ALTER TABLE`，按 Alembic 文档在
  `migrations/env.py` 中对 SQLite 启用批量模式（`render_as_batch`，即"建新表—复制数据—替换旧表"）。在 PostgreSQL 上
  批量模式直接执行原来的 ALTER 语句，已有数据库不受影响。新增测试：在空 SQLite 数据库上从头执行全部迁移，再与
  模型定义比对。

### 3.2 文件存储：本地文件夹

- 新增 `LocalStorage`，实现现有的 `ObjectStorage` 接口（`put`、`get`、`delete`），备份所需的 `keys`、`content_type`
  同样实现。
- 存储键映射为 `<数据目录>/files/<键>`；含 `..` 或绝对路径的键一律拒绝。
- 写入时先写临时文件，再用 `os.replace` 替换（Python 文档：同一文件系统上的重命名是原子操作），断电时不会留下写了
  一半的文件。
- `content_type` 由文件扩展名推断（`mimetypes`），推断不出时为 `application/octet-stream`。文档的内容类型本来就记
  在数据库 `documents.content_type` 中，存储层的类型只在备份时使用。

### 3.3 任务队列：Huey

- 桌面版不使用 Celery 和 Valkey，改用 Huey（MIT 许可）的 SQLite 存储，文件为 `<数据目录>/queue.db`
  （与业务数据库分开，避免互相等待锁）。
- 实现现有的 `TaskQueue` 接口（`enqueue_document`、`enqueue_eval`，以及工位之间的 `enqueue_extract`）。业务代码不变。
- 两个工位对应两个队列（与现在的 `pipeline`、`extract` 队列同名）。每个工位进程运行一个 Huey 消费者，
  线程工作者、数量为 1，相当于现在的 `--pool=solo --concurrency=1`（本机模型一次只处理一个请求；onnxruntime
  在 macOS 上的 fork 子进程中容易崩溃）。
- 任务在 `queue.db` 中持久保存，程序重启后继续处理；处理到一半被中断的文档，按现有规则标记为失败、可重新处理。
- 服务器方式继续使用 Celery，不受影响。

### 3.4 大模型服务：llama.cpp

- 随安装包附带 llama.cpp 官方发布的构建（MIT 许可）：
  - macOS：`llama-<版本>-bin-macos-arm64.tar.gz`（Metal 加速）。
  - Windows：`llama-<版本>-bin-win-cpu-x64.zip` 和 `llama-<版本>-bin-win-vulkan-x64.zip`。
- 版本固定，在 `packaging/desktop/llama.lock` 中记录版本号和每个压缩包的大小和 SHA-256；构建时
  `packaging/desktop/fetch_llama.py` 下载并校验，不一致即失败。现在固定为 b12359，即 llama.cpp v1.0.0
  （v1.0.0 发布页的 `nightly-tag.txt` 写的就是 b12359）。只保留 `llama-server` 用到的文件：macOS 按 `@rpath` 加载命令
  逐个找出它加载的动态库（版本链接展开为文件）；Windows 保留 `llama-server.exe` 和除其他工具专用库（`*-impl.dll`）
  外的全部 DLL，因为 ggml 运行时按名字加载 CPU 和显卡后端（`ggml-cpu-*.dll`、`ggml-vulkan.dll`）。
- macOS 构建要求 macOS 13.3；onnxruntime 的 wheel 要求 macOS 14.0，所以安装包的最低系统版本是 14.0（7.2 节）。
- **Windows 选择 CPU 还是 GPU**：设置 `jacara_llama_device`（自动 / CPU / 显卡（Vulkan），默认自动）。自动时运行
  Vulkan 版的 `llama-server --list-devices`（官方参数："列出可用设备后退出"），输出里有 `VulkanN: …` 设备行就用
  Vulkan 版，只有 `(none)` 或运行出错、30 秒内没结束就用 CPU 版。检测结果按构建（文件夹名、大小、修改时间）记在
  `data/llama-devices.json`，同一个构建只检测一次，更新程序后重新检测。"模型"页的"计算设备"只在安装包里有不止一个
  构建时显示（即只在 Windows 上），说明文字里写检测到的显卡。不附带 CUDA 版（需要额外的 CUDA 运行库，体积大）。
  **此方案需在有独立显卡和无独立显卡的 Windows 实机上各验证一次。**
- 保留现有保护措施：每次启动生成随机 API 密钥，通过环境变量 `LLAMA_API_KEY` 传给 `llama-server`；只监听
  `127.0.0.1`；关闭自带网页（`--no-webui`）；macOS 上设置 `GGML_METAL_BF16_DISABLE=1`。
- `server_command` 查找 `llama-server` 的顺序：先看设置 `JACARA_LLAMA_SERVER`，再用安装包内附带的构建
  （`plugins/model_backends/llama_builds.py` 按上一条选择），最后在系统路径中查找。

### 3.5 OCR、版面和表格模型

- RapidOCR 自带的 PP-OCRv6 检测、识别模型和方向分类模型，随 Python 包一起打包。
- 版面模型：只打包 `rapid_layout/models/layout_cdla.onnx`。`rapid-layout` 也提供 YOLOv8 版面模型，其权重是 AGPL 许可
  （见 `structure/engines.py` 的说明），违反规则 10。**构建脚本检查打包结果中 `rapid_layout` 目录只有
  `layout_cdla.onnx`，出现其他权重文件即构建失败。**
- 表格模型 SLANet-plus（Apache 2.0，约 7 MB）：现在要求用户自己下载。桌面版改为随安装包附带：构建时
  `packaging/desktop/fetch_table_model.py` 按 README 中已有的地址、大小和 SHA-256 下载并校验，spec 再校验一次。
  桌面版只用程序资源目录（PyInstaller 的 `sys._MEIPASS`）中附带的那份或用户导入的版本，不再读数据目录里散放的
  `models/table/slanet-plus.onnx`（这类文件在放入文件夹列表中可以导入，6 节）；「设置 → 模型」里显示为
  「SLANet-plus（PaddleOCR 模型，程序内置，7.4 MB）」。

### 3.6 网页

- `web/dist` 作为 Tauri 资源打包，后端通过 `JACARA_WEB_DIR` 找到它。

## 4. 数据目录

- 根目录是系统的本地应用数据文件夹下的 `JacarandaParse`（Tauri 的 `localDataDir` + 程序名），用户看到的是程序名，
  不是标识符：
  - macOS：`~/Library/Application Support/JacarandaParse/`
  - Windows：`%LOCALAPPDATA%\JacarandaParse\`
  这与 Electron 的默认做法相同：`userData` 是"`appData` 目录加上程序名"，日志在 macOS 的 `~/Library/Logs/<程序名>`；
  VS Code（`Code`）、Slack 等桌面程序的数据文件夹都是程序名。Windows 用 Local 而不是 Roaming：数据库、文件和模型
  有几个 GB，不应随漫游配置同步。
- 日志：macOS `~/Library/Logs/JacarandaParse/`，Windows `%LOCALAPPDATA%\JacarandaParse\logs\`。
  Tauri 自带的 `appLocalDataDir` / `appLogDir` 以标识符命名，因此不用；外壳算出上面的路径，经 `JACARA_HOME`、
  `JACARA_LOG_DIR` 传给后台。
- **标识符**：Tauri 要求反向域名格式，只能含字母、数字、连字符和点，且在所有应用中唯一（用作系统里的 bundle ID 和
  网页内核数据文件夹的路径）；Apple 也要求 bundle ID 用反向域名格式。它不再决定数据文件夹的名字，但仍出现在
  网页内核的缓存文件夹（macOS `~/Library/WebKit/<标识符>/`、`~/Library/Caches/<标识符>/`，Windows WebView2 的
  `EBWebView` 文件夹）和系统的程序信息里。**标识符发布后不能再改**：改了系统会当成另一个程序，网页内核里保存的
  数据（例如界面语言选择）就找不到了。定为 `io.github.ryancamus12138-sketch.jacarandaparse`（"已确定"第 2 条）。
- 目录内容：

  | 路径 | 内容 |
  |---|---|
  | `jacara.db` | 业务数据库 |
  | `queue.db` | 任务队列 |
  | `files/` | 上传的原始文件、OCR 样本、导出文件等（原对象存储中的内容） |
  | `models/` | 模型库和 `selected.json`（原项目下的 `models/`） |
  | `data/settings.json` | 设置页保存的设置（与项目下的位置相同） |
  | `backups/` | 手动完整备份（`jacara-<时间>.tar`） |
  | `backups/auto/` | 自动备份（`jacara-<时间>.db`，只含数据库，13.5 节） |
  | `before-restore-<时间>/` | 恢复备份前移开的原数据，程序不删除（第 6 节"备份和恢复"） |
  | `data-location.json` | 只在起始文件夹中：数据文件夹被移走后记录新位置（4.1 节） |
  | `.run/desktop.json` | 运行中的进程编号 |
  | `logs/` | 各进程的日志（桌面版由 `JACARA_LOG_DIR` 改到上面的日志文件夹） |

- 现在 `core/config.py` 中有 12 处、其他文件中有 13 处用 `PROJECT_ROOT` 拼接路径。其中运行数据（模型、设置、`.run`）
  的路径改为用 `JACARA_HOME`。
  `PROJECT_ROOT` 只保留给随程序分发的只读文件。服务器方式下 `JACARA_HOME` 默认等于项目根目录，行为不变。
- 卸载程序不删除数据目录。README 写明数据目录位置，以及需要彻底删除时如何手动删除。

### 4.1 移动数据文件夹

磁盘空间不足时，用户可以在「设置 → 数据和备份 → 更改位置…」把数据文件夹移到其他磁盘，也可以「改回默认位置」。
参照 Docker Desktop 设置中的"Disk image location"（可以把磁盘映像移到别处；目标已有映像时询问）和 Zotero 的
"Data Directory Location"（自定义位置记在设置里，程序启动时据此打开数据）。

- **起始文件夹不变**：Tauri 传入的 `JACARA_HOME`（第 4 节的 `JacarandaParse` 文件夹）始终是起始文件夹。移动后在其中写
  `data-location.json`（`{"path": "<新位置>"}`），启动程序读到它就把 `JACARA_HOME` 换成新位置，起始文件夹本身记在
  `JACARA_DEFAULT_HOME`。改回默认位置时删除这个文件。
- **目标**：所选文件夹中新建 `JacarandaParse` 文件夹，它必须不存在或为空。不能是当前数据文件夹本身、它里面的文件夹或
  包含它的文件夹；可用空间至少是数据大小的 1.05 倍。不建议放在网络位置或网盘同步文件夹：SQLite 的 WAL 模式要求所有
  进程在同一台计算机上，不能用于网络文件系统（SQLite 文档；Zotero 对数据目录有同样的提醒），界面上说明这一点。
- **步骤**：停止后台进程并关闭数据库 → 把 WAL 合并进数据库（`PRAGMA wal_checkpoint(TRUNCATE)`）→ 逐个复制文件和
  文件夹（包括空文件夹），复制时计算 SHA-256，写完后重新读一遍核对 → 全部一致后才写 `data-location.json`、切换
  `JACARA_HOME`，再删除原位置的数据 → 重新启动后台。复制或核对失败时删除不完整的副本，数据留在原处，什么都不改。
  进程记录 `.run/desktop.json` 在停止后台时删除，不会留在原位置。
- **找不到数据**：启动时 `data-location.json` 指向的位置没有数据库（移动硬盘没接上，或同一盘符换成了另一块磁盘），
  **不会在那里新建空的数据库**。服务照常启动，但除健康检查、作业状态和"数据位置"三个接口外一律返回 503，网页显示
  「找不到数据文件夹」，提供「重试」（接上硬盘后）和「改用默认位置」（打开起始文件夹中的数据，原来的文件夹不删除）。

## 5. 本机安全

**问题**：个人模式不需要登录。任何本机程序，以及用户浏览器里打开的任何网页，都能向 `127.0.0.1` 上的端口发请求。
攻击者还可以利用 DNS 重绑定，让恶意网页读到本机服务的返回内容。MCP 规范对本机 HTTP 服务明确要求：必须校验
`Origin` 头以防 DNS 重绑定、只绑定 `127.0.0.1`、应当有身份验证。

**措施**（四项同时采用）：

1. **随机端口**：每次启动由系统分配空闲端口，不再固定为 8000。
2. **每次启动的随机令牌**（参照 Jupyter Server：启动时生成令牌，首次访问时通过网址传入，之后改用 Cookie）：
   - 后端用 `secrets.token_urlsafe(32)` 生成令牌，只通过标准输出交给 Tauri，不写入文件。
   - Tauri 打开 `/desktop/enter?token=<令牌>`；后端核对后设置 `HttpOnly`、`SameSite=Strict` 的 Cookie，再跳转到首页。
   - 之后每个 `/api` 请求都必须带这个 Cookie，否则返回 401。
3. **Host 检查**：只接受 `Host` 为 `127.0.0.1:<端口>` 或 `localhost:<端口>` 的请求（Starlette `TrustedHostMiddleware`），
   DNS 重绑定的请求带的是攻击者的域名，会被拒绝。
4. **跨站请求检查**：对 POST、PUT、PATCH、DELETE 请求，按 OWASP 建议检查 `Sec-Fetch-Site`，缺失时检查 `Origin`；
   来源不是本机地址的一律拒绝。

第 3、4 项对现有的服务器个人版同样有效（现在没有任何 Host 或 Origin 检查），建议一并启用。

## 6. 界面改动

| 位置 | 改动 |
|---|---|
| 设置 → 个人 | 个人模式下隐藏"账号"卡片（用户名、团队、角色、权限、修改密码），只保留头像和昵称 |
| 设置 → 数据和备份 | 去掉命令行步骤，改为"立即备份"（可选保存位置、可选包含模型文件）、备份列表和"恢复"、"从其他备份文件恢复…"、"打开备份文件夹"；"数据文件夹"卡片显示位置和大小，有"打开数据文件夹""更改位置…""改回默认位置"（4.1 节）；"自动备份"卡片（13.5 节）（见下） |
| 设置 → 识别和处理 | 监视文件夹可以设多个，每个单独选模板；桌面版用系统对话框选择文件夹（见下） |
| 设置 → 识别和处理、模型 | 桌面版有"重新启动后台"按钮，不必退出程序就能让新设置和新模型生效（见下） |
| 设置 → 模型 | 首次启动向导（见下）；Windows 增加"计算设备：自动 / CPU / 显卡（Vulkan）" |
| 设置 → 关于和诊断 | 显示版本、署名（NOTICE 内容）、许可、第三方许可清单、数据目录和日志目录，"打开数据文件夹"按钮（见下） |
| 全站提示 | 18 个文件中约 40 处提到 `jacara start`、`docker compose`、`brew install` 等命令。桌面版改为界面操作的说法，例如"重新启动 JacarandaParse 后生效" |

**关于和诊断**

- "关于"卡片：版本、署名（`NOTICE` 原文）、许可（Apache-2.0）、"许可证全文""署名""第三方许可"三个链接（打开程序包内的
  `LICENSE`、`NOTICE`、`THIRD_PARTY_LICENSES`，`GET /api/v1/system/licenses/{license|notice|third_party}`；这个版本
  没有附带的文件显示"这个版本没有附带 …"），以及"复制诊断信息"按钮。
- 诊断信息参照 Obsidian 的"Show debug info → Copy to clipboard"：纯文本，分为系统、模型、后台进程、检查、数据几段，
  键名用英文（反馈问题时不受界面语言影响）；包括版本、系统和内存、所选模型文件和大小、`llama-server --version`、
  各后台进程的状态和启动次数、`jacara doctor` 的检查结果。不含单据内容和文件名，用户主目录写成 `~`。
  只有管理员能取（`GET /api/v1/system/diagnostics`，个人版即本机用户）。复制用浏览器剪贴板接口，不可用时退回到
  选中文本框再复制。
- "文件夹"卡片：数据文件夹、备份文件夹、日志文件夹（macOS `~/Library/Logs/JacarandaParse`，Windows
  `<数据文件夹>\logs`），各有"打开"按钮。
- "后台进程"卡片：模型服务、识别、抽取、监视文件夹各一行，状态为运行中 / 正在重新启动 / 已暂停 / 已停止，
  重新启动过的显示次数和最近一次原因。"服务状态"卡片是原来系统信息页的检查结果。

**备份和恢复**

- 沿用 ADR 0054 的备份格式（数据库各表的 JSON 行 + 全部文件，打包为 `.tar`，与数据库种类无关），所以桌面版的备份
  和服务器版的备份可以互相恢复。清单 `manifest.json` 增加可选的 `extras`：`extra/settings.json`（设置页的设置）、
  `extra/selected.json`（使用中的模型选择）、`extra/models.json`（使用中的模型清单：路径、字节数、SHA-256），勾选
  "包含模型文件"时还有 `extra/models/...`。每项记 SHA-256，核对备份包时一并核对；旧版本读取时忽略 `extras`。
- **作业**：备份、恢复、移动数据文件夹、重新启动后台都作为"作业"在服务进程的后台线程中进行，同一时间只有一个。
  网页每 0.5 秒读 `GET /api/v1/desktop/job` 显示进度，作业期间不能关闭对话框。作业期间：备份和重新启动只拒绝修改
  （可以浏览）；恢复和移动拒绝全部请求。被拒绝的请求返回 503 和 `Retry-After`，网页在此期间刷新时显示"正在进行数据
  操作"并在完成后自动打开。
- **备份**：暂停两个工位和监视进程（模型服务不停），写入所选位置（默认 `<数据目录>/backups/`），核对备份包，再恢复。
  同一秒内的两次备份不会互相覆盖（第二个文件名加 `-2`）。
- **恢复**：现有规则是"只能恢复到空环境"。桌面版先停止后台，把当前的 `jacara.db`、`queue.db`、`files/`、设置和模型
  选择移到 `<数据目录>/before-restore-<时间>/`（不删除），在空环境中恢复，再逐项核对；任何一步失败都删除恢复了一半的
  数据并移回原数据，然后重新启动后台。备份中没有、本机也没有的模型列出来，提示重新下载或导入。
  恢复自动备份（`.db`，只含数据库）时文件保持原样；备份之后删除的文件在核对中列为提醒，不算恢复失败。
  比本程序新的版本做的备份不恢复，提示先安装新版本。
- "打开数据文件夹""打开备份文件夹"由后端调用系统命令完成（macOS `open`，Windows `os.startfile`），不需要 Tauri 的插件。
- **选择文件夹和文件**：网页拿不到本机路径，桌面版由后端打开系统对话框：macOS 用 `osascript` 的 `choose folder` /
  `choose file`，Windows 用 PowerShell 的 `FolderBrowserDialog` / `OpenFileDialog`（置顶窗口作为所有者，避免对话框
  藏在程序窗口后面），Linux 用 zenity。没有对话框程序时提示直接输入完整路径，每处都可以手动输入。

**多个监视文件夹**

- 设置 `JACARA_WATCH_FOLDERS`：列表，每项是 `{"path": 完整路径, "template": 模板}`，模板的选项与上传时相同
  （自动判断、我的模板、抽取所有键值对）。保存时检查：文件夹存在、是完整路径、不重复、不互相包含；模板存在且未归档。
- 以前的单个文件夹 `JACARA_WATCH_DIR` / `JACARA_WATCH_TEMPLATE` 仍然读取（.env 或设置页），作为列表的一项；在页面上
  保存列表后改用列表，旧的两项从设置页文件中删除。
- 一个监视进程监视全部文件夹（每个文件夹一个 `Watcher`，每 5 秒扫描一次）；"关于"页每个文件夹一项检查。

**重新启动后台**

- 后台进程（两个工位、监视进程、模型服务）只在启动时读取设置和模型。桌面版的设置页和模型页在需要时显示"重新启动
  后台"：停止全部子进程，清除服务进程中缓存的数据库连接、队列、OCR 和版面模型，把处理到一半的文档重新排队，再按当前
  设置启动。不需要退出程序。
- 只由服务进程自己读取的设置（自动备份的开关和份数）每次使用时重新读取，保存后立即生效，不提示重启（设置项的 `live`）。

**模型为什么不放进安装包**

- GitHub Releases 规定每个文件必须小于 2 GiB。大模型约 5.68 GB、视觉模型约 0.92 GB，无法作为一个安装包上传；
  拆成多个文件又失去"双击安装"的意义。
- 每次发布新版本，用户都要重新下载 6 GB 以上，而模型通常没有变化。
- 模型有自己的许可和版本，用户也可能已经下载过，或想换用模型库中的其他模型。

**首次启动向导和模型下载**

检测不到大模型时显示向导，提供三种方式：

1. **在程序内下载（默认）**：列出要下载的文件、大小和许可，用户点"下载"后开始。下载来源自动选择、失败时自动切换（见下），
   用户不需要选择。
2. **放入文件夹**：向导显示"模型收件"文件夹 `<数据目录>/models/inbox/` 和需要的文件名，提供"打开文件夹"按钮。
   用户用浏览器或其他工具从任一来源下载，把文件放进去，程序检测到后自动校验、合并、导入并继续（见下）。
3. **选择已下载的文件**：用系统对话框选择（或输入路径），复制一份进模型库（原文件保留）。清单中的文件按清单校验；
   不在清单中的 `.gguf` 文件作为自定义模型导入（文件名含 mmproj 的作为视觉模型）。

向导另有"从备份恢复"的入口（换电脑时用），转到「设置 → 数据和备份」。向导在检测不到大模型时自动打开一次
（每次打开程序最多一次，可以"稍后再说"）；之后工作台显示提示和"准备模型"按钮，「设置 → 模型」有"下载推荐模型"。
三种方式导入的文件都放进模型库并设为"使用中"；全部完成后，向导提供"启动模型并开始使用"（重新启动后台，见"重新启动后台"）。

下载的文件清单（`backend/src/jacara/downloads/models.lock`，TOML，随程序发布、打包时检查格式，不在运行时获取）：

| 文件 | 大小 | 许可 | 是否默认勾选 |
|---|---|---|---|
| `Qwen3.5-9B-Q4_K_M.gguf`（大模型） | 5.68 GB | Apache 2.0 | 是 |
| `mmproj-F16.gguf`（视觉模型，照片和扫描件 OCR 不可靠时让模型看原图） | 0.92 GB | Apache 2.0 | 是 |

每个文件记录：字节数、完整文件的 SHA-256，以及各下载来源的地址。已核对（2026-11-14）：作者电脑上的两个文件与
Hugging Face 上的文件 SHA-256 一致：

| 文件 | 字节数 | SHA-256 |
|---|---|---|
| `Qwen3.5-9B-Q4_K_M.gguf` | 5,680,522,464 | `03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8` |
| `mmproj-F16.gguf` | 918,166,080 | `f70dc3509053962b0d0d3ee8a7eacebf5d60aa560cad78254ae8698516ae029f` |

**下载来源**（三个来源都提供完整的单个文件，不需要拆分）：

| 来源 | 地址格式 | 核对结果（2026-11-14） |
|---|---|---|
| Hugging Face（unsloth 官方仓库） | `https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/<提交编号>/<文件名>` | 两个文件 SHA-256 一致 |
| hf-mirror.com（社区维护的 Hugging Face 镜像） | `https://hf-mirror.com/unsloth/Qwen3.5-9B-GGUF/resolve/<提交编号>/<文件名>` | 视觉模型：重定向后返回 200，`content-length` 918,166,080，`x-linked-etag` 等于 SHA-256 |
| 魔搭社区 ModelScope（unsloth 仓库） | `https://www.modelscope.cn/models/unsloth/Qwen3.5-9B-GGUF/resolve/<提交编号>/<文件名>` | 接口返回的两个文件 SHA-256 一致。接口中的 `Size`（5,837,251,808 和 921,705,024）与实际不同，但实际下载时视觉模型的 `content-range` 总长为 918,166,080，与实际一致；支持 `Range`（返回 206） |

- 固定的提交编号（2026-11-15 核对）：Hugging Face 和 hf-mirror.com 用各文件最后一次修改的提交——大模型
  `99a1b2185534379e6e8b5ec869da25d3e7b3f73c`，视觉模型 `f0a5ec311e0b365f00a15084f64a43bbd21af890`（Hugging Face 文件页）；
  魔搭用 `ae90f0d1c1be2b9250b0ef68265615f6fe3c777b`（魔搭接口 `repo/files` 的 `CommitId`，两个文件的 `Size`、`Sha256`
  与清单一致；该仓库只有 `master` 分支、没有标签）。六个固定地址各请求前 8 个字节：都先重定向到各自的文件存储
  服务器，最后返回 206，`content-range` 总长分别为 5,680,522,464 和 918,166,080，文件头是 `GGUF`、版本 3。
  从澳大利亚访问时，hf-mirror.com 以 308 把请求转到 huggingface.co，此时它不是独立的来源；在中国大陆它才起镜像的作用。
- 使用**固定的提交编号**而不是 `main` / `master`，避免仓库更新后下到不同的文件。这与 huggingface_hub 文档的建议一致；
  该文档还说明大文件的 ETag 就是它的 SHA-256。
- 清单中的字节数和 SHA-256 以作者核对过的文件为准，**不采用各来源接口返回的元数据**（魔搭接口的 `Size` 就与实际不符）。
  下载时按响应的 `content-length` 和下载完成后的 SHA-256 判断，任何一项与清单不符都视为失败。
- 魔搭对 `HEAD` 请求返回 200 但不带 `content-length`，所以下载程序不用 `HEAD` 探测文件，一律用带 `Range` 的 `GET`
  请求，从 `content-range` 中读取总长。
- hf-mirror.com 不是 Hugging Face 官方服务，可用性不受保证；因为有 SHA-256 校验，它只可能"下载失败"，不可能"下载到错误
  的文件"。
- 魔搭社区由阿里巴巴运营，Qwen 模型的官方发布渠道之一；在中国大陆访问通常比 Hugging Face 顺畅。
- 因为按 SHA-256 校验，无论从哪个来源下载，得到的都是同一个文件。以后增加来源，只需在清单中加地址。
- 不在本项目的 GitHub 发布页转存模型：单个文件须小于 2 GiB，大模型必须拆分；实测 gzip、xz 压缩只能缩小 1%–2%
  （模型参数已量化为约 4 位，数据接近随机），无法避免拆分。

**自动选择和切换来源**

参照 APT 的镜像下载方式（`apt-transport-mirror`）：按优先级依次尝试；一个镜像下载失败时，自动换列表中的下一个，
直到成功或全部失败，用户不需要介入。

- **优先级**：Hugging Face（官方仓库）→ 魔搭社区 → hf-mirror.com（社区镜像，放在最后）。
- **判定失败**：连接失败、HTTP 错误，或连续 30 秒几乎收不到数据（参照 curl 的 `--speed-time`：只设 `--speed-limit`
  时默认 30 秒；速度下限按 curl 的默认 1 字节/秒）。三种情况都立即换下一个来源。
- **切换时不从头下载**：三个来源的文件逐字节相同（SHA-256 一致、字节数一致），已下载的部分直接保留，向下一个来源用
  `Range` 请求剩余部分。下载完成后校验整个文件的 SHA-256；万一不一致，删除后从当前来源重新下载一次，仍不一致则报错。
- **记住结果**：成功的来源记入设置，下次（例如下载视觉模型、换新推荐模型）优先使用。在中国大陆，第一次 Hugging Face
  连接失败后会自动换到魔搭社区，之后都直接用魔搭社区。
- **界面**：进度条上方显示当前来源，例如"正在从魔搭社区下载（Hugging Face 连接失败，已自动切换）"。全部来源都失败时，
  显示每个来源的失败原因，并提供"重试"和"放入文件夹"两个选项。
- **手动指定**：向导的"高级"中可以固定使用某一个来源，供自动选择不理想时使用；默认是"自动"。

**只有一个模型文件夹**

桌面版的模型只在 `<数据目录>/models/` 里，各部分分工固定：

| 位置 | 用途 |
|---|---|
| `models/hub/<名称>/v<n>/` | 模型库。程序运行的模型**只从这里来**，由 `models/selected.json` 指定 |
| `models/inbox/` | 唯一的放入口：用户放进来的文件和文件夹 |
| `models/downloads/` | 程序自己用的临时文件（`.part`、复制中的 `.copy`、`state.json`） |
| `models/selected.json` | 正在使用的版本（指向 `models/hub/` 中的文件） |

桌面版数据文件夹的 `models/` 中只有上面这几项（`hub/` 下另有导入时用的临时文件夹 `.incoming/`）。程序自带的模型
不在数据文件夹中：表格结构模型 SLANet-plus 在程序资源目录（安装包内；从源码运行时是项目文件夹的
`models/table/`），OCR 检测和识别模型在 RapidOCR 包内。旧版本的位置（`models/*.gguf`、`models/ocr/`、
`models/table/`）中的文件，在放入文件夹列表中标为"不在放入文件夹中，程序不会使用"，可以导入或删除（界面不提"旧版本""桌面版"，用户只看到一个程序）。

- 桌面版不再直接运行 `models/` 根目录里散放的 `.gguf`（`jacara start` 的老规则，命令行版保留）：这类文件出现在
  放入文件夹的列表里，点"导入"后进模型库。
- `JACARA_MODEL`、`JACARA_MMPROJ` 环境变量仍然优先（供开发使用），指向的文件不存在时向导显示警告。从访达或
  开始菜单启动的正式版不会继承终端里设置的变量。
- 「设置 → 模型」的下拉列表只有**默认**模型（随程序附带的表格模型、RapidOCR 自带的 OCR 模型）和**已导入**的版本。
  已导入的版本与默认模型是同一个文件（SHA-256 相同）时只列一次（作为默认），除非正选着它。
  旧版本放在 `models/`、`models/ocr/`、`models/table/` 的文件不再自动使用，在放入文件夹列表中可以导入；
  导入的正好是正在使用的文件时，导入后改用导入的版本，选择不会落空。
- 界面用语：不再说"模型库""数据集库"，改为"已导入的模型""数据集"；下拉选项只写名称、版本和大小；
  默认项写模型名、作者和由谁内置，例如"PP-OCRv6_det_small（PaddleOCR 模型，RapidOCR 3.10.0 内置）"
  "SLANet-plus（PaddleOCR 模型，程序内置）"，识别字典写"识别模型内嵌字符表"；来源列写"默认""手动选择"。
  用"内置"而不用"分发"：VS Code 把随程序提供的扩展称为 built-in（"extensions that come with VS Code"，其中也有
  他人编写的），Positron 称为 Included extensions；"分发"（redistribution）是许可证的法律用语，归属说明放在
  关于页和许可证清单中（第 9 节）。写明"PaddleOCR 模型"，避免被当成本程序自己的模型。
- 放入文件夹列表每行可以删除（链接只删链接；分片模型删整组；正在处理的不能删），另有"删除已导入过的文件"。
  删除是永久的（链接只删链接本身，即断开链接，原文件不动），确认框中说明；删除和导入后，收件文件夹中变空的子文件夹
  一并删除（与 git 删除文件后的做法相同）；只剩文件管理器自动写入的 `.DS_Store`（访达）、`Thumbs.db`、`desktop.ini`（资源管理器）的文件夹也算空，连同这几个文件删除，有其他任何文件则保留。删除记审计日志 `hub:inbox_delete`。
- 模型服务每次启动和重启前都重新读取当前选中的模型并确认文件还在（`Child.build`）：文件不在就不启动，写一行
  原因到 `model.log`，工作台和向导显示需要准备模型；不会拿旧路径反复重启。

**放入文件夹的检测方式**

- 向导打开期间，后端每 2 秒检查一次 `models/inbox/` 的**所有层级**的子文件夹（`os.walk`），以及 `models/` 根目录
  散放的 `.gguf`。每一行用相对 `models/` 的路径显示。可以直接放入整个下载好的文件夹：
  - Hugging Face 缓存（`hf download`）：`snapshots/<提交>/` 里是指向 `blobs/` 的链接，`blobs/` 里的文件以哈希命名；
  - llama.cpp 的模型文件夹约定：多文件的模型放一个子文件夹，视觉模型文件名以 `mmproj` 开头，分片放在同一子文件夹。
- 指向文件夹的链接不进入（`os.walk` 默认不跟随），避免循环，列为一行说明；隐藏文件和文件夹跳过；
  浏览器还没下载完的文件（Chrome `.crdownload`、Firefox `.part`、Safari `.download` 包）等它改成正式名字。
- **按内容认清单中的文件**：文件名与清单一致，或者大小与清单中某个文件相同（改过名，或 `blobs/` 中以哈希命名），
  且连续两次检查大小和修改时间不变（复制或下载完毕），就计算 SHA-256；一致则作为那个文件导入，模型库中用清单
  中的文件名。同一个文件在文件夹中出现多次（`blobs/` 和链接）只导入一次。大小相同但内容不同的改名文件，标出原因，
  可以作为自定义模型导入。名字相同的文件比清单大或内容不同，标出是哪个文件，文件留在原处。
- **清单外的文件只在用户点"导入"后处理**：
  - `.gguf`：文件名含 `mmproj` 的建议为视觉模型（与程序其他地方的判断相同），其余建议为大模型；
  - 分片模型（gguf-split 命名 `<名称>-00001-of-00003.gguf`）同一子文件夹中的分片合成一行，分片不全时显示
    "找到 x 个，共 y 个"、不能导入；导入时全部分片进同一个版本（模型卡中第一片为 `weights`，其余为 `shard`），
    llama-server 拿第一片、从同一文件夹读其余分片；
  - `.onnx`：按路径中的词建议种类（PaddleOCR 的命名：`det` → 文字检测、`rec` → 文字识别、`slanet`/`table` →
    表格结构），由用户确认；文字识别模型可以选同一文件夹中的 `.txt` 作为字典。
  - `.gguf` 导入后设为使用中；OCR 模型只放入模型库，到「设置 → 模型」中换用（与模型库页面一致）。
  - 名称、种类沿用模型库的规则（`registry.precheck`），不符合时直接提示，文件不动。
- **导入时不会丢文件**：收件文件夹中的普通文件先硬链接（磁盘不支持时复制）到模型库的临时文件夹，模型库检查通过、
  版本建好后，才从收件文件夹删除；模型库拒绝时文件仍在原处（模型库会删掉临时文件夹）。符号链接复制一份，
  链接和它指向的文件都保留。与 LM Studio `lms import` 一致：默认移动，也可以复制或链接。
- 不需要用户确认清单中的文件：每行一个状态——复制或下载中、排队、正在校验 SHA-256（带进度）、正在放入模型库
  （带进度）、模型库已有这个文件、大小相同且模型库已有（未再校验）、同一文件正在通过其他方式获取、内容不同、
  比清单大、分片不全、指向文件夹的链接。同一过程也显示在向导上方的"进度"中，并标出来自"放入文件夹"。
- 其他文件（说明、配置等）只计数，显示"另有 N 个其他文件，已忽略"。

下载过程：

- **开始前检查磁盘空间**：剩余空间少于"文件大小 + 1 GB"时不开始，并提示需要多少空间。
- **断点续传**：先写入 `<数据目录>/models/downloads/<文件名>.part`。中断后再次开始时，用 HTTP `Range` 请求剩余部分，
  并带上 `If-Range`（首次下载时记录的 ETag）。服务器返回 206 就接着写；返回 200 说明文件变了，丢弃已下载部分重新开始
  （MDN `If-Range` 文档描述的标准做法）。
- **进度**：页面显示已下载大小、速度、预计剩余时间；可以暂停、继续、取消。关闭程序后再打开，可以从断点继续。
- **校验**：下载完成后计算 SHA-256，与清单对比；不一致则删除文件并提示重新下载。字节数不一致同样视为失败。
- **导入**：校验通过后把文件**移动**（不是复制）到模型库，设为"使用中"，重启模型服务。不会多占一份空间。
- **网络限制**：只请求清单里的地址。允许各来源把请求重定向到它们的文件存储服务器，但只接受 HTTPS；
  不发送任何用户数据、不带 Cookie、不带任何账号令牌（三个来源都是公开的，不需要登录）。
- **实现**：使用已有依赖 `httpx`（BSD 许可）以流式方式下载，不引入 `huggingface_hub`。下载在后端进程中进行，
  不经过 Tauri（`jacara/downloads/`，接口 `/api/v1/setup`，只在桌面版可用）：
  - 重定向由程序逐跳处理：每一跳都必须是 HTTPS，每跳前清空 Cookie，最多 10 跳。
  - 按收到的数据边收边写（不按固定块攒），连接中断时已收到的部分不丢，下一个来源从这里接着请求。
  - 同一来源续传时带 `If-Range`（上次记下的 ETag）；换来源时只带 `Range`（各来源的 ETag 不同，带了会被当成文件已变而
    从头下载），正确性由来源返回的总长和最后的 SHA-256 保证。
  - 206 的 `content-range` 起点或总长与清单不符、200 的 `content-length` 与清单不符、收到的数据超过清单大小，都算该来源失败。
  - 下载、校验、导入在后台线程中依次进行；"暂停"在写完当前数据块后停下，排队中的文件一起暂停；恢复备份和移动数据文件夹
    开始前会先暂停下载。程序重新打开后，未完成的文件显示为"已暂停"，由用户点"继续"，不会自动联网。
  - 模型库里已有同一文件（SHA-256 相同）时不下载，直接设为使用中。
- **实机验证**：六个固定地址都支持 `Range`（2026-11-15，见上）。本地模拟服务器上的测试覆盖了：来源报错、连接中断后换来源
  续传、30 秒无数据、暂停后继续（带 `If-Range`）、文件已变（`If-Range` 不匹配返回 200）、校验不一致重下一次后失败、
  重定向不带 Cookie、拒绝非 HTTPS、磁盘空间不足、收件文件夹、选择文件、取消。在真实网络上完整下载一次，在第 5 步实测。

**规则 1 的修改**

现在的规则 1 是"运行时不访问任何外部网络"。改为：

> 运行时不访问外部网络。只有两个例外，且都必须由用户在界面上主动点击触发：
> (1) 模型下载：从随程序发布的固定清单中的地址下载模型文件，下载后按清单校验 SHA-256；
> (2) 程序更新：从固定的发布地址读取最新版本信息，用户确认后下载安装包，安装前校验更新签名（第 13 节）。
> 不得在后台自动联网，不得发送用户数据，不做遥测。

除这一处外，其他功能（识别、审核、导出、备份）照常完全离线。下载完模型后断开网络，程序可以正常使用。

**中国大陆的用户**

在中国大陆通常无法直接访问 Hugging Face。按上面的自动切换，第一次连接失败后会自动改用魔搭社区，不需要用户操作。
三个来源都无法访问时，用户可以用任何方式取得文件，再"放入文件夹"。

**在 Tauri 窗口中需要单独处理的浏览器行为**

- **下载**（已实现，不依赖网页内核）：macOS 的 WKWebView 不处理 `<a download>`，除非程序自己接管下载
  （tauri-apps/tauri#4633）。桌面版因此由后端保存：网页请求文件时带 `X-Jacara-Save: downloads`，后端的
  `SaveToDownloads` 中间件把附件响应边接收边写入系统"下载"文件夹（`platformdirs.user_downloads_path()`；Windows 上
  是可被用户移动过的已知文件夹 FOLDERID_Downloads），写完前用隐藏的临时文件名，重名时加" (1)"" (2)"（与浏览器
  相同），再回答保存位置；网页里生成的文件（审计日志 CSV）发给 `POST /api/v1/desktop/downloads` 保存。网页随后提示
  "已保存到'下载'文件夹"，并可"在文件夹中显示"（`open -R` / `explorer /select,`，只允许"下载"文件夹里的文件）。
  这样 Tauri 不需要 `on_download`，两种内核行为一致。
- **拖放上传**：Tauri 默认拦截文件拖放，网页收不到 HTML5 的拖放事件；**窗口配置必须设 `dragDropEnabled: false`**
  （react-dropzone 的 Tauri 说明；Windows 上不关闭则无法使用 HTML5 拖放）。
- **"选择整个文件夹"**：macOS 上 wry 的 `runOpenPanelWithParameters` 按 `allowsDirectories` 调用
  `setCanChooseDirectories`，`<input webkitdirectory>` 可以选文件夹；Windows 的 WebView2 是 Chromium 内核，原生支持。
  第 5 步在两种内核上实测。
- **外部链接**（已实现）：桌面版拦截指向其他网站的链接，由后端用系统默认浏览器打开（Python 的 `webbrowser`，只接受
  http 和 https），不在程序窗口中打开。
- **网页引擎差异**：macOS 上是 Safari 的内核，Windows 上是 Edge 的内核。前端要在两者上各完整检查一遍。

## 7. 打包

### 7.1 Python 部分：PyInstaller 单文件夹模式

| 方案 | 结论 | 理由 |
|---|---|---|
| PyInstaller 单文件夹（onedir） | **采用** | 构建快，社区钩子覆盖面广；生成的程序可按任意许可发布，只要符合各依赖的许可 |
| PyInstaller 单文件（onefile） | 不用 | 每次启动都要把全部文件解压到临时目录，并以两个进程运行，与第 2 节的进程管理冲突 |
| Nuitka | 暂不用 | 把 Python 编译为 C，较难还原源码；但构建时间长（中等规模项目 5–15 分钟，PyInstaller 约 30 秒），工具本身为 AGPL（对生成的程序有例外） |
| Briefcase | 不用 | 自成一套打包和安装流程，与 Tauri 外壳重复 |

- 单文件夹整体作为 Tauri 资源（`bundle.resources`）打包，由 Rust 代码直接启动。Tauri 的 `externalBin` 只适合单个
  可执行文件，不适合单文件夹。
- 桌面版不需要的依赖不打包：`celery`、`kombu`、`redis`（Valkey 客户端）、`boto3`、`psycopg`，在 spec 的 `excludes`
  中排除（它们在代码里都只在服务器方式的分支中按需导入）。**这样 LGPL 许可的 `psycopg` 不会出现在安装包中。**
  `scipy` 也排除：只有评测命令（开发工具）用它，它的 macOS wheel 还带着 GCC 的 Fortran 运行库。`check_bundle.py`
  检查这些包确实不在打包结果中，`licenses.py` 也不把它们列入许可清单。
- 构建配置：`packaging/desktop/jacara.spec`，入口 `packaging/desktop/jacara_desktop.py`，构建工具在依赖组 `desktop`
  （`uv run --group desktop pyinstaller packaging/desktop/jacara.spec`）。PyInstaller 只跟随 import 收集代码，程序
  按文件读取的内容在 spec 中逐项列出，缺一项就构建失败：起步模板（`templates/**/*.toml`）、界面翻译（`.mo`）、
  数据库迁移、RapidOCR 的模型和配置、rapid-layout 的 `layout_cdla.onnx`（出现其他权重即失败，第 3.5 节）、
  rapid-table 的配置、校验过 SHA-256 的表格模型、运行时用 `importlib.metadata` 读取版本的包的元数据；按名字加载的
  模块（uvicorn 的 `jacara.app:app` 和协议实现、Alembic、Huey）列为隐藏导入。
- 数据库迁移放在程序包内（`jacara/migrations`），后端按包的位置找到它们，不依赖当前文件夹；开发时
  `uv run alembic ...` 仍通过项目根目录的 `alembic.ini`。
- 冒烟测试：`packaging/desktop/smoke.py` 从无关的文件夹、空数据目录启动打包结果，检查迁移、翻译、起步模板、
  附带的表格模型、`LICENSE` / `NOTICE` / `THIRD_PARTY_LICENSES` 能读到、诊断信息里有附带的 `llama-server` 的版本
  且不含主目录、PDF 通过 OCR、监视文件夹导入、保存到"下载"文件夹、完整备份和恢复、关闭标准输入后退出。
  2026-11-15 在 Linux（aarch64）上构建并全部通过（打包结果约 407 MB）；加入 llama.cpp 和许可清单后在 Linux（x86_64）
  上再次全部通过（打包结果约 518 MB，其中 llama.cpp 约 78 MB）；换成 `build_opencv.py` 构建的 OpenCV 后
  同样全部通过，`test_opencv_resize.py` 和 OCR、版面、表格的测试也通过，打包结果约 509 MB（不再有 FFmpeg 等视频库）。macOS 和 Windows 上在自动构建中运行（7.3 节）。
- 打包检查：`packaging/desktop/check_bundle.py` 在 Tauri 打包前检查打包结果：必需文件齐全、每个 `llama-server`
  能运行 `--version`；模型权重只有 RapidOCR 自带的、`layout_cdla.onnx` 和 SLANet-plus（出现 YOLOv8 等 AGPL 权重或
  `.gguf` 即失败）；没有 `tests`、`docs` 目录和 `.md` 文档；没有 GPL 库（x264、x265 等只有 GPL 版 FFmpeg 才会链接的库，
  或内嵌配置含 `--enable-gpl` / `--enable-nonfree`、内嵌许可字符串为 GPL 的 FFmpeg 库）；排除的包确实不在。
  macOS 上还检查每个二进制文件要求的最低系统版本不高于 `tauri.conf.json` 的设置，并逐个 ad-hoc 签名后
  `codesign --verify --strict`（8.1 节）。
- PyInstaller 不能跨平台构建，macOS 包在 macOS 上构建，Windows 包在 Windows 上构建。
- 说明：PyInstaller 打包的是 `.pyc` 字节码，有心人可以还原出接近源码的内容。源码不公开主要靠"不发布源码"，
  不靠打包工具。若以后需要更强的保护，再评估 Nuitka。

### 7.2 Tauri 外壳和安装包

- macOS：`.dmg`（拖入"应用程序"文件夹）。只支持 Apple 芯片。
- Windows：NSIS 安装程序 `-setup.exe`，默认按用户安装到 `%LOCALAPPDATA%`，不需要管理员权限。WebView2 采用默认的
  "下载引导程序"方式；Windows 10（2018 年 4 月更新及以后）和 Windows 11 已自带 WebView2，通常无需下载。
  不提供 MSI（MSI 只在企业批量部署时需要）。
- macOS 最低系统版本：构建后检查各二进制文件（Python 运行库、onnxruntime、llama-server）要求的最低版本，取最高者写入
  `tauri.conf.json` 的 `bundle.macOS.minimumSystemVersion` 和 README。现在是 14.0（onnxruntime 的 wheel 标为
  `macosx_14_0_arm64`；llama.cpp 要求 13.3），`check_bundle.py` 每次构建都核对。
- 外壳在 `desktop/`：`desktop/src-tauri`（Rust，tauri 2.12、tauri-plugin-single-instance、tauri-plugin-opener）和
  `desktop/splash`（启动页）。`desktop/package.json` 只装 Tauri 命令行工具（`@tauri-apps/cli`），与 `web/` 的前端依赖
  分开，所以根目录仍然没有 `package.json`。外壳的行为：
  - 只有一个窗口，先显示本地启动页，同时启动 `resource_dir/backend/jacara-backend desktop`，环境变量
    `JACARA_HOME`（数据文件夹：系统的本地应用数据文件夹加程序名，即 Electron `userData` 的命名方式）、
    `JACARA_LOG_DIR`、`JACARA_WEB_DIR`、`JACARA_WATCH_STDIN=1`；后端的标准错误写入日志文件夹的 `backend.log`，
    外壳自己的记录写 `app.log`；Windows 上以 `CREATE_NO_WINDOW` 启动，不出现命令行窗口。
  - 后端输出 `JACARA_READY <地址>` 后窗口打开这个地址；后端没能启动或意外退出时回到启动页，显示原因和日志文件夹。
  - 窗口内只允许启动页和后端自己的地址，其他链接用系统浏览器打开；窗口不接管文件拖放
    （`disable_drag_drop_handler`），网页收到的是 HTML5 拖放。
  - 再次打开程序时把已有窗口调到前面（单实例插件）。
  - 退出时关闭后端的标准输入，最多等 20 秒（后端自己 10 秒内停止 worker 和模型服务），还没退出才强制结束。
  - `bundle.resources` 把 `dist/jacara-backend/` 映射为 `backend/`、`web/dist/` 映射为 `web/`。已在 Linux 上用
    `tauri build --bundles deb` 核对映射和可执行权限。

### 7.3 自动构建

- GitHub Actions，两台运行器：`macos-15`（Apple 芯片）和 `windows-latest`。不用 `macos-14`：GitHub 已于
  2026 年 11 月 2 日停用该镜像。
- 使用 Tauri 官方的 `tauri-apps/tauri-action` 构建并创建草稿发布，人工检查后再正式发布。
- 构建步骤：安装依赖 → 构建网页 → 下载并校验 llama.cpp 和表格模型 → PyInstaller 打包后端 → 运行
  `packaging/desktop/smoke.py` → 生成许可清单 → 检查 AGPL 权重和签名 → Tauri 打包 → 计算 SHA-256。
- 后端测试也在 Windows 上运行一遍（现有代码只在 macOS 上运行过，进程管理、路径、信号处理需要验证）。
- 实现：`.github/workflows/desktop.yml`。改动打包相关文件的 PR 和手动运行时只构建，安装包留在工作流的构件中
  （GitHub 只在工作流文件已经在默认分支上时才能手动运行，所以新的打包改动靠 PR 触发）；推送 `v*` 标签时把安装包和
  `.sha256` 文件放进本仓库的**草稿**发布，人工检查后再发布（改为发布到公开仓库是第 6 步）。每台运行器依次：
  `check_versions.py`（`pyproject.toml`、`web/package.json`、`desktop/package.json`、`Cargo.toml`、
  `tauri.conf.json` 的版本一致，标签与版本一致）→ `uv sync --locked --group desktop` →（macOS）从源码构建
  不含 FFmpeg 的 OpenCV（见下）并运行 `test_opencv_resize.py` → 网页 → `fetch_llama.py`、`fetch_table_model.py` →
  `licenses.py` → PyInstaller → `smoke.py` → `check_bundle.py`（macOS 加 `--sign`）→ `tauri-apps/tauri-action@v1`
  （`projectPath: desktop`，不上传更新清单）→（macOS）`codesign --verify --deep --strict` 检查 `.app` →
  每个安装包的 SHA-256。后端测试在两个系统上作为单独的任务与打包并行运行，测试失败不会让打包的问题看不到，
  反过来也一样；任何一个失败，整次运行都算失败。
- 第一次运行（PR #33）时 Windows 上的后端测试暴露了几处只在 Windows 上才有的问题，已修正：
  - `jacara stop` 用 `os.kill(pid, 0)` 判断进程是否还在；Windows 上 `os.kill()` 除控制台信号外一律结束进程
    （TerminateProcess），改为用 `OpenProcess`（SYNCHRONIZE 权限）打开进程、对句柄做超时为 0 的
    `WaitForSingleObject`：进程结束时句柄变为有信号。最初用的是读退出码是否为 `STILL_ACTIVE`，按 Windows 规范检查时
    改掉了：259 也是合法的退出码，`GetExitCodeProcess` 的文档明确提醒不要据此判断进程仍在运行。Windows 没有
    `SIGKILL`。
  - 端口检查用了 `SO_REUSEADDR`：Windows 上它允许绑定到正在使用的端口，检查总是通过；Windows 上改为不加选项
    直接绑定（Windows Server 2003 起套接字默认不共享）。
  - 诊断信息把主目录写成 `~`：Windows 路径不区分大小写，可能用 `\` 或 `/`，两种写法都替换，其余部分统一用 `/`。
  - 测试本身的假设：权限位 0o600 只在 POSIX 上检查（Windows 靠用户文件夹的访问控制列表）；数据文件夹搬回之前
    先关闭数据库（Windows 不能删除打开着的文件，接口本来就这样做）；macOS 和 Windows 的文件名不区分大小写，
    测试里的 `Models` 文件夹就是数据文件夹里的 `models`。
- 第九次运行时打包检查在 Windows 上把 `llama-server-impl.dll` 当成程序运行（"llama-server*" 也匹配到它，
  WinError 193）。打包检查和 spec 改为按准确的程序名查找（Windows 上 `llama-server.exe`）。
- 第二次运行时 Windows 上 `licenses.py` 找不到 `npx`：Windows 上它是 `npx.cmd`，`CreateProcess` 不按裸名字找 `.cmd`，
  改为先用 `shutil.which` 找到程序再运行，并按 UTF-8 读取输出。
- 第四次运行时 Windows 上打包好的后端启动约 2 秒就退出，冒烟测试没有显示原因。按根本原因处理：
  - 冒烟测试改为像外壳一样在线程里一直读后端的输出（Windows 的管道只能存几 KB，没人读时后端会卡在下一行输出上），
    任何一项失败都打印后端的输出和日志文件，以后在构建机上失败也能从构建日志看到原因。
  - 打包好的程序打开 Python 的 UTF-8 模式（PEP 540，spec 里的 `X utf8`，PyInstaller 手册
    "Specifying Python Interpreter Options"；第一次误写成 `X utf8_mode=1`，引导程序只认 `utf8` / `utf8=1`，
    别的写法不报错也不生效，第六次运行时 worker 仍以 cp1252 输出而崩溃才发现。现在诊断信息写出 UTF-8 模式和输出编码，
    冒烟测试检查"UTF-8 mode on"，在 macOS 和 Windows 的构建机上都能确认设置真的生效）。打包好的程序不读 `PYTHONUTF8` 等环境变量；没有这个设置时，Windows 上的
    Python 用系统代码页（如 cp1252、cp936）写管道里的标准输出和标准错误、读写子进程的文本，而外壳按 UTF-8 读后端的
    输出，测试在各系统上也都是 UTF-8 模式（CI 设了 `PYTHONUTF8=1`，macOS 和 Linux 本来就是 UTF-8）。
  - 外壳读后端输出时，遇到不是 UTF-8 的行改为用替换字符显示；原来会结束读取，被当成后端已经停止。
- 第五次运行时冒烟测试显示了真正的原因：`kill_children_with_me()`（把后端放进 Windows 作业对象，后端结束时系统结束
  它启动的所有进程）调用 Windows 接口时没有声明参数类型。ctypes 把 Python 整数当作 C 的 int 传，而
  `GetCurrentProcess()` 返回的伪句柄 -1 作为 HANDLE 是 0xFFFFFFFFFFFFFFFF，放不进 int，后端在 Windows 上一启动就崩溃。
  已为用到的每个接口声明参数和返回类型；某一步失败时写一行原因到标准错误（程序照常运行，只是少了崩溃后清理子进程
  的保障）。
- 这个错误没有被测试发现，是因为 `test_desktop_launcher.py`（真正启动 `jacara desktop`、检查就绪、关闭标准输入后
  退出、后端被结束后 worker 也结束、worker 崩溃后重启、监视文件夹、备份、搬数据文件夹）整个文件在 Windows 上跳过了。
  已改为在 Windows 上也运行：检查进程是否还在用 `_alive()`；结束进程用 `Popen.kill()`（Windows 上是
  TerminateProcess）；Windows 的虚拟环境里 `python.exe` 是另起基础解释器的启动器，结束它并不结束应用本身，所以在
  Windows 上直接用基础解释器启动，并传入同样的导入路径。worker 被结束后重启的日志在 Windows 上是退出码 15
  （`os.kill` 的信号号），在 POSIX 上是 SIGKILL。仍只在 POSIX 上运行的测试只剩按信号判断退出原因
  （`test_desktop_supervisor.py`）和 KleidiCV 的内存保护检查（`test_opencv_resize.py`，用 POSIX 的 `mprotect`）。
- 第六次运行时 Windows 的 worker 日志还显示 "Fatal Python error: _enter_buffered_busy"：监视标准输入的守护线程一直
  等在 `sys.stdin.buffer.read()` 里，持有 BufferedReader 的锁；进程因为别的原因退出时（主线程返回或出错），解释器
  关闭时拿不到这个锁就中止，任何退出都变成崩溃（Linux 上 SIGABRT，Windows 上 0xC0000005）。在 Linux 上也能复现
  （10 次 10 次）。改为读文件描述符（`os.read`，不经过有锁的缓冲对象），并加了测试：进程在标准输入仍打开时正常退出，
  退出码为 0、没有 Fatal Python error（改之前这个测试失败）。
- 第七、八次运行时 Windows 冒烟测试卡在"监视文件夹导入"：监视进程不输出也不退出。冒烟测试打开
  `JACARA_STACK_DUMP_SECONDS`（各后台进程每隔这么多秒用 faulthandler 把所有线程的调用栈写进日志；不设置时不开）后，
  日志显示监视进程的主线程停在第一次扫描时的 `import numpy`（加载 `_multiarray_umath` 扩展模块），另一个线程是等在
  标准输入上的读取。原因是 Windows 的同步句柄上的 I/O 是串行的：一个线程在标准输入管道上的 ReadFile 一直等着，
  其他线程对这个句柄的任何操作也要等；新加载的 DLL 的 C 运行库启动时会查询标准句柄，于是一直等下去（Raymond Chen，
  "Don't let more than one process try to read from stdin at the same time"；CPython issue 34780 是 Python 自己启动时
  因为同样原因卡住）。这对后端主进程同样成立：只要在监视线程开始之后才第一次加载某个 DLL（例如第一次渲染 PDF
  页面时的 pdfium），就会卡住。修改：Windows 上不再在标准输入上留一个等着的读取，改为每 0.5 秒用 `PeekNamedPipe`
  检查一次管道（立即返回；有数据就读走，不会等；`ERROR_BROKEN_PIPE` 表示另一端已关闭或已退出）；标准输入不是管道时
  写一行原因到标准错误、不再监视。macOS 和 Linux 没有这个限制，仍然直接读。新加测试：监视标准输入之后再加载
  numpy、onnxruntime、pypdfium2 必须在限定时间内完成；关闭标准输入后进程在 10 秒内按预期退出。
  （同一套监视文件夹测试从源码运行时在 Windows 上通过了，原因没有完全查清，可能与导入顺序有关；打包后的程序是
  用户实际运行的，以冒烟测试为准。）
- **macOS 的 OpenCV 从源码构建**：PyPI 上 `opencv-python-headless` 的 macOS wheel（4.9 到 5.0 都是）带的 FFmpeg
  是用 `--enable-gpl` 构建的：`cv2` 加载 `libavcodec`，`libavcodec` 链接 `libx264`、`libx265`（GPL-2.0 及以上），
  4.13.0.92 还带 `librubberband`、`libvidstab`、`libpostproc`。项目页说"All wheels ship with FFmpeg licensed under
  the LGPLv2.1"，与 macOS wheel 的实际内容不符。程序只读图片和 PDF，不读写视频，所以 macOS 安装包改用
  `packaging/desktop/build_opencv.py` 构建的 wheel：按 `packaging/desktop/opencv.lock` 固定的
  opencv-python 发布标签和提交（与 `uv.lock` 同一版本，4.14 起有 KleidiCV 的越界读取，已在 `pyproject.toml` 中限制），
  按其 README 的手动构建方法（`ENABLE_HEADLESS=1`，另加 `CMAKE_ARGS`）构建：`WITH_FFMPEG=OFF`、
  `WITH_GSTREAMER=OFF`（不带任何视频库）；`OPENCV_FORCE_3RDPARTY_BUILD=ON`，zlib、libpng、libjpeg、libtiff、libwebp、
  OpenJPEG、OpenEXR 用 OpenCV 自带的源码编译，不链接构建机上碰巧装着的库（macOS 运行器上的 Homebrew）；
  `WITH_AVIF=OFF`（AVIF 只能用构建机上的库）。在 Linux 上试构建时，不加这个开关的 `cv2` 链接了系统的
  libjpeg、libpng、libtiff、libwebp。安装后检查 `cv2.getBuildInformation()` 的 Video I/O 中没有 FFmpeg，
  且 `cv2` 只链接系统库（macOS：`/System/Library`、`/usr/lib`）。之后的步骤不再同步环境（`uv run --no-sync`），
  否则 uv 会装回 PyPI 的 wheel。构建好的 wheel 按 `opencv.lock` 的内容缓存。macOS 上用
  `MACOSX_DEPLOYMENT_TARGET`（CMake 据此设 `CMAKE_OSX_DEPLOYMENT_TARGET`）把最低系统版本定为
  `opencv.lock` 的 `macos_deployment_target`，它必须等于 `tauri.conf.json` 的最低版本（脚本检查）；不设时按构建机的系统
  版本编译，第二次运行时 `check_bundle.py` 查出 `cv2` 要求 macOS 15.0。Windows 的 wheel 只带 OpenCV 自己构建的 LGPL 版 FFmpeg 插件
  （`opencv_videoio_ffmpeg*.dll`，内嵌许可字符串为"LGPL version 2.1 or later"），照常使用。
- 不支持 Intel Mac：GitHub 已停用最后一个 Intel Mac 标准镜像（`macos-13`），只剩 `macos-15-intel` 维持到 2027 年秋；
  Python 也已把 Intel Mac 降为二级支持。Intel Mac 没有 Metal 加速的大模型推理，9B 模型速度也难以接受。

## 8. 未签名发布

### 8.1 macOS

- Tauri 文档指出：Apple 芯片要求从互联网下载的程序必须有签名，没有 Apple 证书时可以使用 ad-hoc 签名
  （签名身份设为 `-`）。ad-hoc 签名不能免去用户的手动放行。
- 构建后用 `codesign --verify --deep --strict` 检查程序包内每个二进制文件（含 PyInstaller 收集的动态库和
  `llama-server`）都已签名；未签名的在打包脚本中逐个 ad-hoc 签名。
- 用户首次打开时的步骤（README 中配图说明）：
  1. 双击打开，系统提示无法验证开发者，点"完成"。
  2. 打开"系统设置 → 隐私与安全性"，在页面下方找到 JacarandaParse，点"仍要打开"，输入登录密码。
  3. macOS 15 起不能再用"按住 Control 键点按 → 打开"绕过（Apple 2024 年 8 月的开发者公告）。
- 如果提示"已损坏，无法打开"：先核对 SHA-256 与发布页一致，再在终端执行
  `xattr -cr /Applications/JacarandaParse.app`。这会去掉系统的隔离标记，只对确认来自官方发布页的文件这样做。

### 8.2 Windows

- 未签名的程序会被 SmartScreen 拦截，显示"Windows 已保护你的电脑"。用户点"更多信息"，再点"仍要运行"。
- 微软文档说明：未签名的文件，每个新版本的信誉都从零开始，提示不会因为旧版本下载过而消失。
- 企业电脑可能被策略禁止运行未签名程序，README 中注明。

### 8.3 以后签名的条件

- macOS：需加入 Apple 开发者计划（账号持有人）获取 Developer ID 证书，再用 `notarytool` 公证。
- Windows：微软 Artifact Signing 的个人开发者只限美国和加拿大；其他地区个人可购买 OV 证书（每年约 150–300 美元）；
  符合条件的开源项目可申请 SignPath Foundation。源码不公开，所以 SignPath Foundation 不适用。

## 9. 许可和公开仓库

- **许可**：沿用 Apache 2.0。新增 `NOTICE` 文件：

  ```
  JacarandaParse
  Copyright 2026 Ryan Camus
  ```

  Apache 2.0 第 4 条（d）项要求转发者保留 NOTICE 中的署名；第 6 条不授予使用作者名称的权利。`LICENSE` 末尾的
  `Copyright [yyyy] [name of copyright owner]` 是许可原文附带的填写示例，保持原样。
- **第三方许可清单**（`THIRD_PARTY_LICENSES`，随安装包发布，"关于"页可以查看），构建时由
  `packaging/desktop/licenses.py` 自动生成，写到 `build/desktop/THIRD_PARTY_LICENSES`，spec 把它打进程序包：
  - Python 依赖：`uv tree --frozen --no-dev --no-dedupe` 去掉不打包的包（7.1 节）得到运行时依赖；许可名称用
    `pip-licenses`（MIT）；许可原文收全每个包带的所有许可和声明文件（元数据中的 `License-File`（PEP 639）、
    `.dist-info` 里的、导入包顶层的，例如 onnxruntime 的 `ThirdPartyNotices.txt`、opencv 的
    `LICENSE-3RD-PARTY.txt`、shapely 的 `LICENSE_GEOS`），因为 `pip-licenses` 每个包只取一个文件。
    包的许可是 GPL、LGPL 或 AGPL 即构建失败。元数据没写许可的包（huey）在脚本中写明许可，并要求包里有许可文件。
  - 包在许可文件中声明的随附组件（NumPy 等 wheel 用的"Name: / Files: / License:"格式）：GPL 系的只允许两项，
    其余即构建失败：GCC 运行库（GPL-3.0-or-later WITH GCC-exception-3.1，运行库例外允许用 GCC 编译并与之组合的程序
    按任意条款发布；Windows 的 NumPy 的 OpenBLAS 库内含它）和 libquadmath（LGPL-2.1-or-later，独立的动态库）。
    实际打进安装包的二进制文件另由 `check_bundle.py` 检查（7.1 节）。
  - 网页依赖：`license-checker-rseidelsohn`（BSD-3-Clause）带 `--production`。
  - Tauri 的 Rust 依赖：`cargo-about`（0.9.3，`generate --format json`），配置在 `desktop/src-tauri/about.toml`：
    只列发布目标（`aarch64-apple-darwin`、`x86_64-pc-windows-msvc`）编译进去的 crate；接受的许可依次为 Apache-2.0、
    MIT、BSD-3-Clause、Unicode-3.0、Zlib、MPL-2.0，多许可的 crate 取其中第一个被接受的，GPL 系从不接受。
    检查针对选中的许可（例如某个 crate 写"MIT OR Apache-2.0 OR LGPL-2.1-or-later"，选中的是 Apache-2.0）。
    同一份许可原文只印一次，后面列出用它的 crate（cargo-about 自带模板的做法）。
  - 手工补充：llama.cpp（MIT）和它在 Windows 上带的 LLVM OpenMP 运行库（Apache-2.0 WITH LLVM-exception）、
    Python 运行时（PSF 许可）、PyInstaller 引导程序（GPL-2.0-or-later WITH Bootloader-exception）、
    PaddleOCR 的 OCR、版面和表格模型（Apache 2.0）。
  - 模型不随安装包分发，但在清单和 README 中注明：Qwen3.5-9B 为 Apache 2.0，GGUF 文件来自 unsloth 的转换仓库，
    同样标为 Apache 2.0。
- **现有依赖的许可检查结果**（2026-11-14，后端 97 个包、前端 8 个直接依赖）：
  - 没有 GPL、AGPL。
  - 2026-11-15 生成许可清单时补充：随包分发的范围是 61 个 Python 包、81 个网页包、251 个 Rust crate；包本身的许可
    没有 GPL 系。但 `opencv-python-headless` 的 macOS wheel 带 GPL 版 FFmpeg 和 x264、x265，macOS 安装包因此改用
    从源码构建的 OpenCV（7.3 节）。用 PyPI wheel 在本机打出的测试包含这些库，不能对外分发。
  - `psycopg`、`psycopg-binary` 为 LGPL-3.0：桌面版不打包（7.1 节）。
  - `certifi`、`tqdm` 为 MPL-2.0（文件级要求）：不修改其源码，附上许可原文即可。
  - 前端直接依赖全部为 MIT；间接依赖在构建时由上面的工具全量检查。
- **构建工具的许可**：PyInstaller 为 GPL 加引导程序例外，生成的程序可按任意许可发布；社区钩子包的标准钩子为
  GPL-2.0 及以上，只在构建时运行，不进入安装包；进入安装包的运行时钩子为 Apache 2.0。规则 10 约束的是运行时依赖，
  构建工具不受影响。
- **公开仓库只放两个文件**：`README.md` 和 `LICENSE`。参照两个闭源桌面软件的公开仓库：Obsidian 的
  `obsidian-releases` 只用于发布安装包，说明中写明软件不开源；LM Studio 的 `lmstudio-bug-tracker` 只用于收集问题。
  - `NOTICE` 和 `THIRD_PARTY_LICENSES` 放在安装包内，并在"设置 → 关于"中显示。Apache 2.0 第 4 条（d）项允许 NOTICE
    随分发物一起提供或在程序中显示，安装包本身就是分发物，满足要求。
  - 截图和 GIF 通过 GitHub 编辑页面上传为附件（`github.com/user-attachments/...` 链接），不在仓库中放图片文件；
    演示视频放在视频网站，README 中只放链接。
  - Releases 中放安装包、`.sha256` 文件和 `latest.json`（程序内更新用）。发布说明来自 `packaging/notes/<版本>.md`，
    只写用户能感知的变化，不写开发细节。
- **README 的内容**（`packaging/README.md`，发布时复制到公开仓库）：按 GitHub 文档对 README 的建议
  （做什么、为什么有用、怎么开始、到哪里求助、谁在维护）组织为：一句话介绍、截图和演示视频、功能、下载、系统要求、
  安装和放行步骤、第一次使用、数据和隐私、更新和备份、问题反馈、许可；末尾附英文摘要。
  不写开发相关内容（技术架构、开发环境、测试、设计记录、路线图）。
  文中用"【发布前填写：…】"标出发布前才能确定的内容（截图、视频、内存要求、程序大小、反馈渠道）；发布流程检查
  README 中不再有该标记，否则发布失败。
- **安装包内不含测试和文档**：PyInstaller 只收集运行时导入的代码；构建脚本检查打包结果中没有 `tests`、`docs`
  目录和 `.md` 设计文档，出现即构建失败。测试、设计文档（ADR）、CHANGELOG 只保留在私有仓库中。
- **不再公开源码**：现在的 `release.yml` 把 `git archive` 生成的源码 zip 上传到公开仓库的 Releases。改为只上传安装包。
  已经发布的源码 zip 是否从公开仓库删除，由作者决定（已下载的人仍可按 Apache 2.0 使用那一版）。

## 10. 体积和硬件要求

**现在的体积**（2026-11-14 在作者电脑上测量）：

| 部分 | 大小 |
|---|---|
| Python 依赖（`.venv`） | 约 833 MB，其中开发工具和桌面版不需要的服务器依赖约 347 MB |
| 打包好的网页（`web/dist`） | 约 6.3 MB |
| 大模型 `Qwen3.5-9B-Q4_K_M.gguf` | 5.68 GB |
| 视觉模型 `mmproj-F16.gguf` | 0.92 GB |
| 表格模型 | 约 7.5 MB |
| Docker 镜像（PostgreSQL、Valkey、SeaweedFS）、llama.cpp | 未测量（桌面版不再需要 Docker） |

**桌面版安装后的预计体积**：去掉开发工具和服务器依赖后，Python 依赖约 465 MB，加上 Python 运行库、网页、表格模型和
llama-server。安装包经过压缩，下载体积会更小。准确数字以第一次构建的实测为准，写入 README。模型另外下载。


**硬件要求**

- macOS：Apple 芯片。Windows：Windows 10/11 x64。
- 磁盘：大模型约 5.68 GB，视觉模型和安装包另计；导入模型时会临时多占一份模型大小的空间。
- 内存：在 16 GB 内存的 Mac 和 Windows 电脑上实测（含 32768 上下文长度）后写入 README，不预先估计。

## 11. 本版不做

- 网页版、在线体验版。
- 代码签名和公证（第 8.3 节）。
- 后台自动检查更新（只在用户点击"检查更新"时检查，第 13 节）。
- Intel Mac、Windows ARM、Linux 桌面版。
- Windows CUDA 加速。
- 团队版桌面程序。
- 在本项目 GitHub 发布页转存模型（需拆分，见上）。

## 12. 实施步骤和验收

| 步骤 | 内容 | 验收 |
|---|---|---|
| 1 | 后端可切换：`JACARA_HOME`、三项运行方式设置、`LocalStorage`、Huey 队列、SQLite 连接设置、迁移批量模式 | 全部测试在 SQLite + 本地存储 + Huey 下通过；空 SQLite 上执行全部迁移后与模型定义一致；Docker 方式不受影响 |
| 2 | `jacara desktop` 子命令：随机端口、令牌、Host 和 Origin 检查、进程管理、就绪输出 | 在 Mac 上从源码运行，正常启动和退出；强制结束父进程后没有残留进程；带错误 Host 或外站 Origin 的请求被拒绝 |
| 3 | 界面改动和模型下载（第 6 节） | Safari 内核和 Edge 内核下各完整走一遍：上传、识别、审核、导出、备份、恢复、首次启动向导；下载中途断网再恢复能续传；篡改文件后校验失败 |
| 4 | PyInstaller 打包后端，附带 llama.cpp 和模型文件，生成许可清单 | 打包结果在没有安装 Python 的 Mac 上运行；`packaging/desktop/smoke.py` 全部通过；AGPL 权重检查和许可检查生效 |
| 5 | Tauri 外壳、`.dmg`、Windows 安装包、GitHub Actions | 在全新的 macOS 用户账户和全新的 Windows 虚拟机上安装、放行、首次启动、下载模型、处理样例单据、导出（文件出现在"下载"文件夹）、拖放上传、选择整个文件夹上传、外部链接在系统浏览器中打开、退出后无残留进程、升级安装保留数据、卸载 |
| 6 | `NOTICE`、README、放行说明配图、`release.yml` 改为只发布安装包和 `latest.json` | 公开仓库中不含源码；README 中的每个步骤都按实际界面截图核对 |
| 7 | 升级和备份（第 13 节）：升级前备份、降级保护、程序内更新、自动备份、从备份恢复 | 用上一版本建的数据升级到新版本无损；迁移失败能还原；装回旧版本时拒绝打开；在另一台电脑上从备份完整恢复 |

## 13. 升级、数据迁移和备份

### 13.1 程序和数据分开存放

- 程序装在"应用程序"文件夹（macOS）或 `%LOCALAPPDATA%` 下的安装目录（Windows，NSIS 按用户安装的默认位置），数据在第 4 节的数据目录。
- 安装新版本只替换程序文件，**数据目录不动**：业务数据、上传的文件、模型库（含已下载的模型）、设置全部保留。
  用户不需要做任何迁移操作。
- 卸载程序同样不删除数据目录（第 4 节）。

### 13.2 新版本第一次启动时自动升级数据

- 数据目录中记录 `version.json`：上次运行的程序版本和数据库迁移版本（参照 Firefox 在配置目录的 `compatibility.ini`
  中记录 `LastVersion`）。
- **新版本打开旧数据**：
  1. 先自动做一次"升级前备份"：用 SQLite 在线备份复制 `jacara.db`，连同 `settings.json`、`version.json` 存入
     `backups/pre-upgrade-<旧版本>-<时间>/`。上传的文件不复制，因为迁移只改数据库表，不改文件。若以后某个迁移必须
     改动文件，该迁移自己负责先备份受影响的文件。
  2. 执行数据库迁移（`alembic upgrade head`，与现在 `jacara setup` 的做法相同）。
  3. 迁移失败时，用第 1 步的副本还原数据库，提示错误原因，并告知可以重新安装旧版本继续使用。
- **旧版本打开新数据**（用户装回了旧版本）：数据库迁移版本是旧程序不认识的，程序**拒绝打开**，提示"数据由更新的版本
  创建，请安装 X.Y.Z 或更新的版本"，不做任何改动，避免损坏数据。这与 Firefox 的降级保护（"You've launched an older
  version of Firefox using a profile that may not be compatible"）以及标准模板表格拒绝读取更新格式（ADR 0067）是同一做法。
  如果确实要退回旧版本，可以用旧版本恢复 `pre-upgrade-<旧版本>` 中的备份。
- 设置文件 `settings.json`：新版本不认识的项忽略，缺少的项用默认值（pydantic-settings 的现有行为）。设置项改名时，
  在升级步骤中改写，并记入 CHANGELOG。

### 13.3 模型

- 已下载的模型在数据目录的模型库中，升级后照常使用，"使用中"的选择也保留。
- 新版本的 `models.lock` 推荐了新模型时，"模型"页显示"有新的推荐模型"，由用户决定是否下载。旧模型保留，直到用户
  在模型库中删除。每条抽取结果记录了所用模型的名称和版本（规则 4），换模型后旧结果仍可追溯。
- 新版本附带的 llama.cpp 会更新。发布前的检查清单中加入一项：用上一个版本推荐的模型在新版本中处理样例单据，
  确认仍能正常加载和抽取。

### 13.4 获取新版本

两种方式都提供：

1. **程序内更新（推荐）**：设置 → 关于和诊断 → "检查更新"。
   - 用户点击后，程序读取 `https://github.com/<公开仓库>/releases/latest/download/latest.json`（Tauri 更新插件的
     静态文件格式，由 `tauri-action` 生成）。有新版本时显示版本号和 CHANGELOG 中的更新说明。
   - 用户点"下载并安装"后，Tauri 更新插件下载安装包，**校验更新签名**，然后安装并重启。Windows 使用 `passive` 模式
     （只显示进度条，不需要操作）。
   - 更新签名是 Tauri 自己的签名密钥，免费生成，与 Apple、微软的代码签名证书无关。Tauri 文档说明：更新签名不能关闭；
     **私钥丢失后，已安装的用户将无法再通过程序内更新升级**。私钥只保存在 GitHub Actions 的密钥
     （`TAURI_SIGNING_PRIVATE_KEY`）和作者自己的离线备份中，不放进仓库，也不放进 `.env`。
   - macOS：由程序自己下载的文件不带系统的隔离标记（浏览器下载的才带），预计更新后不需要再次手动放行。**需在实机验证。**
   - 不在后台自动检查。
2. **手动下载**："前往下载页"按钮在系统浏览器中打开公开仓库的 Releases 页面。用户下载新安装包，直接安装覆盖即可，
   数据同样保留。

### 13.5 备份：导出全部数据，需要时再导入

现有的备份功能（ADR 0054）就是"把全部数据导出成一个文件，以后再导入"：备份包含数据库全部表和全部上传文件，
恢复前逐项校验 SHA-256。桌面版在此基础上：

- **手动完整备份**：设置 → 数据和备份 → "立即备份"。可选择保存位置（例如移动硬盘、网盘同步文件夹）。
  备份另外包含 `settings.json` 和"使用中的模型"清单（文件名和 SHA-256）。
  可勾选"包含模型文件"：默认不勾选，因为模型有 6 GB 以上，而且可以按清单重新下载。
- **自动备份**：每天自动备份一次数据库，保存在数据目录的 `backups/auto/` 中，保留最近 14 份（参照 Immich 的默认设置：
  每天一次、保留最近 14 份），可在设置中关闭或修改份数（1–365）。与 Immich 相同，自动备份只含数据库，不含上传的文件；
  需要完整保护时用手动完整备份存到另一个磁盘（Immich 建议的 3-2-1 原则：3 份副本、2 种介质、1 份异地）。
  - 用 SQLite 的在线备份接口（Python `sqlite3.Connection.backup`）复制，程序运行中也能得到一致的副本；先写
    `.partial`，完成后改名。
  - 启动程序启动 1 分钟后检查一次，之后每小时检查一次；距上次自动备份不满 24 小时则跳过，作业进行中或找不到数据
    文件夹时也跳过。失败时记日志，下一次检查时重试。
  - 页面上可以从自动备份恢复（只恢复数据库，见第 6 节"备份和恢复"）。
- **恢复和换电脑**：在新电脑上安装程序，首次启动向导中选择"从备份恢复"，选择备份文件即可。备份中没有模型文件时，
  按"使用中的模型"清单提示下载。
- **跨版本**：备份记录了数据库迁移版本。旧版本的备份可以恢复到新版本（恢复时先建到备份时的版本，导入数据后再升级，
  现有的恢复流程已经这样做）；新版本的备份不能恢复到旧版本，提示需要安装的版本。

## 已确定

1. 署名：Ryan Camus（`NOTICE`、"关于"页、README）。
2. 数据文件夹以程序名命名（`JacarandaParse`，第 4 节），不含标识符和 GitHub 账户名。标识符定为
   `io.github.ryancamus12138-sketch.jacarandaparse`（作者没有自己的域名；Flathub 对托管在 GitHub 上的应用的命名规定），
   发布后不再改。
3. Windows 显卡加速：自动检测，有可用显卡用 Vulkan，否则用 CPU。
4. 模型下载：在程序内下载，按 Hugging Face → 魔搭社区 → hf-mirror.com 的顺序自动选择，失败时自动切换并接着下载；
   也可以手动下载后放入文件夹自动检测。
5. 更新：用户点击"检查更新"后可在程序内下载安装，也可以去发布页手动下载。规则 1 增加这两个例外。

## 参考

- GitLab：Merging CE and EE codebases — https://about.gitlab.com/blog/merging-ce-and-ee-codebases/
- Tauri：What is Tauri — https://v2.tauri.app/start/
- Tauri：Embedding External Binaries（sidecar）— https://v2.tauri.app/develop/sidecar
- Tauri：Resources — https://v2.tauri.app/develop/resources/
- Tauri：path API（localDataDir、appLocalDataDir、appLogDir、resourceDir）— https://v2.tauri.app/reference/javascript/api/namespacepath
- Electron：`app.getPath`（`userData` 默认是 `appData` 加程序名；macOS 日志在 `~/Library/Logs/<程序名>`）— https://github.com/electron/electron/blob/main/docs/api/app.md
- Apple：Bundle ID（反向域名格式）— https://developer.apple.com/help/glossary/bundle-id
- Tauri：Single Instance 插件 — https://v2.tauri.app/plugin/single-instance
- Tauri：Windows Installer — https://v2.tauri.app/distribute/windows-installer/
- Tauri：DMG — https://v2.tauri.app/distribute/dmg/
- Tauri：macOS Code Signing（ad-hoc 签名）— https://v2.tauri.app/distribute/sign/macos/
- Tauri：GitHub 发布流程 — https://tauri.app/distribute/pipelines/github/
- Tauri：`WindowConfig.drag_drop_enabled` — https://docs.rs/tauri-utils/2.9.3/tauri_utils/config/struct.WindowConfig.html
- Tauri：`Config.identifier` — https://docs.rs/tauri-utils/2.9.3/tauri_utils/config/struct.Config.html
- Tauri：`DownloadEvent` — https://docs.rs/tauri-runtime/2.10.0/src/tauri_runtime/webview.rs.html
- PyInstaller：License — https://pyinstaller.org/en/latest/license.html
- PyInstaller：Advanced Topics（单文件模式的进程和临时目录）— https://pyinstaller.org/en/latest/advanced-topics.html
- pyinstaller-hooks-contrib — https://pypi.org/project/pyinstaller-hooks-contrib
- Nuitka（pydevtools 手册）— https://pydevtools.com/handbook/reference/nuitka/
- Gitea：Configuration Cheat Sheet（DB_TYPE、STORAGE_TYPE、queue TYPE）— https://docs.gitea.com/1.19/administration/config-cheat-sheet
- Huey — https://github.com/coleifer/huey ；API 文档 — https://huey.readthedocs.io/en/latest/api.html
- SQLite：Write-Ahead Logging — https://www.sqlite.org/wal.html
- Alembic：Batch Migrations for SQLite — https://alembic.sqlalchemy.org/en/latest/batch.html
- llama.cpp：发布构建的文件名（release workflow）— https://huggingface.co/datasets/echodict/llama.cpp/blob/main/.github/workflows/release.yml
- llama.cpp：`llama-server` 参数 — https://manpages.debian.org/unstable/llama.cpp-tools/llama-server.1.en.html
- GitHub：About releases（单个文件小于 2 GiB）— https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
- huggingface_hub：Downloading files（resolve 地址、固定提交、ETag 与 SHA-256）— https://huggingface.co/docs/huggingface_hub/package_reference/file_download
- MDN：If-Range — https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/If-Range
- Immich：Backup and Restore（自动数据库备份：每天、保留最近 14 份、不含照片和视频）— https://docs.immich.app/administration/backup-and-restore
- SQLite：Online Backup API — https://www.sqlite.org/backup.html ；Python `sqlite3.Connection.backup` — https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup
- SQLite：SQLite Over a Network, Caveats and Considerations — https://sqlite.org/useovernet.html
- Docker Desktop：Settings（Disk image location：可把磁盘映像移到其他位置）— https://docs.docker.com/desktop/settings-and-maintenance/settings/
- Zotero：Advanced preferences（Data Directory Location；云同步文件夹和网络驱动器可能导致数据库损坏）— https://www.zotero.org/support/preferences/advanced
- supervisord：Configuration（autorestart、startsecs、startretries）— https://supervisord.org/configuration.html
- Tauri：Updater 插件 — https://v2.tauri.app/plugin/updater/
- Firefox 降级保护说明 — https://its.forsythtech.edu/support/solutions/articles/10000049967
- Immich：Backup and Restore（自动备份默认值、3-2-1）— https://docs.immich.app/administration/backup-and-restore
- 程序自行下载的更新不带隔离标记 — https://flaviocopes.com/mac-app-updates.md
- 魔搭社区：unsloth/Qwen3.5-9B-GGUF — https://www.modelscope.cn/models/unsloth/Qwen3.5-9B-GGUF
- MindNLP：使用镜像下载模型（hf-mirror、ModelScope）— https://mindnlp.readthedocs.io/tutorials/use_mirror/
- APT：apt-transport-mirror（按优先级尝试镜像、失败自动换下一个）— https://www.mankier.com/1/apt-transport-mirror
- curl：`--speed-time`（默认 30 秒，速度下限默认 1 字节/秒）— https://daniel.haxx.se/blog/?p=14009 ；https://fuchsia.googlesource.com/third_party/curl/+/refs/heads/main/docs/cmdline-opts/speed-time.md
- GitHub：About READMEs — https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes
- Obsidian：obsidian-releases README — https://raw.githubusercontent.com/obsidianmd/obsidian-releases/master/README.md
- LM Studio：lmstudio-bug-tracker — https://github.com/lmstudio-ai/lmstudio-bug-tracker
- huggingface_hub：Manage the cache（`blobs/` 以哈希命名，`snapshots/<提交>/` 是指向它们的链接）— https://huggingface.co/docs/huggingface_hub/guides/manage-cache
- llama.cpp：`llama-server` README，Using multiple models（多文件模型放子文件夹、mmproj 文件名、分片）— https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- LM Studio：`lms import`（默认移动，`--copy`、`--hard-link`、`--symbolic-link`）— https://lmstudio.ai/docs/cli/local-models/import.md
- LM Studio：Import Models（`models/<发布者>/<模型>/<文件>.gguf`）— https://lmstudio.ai/docs/app/advanced/import-model
- Python：`os.walk`（默认不进入指向文件夹的链接）— https://docs.python.org/3/library/os.html#os.walk
- VS Code：Extension Marketplace（`@builtin`：extensions that come with VS Code）— https://code.visualstudio.com/docs/configure/extensions/extension-marketplace
- Positron：Extensions（Included extensions：Built-in、Bootstrapped）— https://positron.posit.co/extensions.html
- PaddleOCR：表格结构识别模块（SLANet_plus）— https://www.paddleocr.ai/latest/version3.x/module_usage/table_structure_recognition.html
- PaddlePaddle：PP-OCRv6 模型（Hugging Face）— https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_det
- 微软：Job Objects — https://learn.microsoft.com/windows/win32/procthread/job-objects
- Jupyter Server：Security — https://jupyter-server.readthedocs.io/en/latest/operators/security.html
- MCP 规范：Transports（Security Warning）— https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- OWASP：CSRF Prevention Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html
- Flathub：Requirements（应用 ID）— https://docs.flathub.org/docs/for-app-authors/requirements
- Apple：Developer ID — https://developer.apple.com/developer-id
- macOS Sequoia 取消 Control 点按绕过（Michael Tsai 汇总 Apple 公告）— https://mjtsai.com/blog/2024/07/05/sequoia-removes-gatekeeper-contextual-menu-override
- AppleInsider：macOS Sequoia 的运行时保护变化 — https://appleinsider.com/inside/macos-sequoia/tips/whats-changed-in-runtime-protection-for-macos-sequoia
- 微软：Code signing options — https://learn.microsoft.com/windows/apps/package-and-deploy/code-signing-options
- 微软：SmartScreen reputation — https://learn.microsoft.com/windows/apps/package-and-deploy/smartscreen-reputation
- GitHub：macOS 14 runner image retirement — https://github.blog/changelog/2026-10-01-github-actions-macos-14-runner-image-retirement
- Python 讨论：Dropping Intel Mac to Tier 2 — https://discuss.python.org/t/dropping-intel-mac-to-tier-2/102100
- Apache License 2.0 — https://www.apache.org/licenses/LICENSE-2.0
- pip-licenses — https://pypi.org/project/pip-licenses/
- license-checker-rseidelsohn — https://www.npmjs.com/package/license-checker-rseidelsohn
- cargo-about — https://embarkstudios.github.io/cargo-about
- Qwen3.5-9B — https://huggingface.co/Qwen/Qwen3.5-9B ；GGUF — https://huggingface.co/unsloth/Qwen3.5-9B-GGUF
- Tauri：网页中的下载在 macOS 上不生效 — https://github.com/tauri-apps/tauri/issues/4633
- react-dropzone：Using with Tauri（`dragDropEnabled`）— https://react-dropzone.js.org/guide/tauri/
- wry：`runOpenPanelWithParameters`（`setCanChooseDirectories`）— https://github.com/tauri-apps/wry/blob/dev/src/wkwebview/class/wry_web_view_ui_delegate.rs
- platformdirs — https://pypi.org/project/platformdirs/
- PyInstaller：Using Spec Files（datas、hiddenimports）— https://pyinstaller.org/en/stable/spec-files.html
- opencv-python：PyPI 项目页（FFmpeg 许可说明、Manual builds：ENABLE_HEADLESS、CMAKE_ARGS）— https://pypi.org/project/opencv-python/
- opencv-python：维护者建议不带 FFmpeg 构建以避开随附库的许可问题 — https://github.com/opencv/opencv-python/issues/1097
- FFmpeg：License and Legal Considerations（用到 GPL 部分时整个 FFmpeg 适用 GPL；LGPL 合规清单要求不用 `--enable-gpl`、`--enable-nonfree`，不用 libx264 等 GPL 库）— https://www.ffmpeg.org/legal.html
- GCC Runtime Library Exception 3.1 — https://www.gnu.org/licenses/gcc-exception-3.1.html
- cargo-about：Configuration（targets、accepted、private）— https://embarkstudios.github.io/cargo-about/cli/generate/config.html
- tauri-action（v1 输入：projectPath、tauriScript、tagName、releaseDraft、uploadUpdaterJson、uploadWorkflowArtifacts）— https://github.com/tauri-apps/tauri-action
- astral-sh/setup-uv（按提交固定版本）— https://github.com/astral-sh/setup-uv
