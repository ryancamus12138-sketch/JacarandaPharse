# 0027：审核工具：从原文选取、框选重新识别、表格增删行

- 状态：已采纳
- 日期：2026-11-11

## 背景

商品明细加上之后，一张收据常有几十格要核对。只能手打改值太慢；漏了的行没法补，多出来的行没法删；
OCR 认错的地方（9.99 认成 666）手打之外没有办法。参考了 UiPath（点词填值、框选、行菜单）、
Kofax（在选中行后插入）、Rossum（按住 Alt 显示候选）、ABBYY（重新抽取）的做法。

## 决定

1. **保存每一页的 OCR 结果**（`extractions.ocr`，JSON：每个文字块的文字、外接框、四个角、置信度、所在视觉行）。
   `GET /documents/{id}/pages/{n}/ocr` 读取。之前处理的文档没有，这些功能不可用（页面上说明）。
2. **从原文选取**：审核时 `source_lines`（文字块编号）代替手打的值。后端从文字块读出值
   （数字取最后一个数，"6.00-" 读成 −6；日期按 日/月/年 读，`core/textvalue.py`），
   新出处就是这几块，记在 `value_revisions.evidence`。原因可以不写，默认"从原文选取"。
3. **框选**：在原图上拖出、拖动、拉伸一个框。两种用法：
   - 用框里的文字：中心落在框里的文字块，走第 2 条；
   - 重新识别这块：`POST /documents/{id}/pages/{n}/ocr-region` 把这块裁下来（四周多留 6 像素）、
     放大到至少 96 像素高，再 OCR 一次，只返回文字不保存；人确认（可以改）后用 `source_box` 保存，
     出处就是这个框（`drawn: true`，置信度记 1.0 表示人确认过）。API 进程里的 OCR 引擎第一次用到时才加载。
4. **当前出处** `current_evidence` = 最近一次带出处的审核记录，否则是抽取时的出处。原图上画的是它；导出也用它。
5. **表格增删行**：
   - 删除 = 这一行每一格新增一条 `action="delete"` 的记录；恢复 = `restore`。不真的删，原因必填。
     页面上删掉的行留在原位：灰色、划掉、不占行号，菜单里可以恢复。
   - 增加 = 新增 `origin="manual"` 的空字段，名字用比现有都大的行号（不和任何一行重名）。
   - 显示顺序看新列 `sort_key`：插在两行中间的新行取两边的中间数，原有的字段不用改（写入后不改，项目规则 2）。
   - `POST /extractions/{id}/tables/{table}/rows`（`after`：插在哪行后面，0 是最前面，不写加在最后），
     `.../rows/{index}/delete`、`.../rows/{index}/restore`。都记审计日志。
6. **规则按当前值重新检查**（`pipeline/current.py`）：跳过删掉的行，表格按显示顺序重新编号，
   接口多返回 `current_checks`，审核页面显示它；导出也按它标黄、写原因。抽取时的 `checks` 不变。
7. **快捷键**：按住 Alt（Option）显示原文文字块；Shift 或 ⌘/Ctrl 点多选、Enter 填入；
   Delete 清空选中的字段（问原因）；⌘/Ctrl + Enter 在选中的这一行下面插一行；Esc 取消。

8. **批量确认**：`POST /values/confirm`（ids、原因可不写，默认"批量确认"）。每个字段各记一条 confirm，
   审计日志只记一条（带全部 id）。页面上方确认整页标黄的、表头确认整张表、行菜单确认一行，都要再点一次确认。
9. **补 OCR 结果**：`jacara backfill-ocr` 给更新前处理的文档重新 OCR 原始文件、只补 `extractions.ocr`，
   抽取结果和审核记录不动。`ocr` 列没有结果时存 SQL NULL（`none_as_null`），才能查出要补的。
10. **RapidOCR 的开关每次都写明**（找字、转方向、认字）：它会记住上一次传的值，
    "整块当一行识别"关掉过找字之后，整页识别也会跟着不找字。

## 不做（以后再说）

- 选一块区域让模型重新抽取整张表格（UiPath 的"从这里重新抽取"）。
- 增加模板里没有的字段：属于"通用模式 / 另存为模板"。

## 影响

- 一条迁移：`value_revisions.evidence`、`extracted_values.origin` / `sort_key`（已有的 = position）、`extractions.ocr`。
- 接口只加不删：`ValueOut` 加 `current_evidence`、`origin`、`deleted`；`RevisionIn` 加 `source_lines`、`source_box`；
  `ExtractionOut` 加 `current_checks`；`DocumentReport` 加 `ocr_page`。
