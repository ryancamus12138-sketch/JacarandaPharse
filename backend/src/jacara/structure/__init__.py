"""Page structure analysis: organizes one page of OCR results into "text blocks + relationships",
modeled on the AWS Textract block structure.

- WORD: one text block (an OCR block or a PDF text-layer span) with position and confidence,
  numbered 1, 2, 3, … in reading order
- LINE: one visual line, CHILD → the text blocks of the line
- LAYOUT: a layout region (title, text, table, header, footer, …) found by the layout model,
  CHILD → the text blocks inside
- KEY / VALUE: a label → value pair (Textract KEY_VALUE_SET); KEY has a VALUE relationship to its
  value, and both have CHILD → text blocks
- TABLE / CELL / MERGED_CELL: tables, cells (row, column, row and column span), merged cells

During extraction the model sees the indexed text blocks (laid out as on the page, LAPDoc)
together with the form and table structure; it answers with "value + blocks used" (LMDX), which
evidence alignment verifies."""
