"""页面结构分析（ADR 0034）：参照 AWS Textract 的结构，把一页 OCR 结果整理成"文字块 + 关系"。

- WORD：一个文字块（OCR 的一块、PDF 文字层的一段），带位置、置信度，按阅读顺序编号 1、2、3……
- LINE：一个视觉行，CHILD → 这一行的文字块
- LAYOUT：版面区域（标题、正文、表格、页眉、页脚……），版面模型找出来，CHILD → 区域里的文字块
- KEY / VALUE：一对"标题 → 值"（Textract 的 KEY_VALUE_SET），KEY 有 VALUE 关系指向它的值，
  两边都 CHILD → 文字块
- TABLE / CELL / MERGED_CELL：表格、格子（行号、列号、跨几行几列）、合并的格子

抽取时，模型看到的是带编号的文字块（按原样排版，LAPDoc），以及表单和表格的结构；模型回答
"值 + 用了哪几块"（LMDX），由证据对齐核对。
"""
