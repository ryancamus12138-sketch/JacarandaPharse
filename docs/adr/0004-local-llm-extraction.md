# 0004 本地大模型抽取：llama-server + Qwen3.5，JSON Schema 约束输出

- 状态：已采纳
- 日期：2026-11-03

## 背景

OCR 只给出一行一行的文字，不知道哪个是总额、哪个是日期。需要一个本地大模型把文字变成字段。
要求：完全离线；不按 token 收费；普通电脑（16GB 内存、无独显）也能跑；以后能换模型。

## 决策

**运行方式：** 用 llama.cpp 自带的 llama-server，通过本机 HTTP 接口（OpenAI 格式的
`/v1/chat/completions`）调用。只监听 `127.0.0.1`。

**模型：** 开发默认 Qwen3.5-9B（Unsloth 的 GGUF，Q4_K_M 量化，约 5.3GB）。
低配档用 Qwen3.5-4B。Qwen3.6 目前只有 27B 及以上，留作以后的高配档对比。上线前在模型页确认授权。

**保证输出格式：**

- 字段清单（`FieldSpec`）自动生成 JSON Schema，交给 llama-server 的 `response_format`，
  在生成时强制格式：数字字段只能是数字，日期只能是 `YYYY-MM-DD`，找不到可以填 null。
- 提示词规定"只根据原文、找不到填 null、文字照抄原文（含 OCR 错字）"。照抄是为了后面能在原文里找到出处。
- temperature 默认 0，同一输入结果稳定；默认关闭思考模式。

**可替换的接口：**

- `plugins/model_backends/`：`ModelBackend` 接口 + 登记表，换服务（例如 vLLM）只需新增一个文件。
- `plugins/extractors/`：`Extractor` 接口，`LlmExtractor` 通过依赖注入使用任意模型后端。
- 每次抽取记录：模型名（llama-server 的 `--alias`，即去掉 .gguf 的文件名）、后端、提示词版本、
  用时、token 数，以及模型的原始回答 `raw_output`。

**启动由程序负责：** `jacara serve --model 文件` 统一处理启动参数和环境变量，用户不需要记。

## 已知问题

- macOS 14、15（已知 15.6.1）上，Homebrew 安装的新版 llama.cpp 的 Metal 程序编译失败
  （llama.cpp issue #31272）。`server.py` 在 Mac 上自动设置 `GGML_METAL_BF16_DISABLE=1` 绕过。
  官方修复后删除。

## 第一次试用（SROIE 第 0 张收据，M 系列 Mac）

4 个字段全部正确：商家照抄了 OCR 错字，日期转成 `2018-12-25`，总额为数字 9.0。抽取约 2 秒。

## 放弃的方案

- **云端 API（OpenAI、Claude 等）**：违反离线要求，财务数据会离开本机，并按 token 收费。
- **Ollama**：内部也是 llama.cpp，多一层封装；llama-server 直接支持 JSON Schema 约束，够用。
- **只靠提示词要求输出 JSON**：模型可能多说话或格式不一致，程序无法稳定解析。

## 后果

- 抽取完全离线、免费，输出格式稳定，可直接给后面的证据对齐和规则校验使用。
- llama.cpp 不能随系统更新：离线安装包要锁定一个测试过的版本，升级前先验证。
- 需要重新评估的情况：9B 在中文发票上准确率不足、多页长文档超出上下文，或需要多人同时使用（考虑 vLLM）。
