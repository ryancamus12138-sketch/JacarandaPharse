# 0060 OCR 模型评测：在数据集上比较两个识别（或检测）模型

状态：已采纳（2026-11）。完成 ADR 0050 第二批里的"用新旧识别模型重认验证集比较整行准确率（`jacara eval ocr`）"。

## 为什么

在系统外训好的识别模型放进模型库（ADR 0059）以后，要先证明它比现在用的好，再换上去。
回放（ADR 0053）比的是抽取结果，OCR 换了模型时差别混在一起；这里只比 OCR 本身。

## 怎么跑

- 命令行：`jacara eval ocr --dataset <名字>[@版本] --split val --a 在用 --b <模型名>@v<版本>`，
  结果写到 `results/eval/ocr-<时间>/`（和别的评测一样不进 Git）。
- 网页：模型库里点"评测"，选数据集、哪一边、两个模型（A 默认是在用的）。放进后台队列（OCR 工位的队列），
  不卡网页；结果存表 `ocr_eval_runs`，每行的结果存对象存储（`ocr-evals/<团队>/<id>.jsonl`，是业务数据）。
  CLAUDE.md 里"评测只在开发时用命令行"改成：抽取评测仍只在命令行，OCR 模型评测网页上也能点（这一条）。
- 只读：不改模型选择、不改数据集。比完由人决定换不换。
- 权限：开始评测要"修改和确认字段"（`value:review`，和数据集的导入一样）；看结果、下载逐条结果要"导出"
  （`document:export`）。开始评测记审计日志（`hub:eval`），记下两边模型文件的 SHA-256。

## 识别

- 每张小图只跑识别（`use_det=False`，和"重新识别这块"同一条路）。
- 指标照 TextRecMetric（RapidOCR 作者 SWHL 的识别评测工具，Apache-2.0，https://github.com/SWHL/TextRecMetric）：
  - **Exact Match**：整行和标注一模一样的比例；
  - **Char Match**：1 − 平均归一化编辑距离，每行 `NL = Levenshtein(预测, 标注) / max(len(预测), len(标注))`。
  - 另外报"只差空格、大小写、全角半角"的行数（ADR 0050 的错误标签 `format`），不算进 Exact Match。
- 两个模型逐行配对：B 对、A 错的是"修好"，反过来是"弄坏"，用精确 McNemar 检验（和回放一样，ADR 0053）。
- 按业务单据分组 bootstrap 算 Exact Match 差的 95% 区间：本系统导出的数据集用 `manifest.jsonl` 里的
  `document_group`；外部数据集没有组号，每张图自成一组（区间会偏窄，结果里写明）。
- 证据等级沿用 ADR 0052 的门槛（少于 10 组或 50 行"只能检查流程"，15 组、100 行以上"可以看趋势"）。

## 检测

- 每张整页图只跑检测（`use_det=True, use_rec=False`）。
- 按 PaddleOCR `ppocr/metrics/eval_det_iou.py` 的 `DetectionIoUEvaluator`（默认 `iou_constraint=0.5`、
  `area_precision_constraint=0.5`）：
  - 标注框和检测框 IoU 大于 0.5、两边都还没配上，配成一对；
  - 标注是 `###` 的框不计入召回；检测框和这种框的交集超过检测框面积一半的，也不计入精确率；
  - 不合法的多边形跳过；Precision、Recall、H-mean 按所有图的总数算（`combine_results`）。
  - 多边形用 shapely 算（已有依赖，BSD）。
- 两个模型逐框比：标注框 B 配上、A 没配上的是"修好"，反过来是"弄坏"，同样做精确 McNemar。

## 不做

- 不在系统里训练（ADR 0055）。
- 不做"检测 + 识别"端到端的指标：PaddleOCR 的检测、识别也是分开评测的。
