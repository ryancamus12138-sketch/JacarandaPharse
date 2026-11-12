# 0034：按文字块理解页面（Textract 的结构 + LMDX 的核对 + LAPDoc 的排版）

- 状态：已采纳
- 日期：2026-11-12

## 背景

到 v3 第 1 批为止，给模型的是"一行一行的文字 + 行号"，模型回答"值在第几行"，系统再拿值回那一行里找出处。
用户拿酒店账单试出的问题：

- "上面一行标题、下面按列写值"的排版（Hotel Details / Check In / Check Out / Rooms）拼成一行一行的文字后，
  "1 在 Rooms 下面"这层关系没了，模型分不清房间数和晚数；
- 一行里有好几个"1"时，按行号回去找也分不清是哪一个；
- 用户的要求：不按行，按单据上具体的文字块认，再填进对应的字段。

调研（2026-11-12）：

| 做法 | 要点 | 用在哪里 |
|---|---|---|
| AWS Textract | 版面模型认出 LAYOUT（标题、正文、表格……）、TABLE / CELL / MERGED_CELL、KEY_VALUE_SET（KEY → VALUE），每块带坐标和 CHILD 关系 | 页面结构的数据结构 |
| Google LMDX（2023） | 每段文字带标识，模型回答值时把标识抄回来；按标识回原文核对，对不上的值丢掉；出处框就是那几段 | 文字块编号、核对、丢弃 |
| LAPDoc（2024） | 几种把版面写成文字的办法里，用空格按原样排开（SpatialFormat）效果最好，对 OCR 顺序乱也最稳 | 给模型看的文字 |
| LayTextLLM（2024） | 坐标直接写成数字，提示词长 2.6～6 倍，7B 模型基本看不懂 | 所以不把坐标数字塞给模型 |

用户决定：以 Textract 的结构为基础，加上 LMDX、LAPDoc 两个改进，不简化它们的方法，适应我们的系统。

## 决定

### 1. 页面结构（`backend/src/jacara/structure/`，照 Textract 的 Block）

| 块 | 含义 | 怎么来 |
|---|---|---|
| WORD | 一个文字块（OCR 的一块、PDF 文字层的一段），按阅读顺序编号 1、2、3…… | OCR / PDF 文字层 |
| LINE | 视觉行，CHILD → WORD | 按位置合并（`plugins/ocr/layout.py`） |
| LAYOUT | 版面区域：正文、标题、图、图注、表格、表注、页眉、页脚、参考文献、公式 | 版面模型 pp_layout_cdla |
| TABLE / CELL / MERGED_CELL | 表格；每个位置一个 CELL（行、列）；跨行跨列的是 MERGED_CELL，CHILD → 它盖住的 CELL | 表格结构模型 SLANet-plus；没有模型时按文字位置划分 |
| KEY / VALUE | 一对"标题 → 值"，KEY 的 attrs.value 指向 VALUE，两边 CHILD → WORD | 版面规则（见下） |

每块都有坐标、置信度。整页结构存在 `extractions.structure`（JSON），审核页"版面结构"画出来，试跑时直接用。

**模型**（都是本地 ONNX，不联网；两个库默认会下载缺的模型，这里都把路径写死成本地文件）：

- 版面：rapid-layout 自带的 `layout_cdla.onnx`（PaddleOCR PicoDet，CDLA 数据集，Apache-2.0）。
  rapid-layout 里的 YOLOv8 版面模型权重是 AGPL，不用。
- 表格：rapid-table + `models/table/slanet-plus.onnx`（PaddleX SLANet-plus，Apache-2.0，约 7 MB），
  用户自己下载一次（README）。没有就按规则：每个视觉行一行，文字块在横向上的投影连成几段就是几列。
- rapid-layout、rapid-table 声明依赖带界面的 opencv-python，和 rapidocr 用的 opencv-python-headless 冲突，
  `pyproject.toml` 里用 `override-dependencies` 去掉，统一用 headless。

**KEY / VALUE**：Textract 用训练好的模型。能离线、能商用的同类模型（LayoutXLM 系列）许可证不允许商用，
所以按版面规则找，按优先顺序（同一块文字只当一次值）：

1. 同一块里"标题：值"（"Date: 31/12/2017"）——标题里不能有数字（"8:13" 不算）
2. 冒号结尾的标题，值在右边（空隙不超过页宽 35%）或正下方；值折行时接着往下拿
3. 上面一行是几个标题（每块有字、没有数字，至少 2 块），下面紧挨着按列写值（左边对齐或中心落在标题范围里，
   至少 2 列对上、六成以上的块对上）；下面"接着上一行写"的行接到对应那一栏（ADR 0033）
4. 两列的表格、左边一列是标题（第一行两格都是标题的，是普通表格，不算）
5. 同一行只有两块：左边像标题、右边有数字（"Grand Total  Rs 2539"）

表格里的字（第 4 条除外）不参与"标题 → 值"。这些只是给模型的线索，哪个值填哪个字段由模型决定。

### 2. 给模型看的文字（LAPDoc + LMDX，提示词 extract-v6、discover-v3）

```
      ⟨5⟩Hotel Details                                  ⟨6⟩Check In         ⟨7⟩Check Out        ⟨8⟩Rooms
      ⟨9⟩Sample Resort Garden,                          ⟨10⟩31/12/2017      ⟨11⟩01/01/2018      ⟨12⟩1
      ⟨13⟩12 Example Street, Springfield

版面区域：页眉 ⟨1⟩；表格 ⟨14⟩～⟨22⟩
标题 → 值（按位置找出来的，供参考）：
- ⟨8⟩Rooms → ⟨12⟩1
表格 1（3 行 × 3 列）：
第 2 行 | ⟨17⟩Room Charges Deluxe | ⟨18⟩Rs 1939 x 1 Night | ⟨19⟩Rs 1939 |
```

- 版面原样：页面宽度换算成 120 个字符，每块按横向位置用空格排开；上下空得多的地方空一行。
- 每块前面 `⟨编号⟩`；不写坐标数字（LayTextLLM 的结论）。
- 结构部分：版面区域（不列"正文"）、标题 → 值、表格（每格的编号，合并的格子写"跨几行几列"，被盖住的写"〃"）。
- 模型回答每个值 `{"value", "blocks": [编号]}`，表格每格也一样；不再给行号。行号由编号推出来（存 row_no）。
- 模型照老格式回答（只有 row）也能读：按行号当线索、按文字去找（和 extract-v5 一样）。

### 3. 核对（LMDX，`plugins/aligners/blocks.py`）

1. 模型说的那几块里有这个值（文字相似度 ≥ 0.85；数字、日期必须完全一致）→ 出处就是这几块；
   一块里只有一部分是值，框只框那一段（ADR 0033）。
2. 那几块里没有，或者模型没说 → 按文字在整页找：找到了留着、标黄，说明"模型说的那几块里没有，在别处找到了"。
3. 整页都找不到 → 按 LMDX 丢掉：`extracted_values.value` 存空，模型给的值存在 `rejected_value`，
   状态 `rejected`，标红；规则校验也当作没有。审核时"采用模型给的值"一键填回去（要人核对原图）。

LMDX 原文还每块采样 16 次投票；本地 9B 模型一页跑 16 遍太慢，不做。

## 影响

- 处理一页多两步：画页面图（PDF 也要画）、跑版面模型（CPU 上约 0.1 秒）；有表格模型时每张表再跑一次。
- 提示词更长（每块多了编号和空格），模型回答也多了 blocks。
- extract-v6 的评测要重跑；之前的数字是 v4 的。
- 迁移 `e6f2c8a1d4b7`：`extractions.structure`、`extracted_values.rejected_value`。
- 新接口 `GET /documents/{id}/pages/{n}/structure`；系统信息页显示版面模型、表格模型。
- 设置：`JACARA_LAYOUT=false` 关掉版面模型；`JACARA_TABLE_MODEL` 指定别的表格模型文件。

## 没做的

- KEY / VALUE 用规则，不是训练的模型（许可证原因，见上）。
- 不做多次采样投票。
