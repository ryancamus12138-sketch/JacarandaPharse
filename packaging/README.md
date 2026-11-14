# JacarandaParse

JacarandaParse 是一款离线运行的文档结构化工具，用于从收据、发票等 PDF 和图片中抽取结构化字段，
并提供人工审核界面。每个抽取结果均可追溯到原文中的出处；无法定位出处的值会被标记，交由人工确认。

## 功能

- 文字识别：基于 PP-OCR 模型（RapidOCR / ONNX Runtime），支持图片与 PDF。
- 字段抽取：由本机运行的大语言模型（llama.cpp）按模板抽取字段，支持表格明细。
- 出处定位与规则校验：在原文中定位每个值的位置，并按模板规则校验。
- 人工审核：在原图上查看出处，修改或确认字段，保留完整修改记录。
- 导出：Excel（单文件或汇总）、审核数据与训练数据。
- 模型与数据集管理：导入 OCR 模型和数据集，对比不同模型的识别效果。

## 数据与隐私

所有服务仅监听本机（127.0.0.1）。运行期间不访问外部网络，文档与数据只保存在本机。
首次安装时需要联网下载依赖、数据库镜像和模型文件。

## 系统要求

- macOS（推荐 Apple 芯片），或 Linux
- 内存 16 GB 及以上，可用磁盘空间 20 GB 及以上
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [uv](https://docs.astral.sh/uv/)（macOS：`brew install uv`）
- [llama.cpp](https://github.com/ggml-org/llama.cpp)（macOS：`brew install llama.cpp`）

## 安装

1. 从 [Releases](../../releases) 下载最新版本的 `JacarandaParse-<版本>.zip`，并解压到固定目录。
   可使用同目录下的 `.sha256` 文件校验：`shasum -a 256 JacarandaParse-<版本>.zip`。
2. 启动 Docker Desktop，在解压目录中执行：

   ```bash
   ./scripts/install.sh
   ```

   安装脚本将安装运行依赖、生成配置文件 `.env`（密钥随机生成）、启动数据库服务并初始化数据库，
   最后逐项检查运行环境，并给出缺失项的处理方法。
3. 下载大语言模型
   [Qwen3.5-9B-Q4_K_M.gguf](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf)
   （约 5.7 GB），放入 `models/` 目录。
   可选：视觉模型 [mmproj-F16.gguf](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/mmproj-F16.gguf)
   （约 918 MB）同样放入 `models/`，用于在文字识别不可靠时由模型直接读取原图。
4. 再次执行检查：

   ```bash
   uv run --frozen --no-dev jacara setup
   ```

## 使用

双击 `JacarandaParse.command`，或执行：

```bash
uv run --frozen --no-dev jacara start
```

浏览器将自动打开 <http://127.0.0.1:8000>。关闭终端窗口或按 Ctrl + C 停止全部服务。

如 macOS 提示无法打开 `JacarandaParse.command`，请执行：

```bash
xattr -d com.apple.quarantine JacarandaParse.command
```

识别与处理选项、模型选择、数据量与服务状态可在网页的“设置”中查看和修改。

## 升级

1. 在“设置 → 数据和备份”中按说明完成备份。
2. 下载新版本并解压到新目录，将旧目录中的 `.env`、`models/` 和 `data/` 复制到新目录。
3. 在新目录中执行 `./scripts/install.sh`，数据库将自动升级到新版本。

## 卸载

1. 在解压目录中执行 `docker compose down` 停止数据库服务。
2. 如需同时删除全部数据，执行 `docker compose down -v`（不可恢复）。
3. 删除解压目录。

## 许可

本软件以 [Apache License 2.0](LICENSE) 发布。模型文件遵循其各自的许可，需单独下载。
