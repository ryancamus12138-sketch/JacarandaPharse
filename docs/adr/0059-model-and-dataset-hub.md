# 0059 模型和数据集库：在网页上导入、导出、换模型和数据集

状态：已采纳（2026-11）。0056～0058 是已放弃的 Agent 化方案用过的编号，不再使用。

## 为什么

ADR 0046 的"换模型"只能在 `models/` 里已有的文件中选：放文件要开终端，版本靠文件名区分，
换错了说不清以前用的是哪一个。训练数据（OCR 样本导出、检测样本、在外面标好的数据）只能下载成 zip，
系统里不留，评测时也找不到同一份。

## 参照（只取概念，不引入这两个软件：它们要联网或要单独的服务，违反规则 1）

- **Hugging Face Hub**：一个模型一个仓库，仓库里的 `README.md` 是模型卡（model card），
  顶上的 YAML 写元数据（`license`、`library_name`、`base_model`、`datasets`），评测结果放在 `model-index` 里；
  数据集同样有数据集卡（https://huggingface.co/docs/hub/model-cards）。
- **MLflow 模型注册表**：注册模型（registered model）有唯一的名字，里面是一个个版本（model version），
  同名再注册版本号加 1；别名（alias，例如 `champion`）是指向某个版本的可变引用，部署时靠改别名切换版本，
  不改用它的代码（https://mlflow.org/docs/latest/ml/model-registry）。

## 模型库

- **位置**：`models/hub/<模型名>/v<版本号>/`。和 ADR 0046 一样在 `models/` 下：不进仓库、每台电脑自己的，
  备份（ADR 0054）不含模型文件。
- **一个版本** = 权重文件 + 模型卡 `README.md`。YAML 头：`kind`、`files`（文件名、SHA-256、大小）、
  `base_model`、`datasets`、`license`、`imported_at`、`imported_by`；正文是说明。
- **kind** 就是 ADR 0046 的槽位：`model`（大模型 GGUF）、`mmproj`（视觉模型 GGUF）、`ocr_det`、`ocr_rec`、
  `table`（都是 ONNX）。识别模型的字典（`.txt`）和模型放在同一个版本里。
- **导入**：网页上拖文件，或命令行 `jacara models import`（从 U 盘之类拷来的，权重离线导入）。
  先写进 `models/hub/.incoming/`，边写边算 SHA-256，检查都通过才改名成 `v<N>`（同一个文件系统里改名是原子的，
  不会出现半个版本）。检查：
  - GGUF：文件开头 4 个字节是 `GGUF`，紧接着的 4 字节版本号是规范里有的 1～3（GGUF 规范，ggml 仓库
    `docs/gguf.md`：v2 把长度改成 64 位，v3 加了大端支持）。只是确认文件对，能不能跑以 llama-server 加载为准。
  - ONNX：onnxruntime 能加载（只用 `CPUExecutionProvider`）；输入是 4 维图片（NCHW），识别模型输出 3 维、
    检测模型输出 4 维（PP-OCR 的检测、识别模型都是这样），选错种类时拒绝。
  - 识别模型：输出最后一维（类别数）必须等于 字符表行数 + 2。RapidOCR 3.10 的 `CTCLabelDecode`
    在字符表最前面插 `blank`、最后加一个空格（`rapidocr/ch_ppocr_rec/utils.py`）。
    字符表先用模型自带的（ONNX 元数据里的 `character`，RapidOCR 有它就只用它，见 `ch_ppocr_rec/main.py`），
    没有就必须一起传字典。两样都没有拒绝：RapidOCR 找不到字典会去网上下载，违反规则 1。
  - 只收 ONNX 和 GGUF。PaddleOCR 训练出来的 Paddle 推理模型，在系统外用 PaddleOCR 官方的
    `paddlex --paddle2onnx` 转成 ONNX（PaddleOCR 文档"Obtaining ONNX Models"），系统里不装 PaddlePaddle。
  - 磁盘剩余空间不到文件大小的 1.1 倍，先拒绝。
- **版本**：同名再导入版本号加 1，种类要和以前的版本一样（别的种类换个名字）。版本只读，不覆盖文件；
  不在用的版本可以删除。
- **在用**（相当于 MLflow 的别名）：就是 ADR 0046 的 `models/selected.json`，不另存一份。
  模型库里的每个版本都出现在"设置 → 模型"（换模型）的下拉里，在那里选上就是用它；换回旧版本就是选旧版本。
  选用的入口只有设置里这一个（ADR 0063）；模型库页只显示哪个版本"在用"。
  `.env` 里写了的仍然优先，生效方式也不变（`jacara stop`、`jacara start`）。
- **导出**：一个版本打包成 zip（权重 + 模型卡），拿到另一台电脑导入。
- **权限**：大家都能看；导入、删除、换只有管理员（`system:manage`），都记审计日志。
  （`POST /hub/models/{名字}/versions/{版本}/use` 接口留着，接口只加不删，规则 9；网页上不再用它。）
  模型是整台电脑的（和 `selected.json` 一样），不按团队分。

## 数据集库

- 数据集是业务数据（单据上的图和文字）：zip 存对象存储（`datasets/<团队>/<id>.zip`），表 `datasets` 存信息，
  按团队过滤，备份里有它。
- **一个数据集**：名字、版本（同名加 1）、`kind`（`ocr_rec` 识别 / `ocr_det` 检测）、来源（`samples`：OCR 样本
  导出；`det_pages`：检测样本，ADR 0061；`upload`：上传的）、说明（数据集卡）、每边几条、SHA-256、大小。
- **导入**（上传 zip），认 PaddleOCR / PaddleX 的格式：
  - 识别：标注文件每行 `图片路径<Tab>文字`（PaddleOCR `SimpleDataSet`）。认这些文件名：
    `rec_gt_train.txt` / `rec_gt_val.txt` / `rec_gt_test.txt`（本系统导出的）、`train.txt` / `val.txt`
    （PaddleX 文字识别数据集）、`rec_gt.txt`（PPOCRLabel 导出的，不分边）。
  - 检测：每行 `图片路径<Tab>[{"transcription": …, "points": [[x, y] × 4]}]`，文字是 `###` 的框不参与。
    认 `det_gt_train.txt` / `det_gt_val.txt` / `det_gt_test.txt`（PaddleOCR `gen_ocr_train_val_test.py` 的输出）、
    `train.txt` / `val.txt`（PaddleX 文字检测数据集）、`Label.txt`（PPOCRLabel，不分边）。
    （格式见 PaddleX 文档"文本检测/文本识别 标注"。）
  - 安全检查：zip 里的路径不能是绝对路径、不能带 `..`（zip slip）；解压后总大小和压缩比有上限（zip 炸弹）；
    每张图要能打开；格式不对的行计数报告，一行有效的都没有就拒绝。
- **从系统里来的**：OCR 样本页的导出可以直接"存进数据集库"（同时照旧记导出记录，ADR 0052 的隔离规则照常）；
  检测样本同样（ADR 0061）。
- **导出** = 下载存着的 zip。删除记审计日志。
- **权限**：看和下载要"导出"（`document:export`，和样本导出一样）；导入、删除要"修改和确认字段"
  （`value:review`，和样本页的操作一样）。

## 不做

- 训练还在系统外（ADR 0055 不变）：模型库只管放进来、选上、比较（ADR 0060）和导出。
- 不从网上下载模型或数据集。
