import { t } from '../i18n'
/** Shapes of backend responses. Mirrors the backend schemas.py with identical field names. */

export interface Health {
  status: string
  version: string
}

export interface User {
  id: string
  username: string
  display_name: string
  role: string
  permissions: string[]
  is_active: boolean
  created_at: string
}

/** GET /auth/me: current user and permitted operations. */
export interface Me {
  mode: 'personal' | 'team'
  role: string
  permissions: string[]
  user: User | null
  /**
   * Name shown in the top-right corner: display name → user name; in personal mode without a display name,
   * "Local user".
   */
  shown_name?: string
  /** Time of the last avatar change (also the avatar version); null when there is no avatar. */
  avatar_at?: string | null
}

/** Profile page (GET /auth/profile). */
export interface Profile {
  mode: 'personal' | 'team'
  /** Login name. Absent in personal mode. */
  username: string | null
  /** Display name; may be empty. */
  display_name: string
  shown_name: string
  role: string
  /** Team name. Empty in personal mode. */
  team: string
  /** Permitted operations (display names). */
  permissions: string[]
  avatar_at: string | null
  member_since: string | null
}

/** Comment: a note for colleagues, attached to a whole document (field_name empty) or to a field. */
export interface DocComment {
  id: string
  document_id: string
  /** Page of the field; null for document comments. */
  page: number | null
  /** Field ID; a table cell is items.2.amount. */
  field_name: string | null
  body: string
  author: string
  /**
   * Whether the current user is the author (authors can delete their own comments; administrators can delete
   * any).
   */
  mine: boolean
  created_at: string
  resolved_at: string | null
  resolved_by: string | null
}

/** Response of POST /auth/login. The token appears only here. */
export interface LoginOut {
  token: string
  expires_at: string
  user: User
}

/** Document status: uploaded (queued) → processing → done / failed; reviewed after review. */
export type DocumentStatus = 'uploaded' | 'processing' | 'done' | 'failed' | 'reviewed'

/** One item of the document list (backend DocumentOut). */
export interface DocumentSummary {
  id: string
  filename: string
  content_type: string | null
  size_bytes: number
  page_count: number
  status: DocumentStatus
  /** Failure cause; only when failed. */
  error: string | null
  created_at: string
  /** Number of fields still awaiting confirmation. */
  review_count: number
  /** Current step, for example "Extracting page 2 / 3". Only while processing. */
  stage: string | null
  /** Processing progress 0–100. Only while processing. */
  progress: number | null
  processed_at: string | null
  reviewed_at: string | null
  /** Run whose results are currently used (incremented on each reprocess). */
  current_run?: number
  /** No template; extract all key-value pairs (the first step of creating a template from a sample). */
  discover?: boolean
  /** Automatic template selection. */
  auto?: boolean
  /** Whether the model sees the page image: always, off; null follows the system setting. */
  vision?: string | null
  /** How PDFs are read: image, text; null follows the system setting. */
  pdf_read?: string | null
  /** Time "Skip for now" was clicked during review; null if not skipped. */
  skipped_at?: string | null
  skip_reason?: string | null
  /** Number of unresolved comments. */
  open_comments?: number
  /** Source folder in a batch import. */
  folder?: string | null
  /**
   * Number of documents in a file: auto (detect), one (whole file), pages (one per page), or page ranges
   * "1-2, 3".
   */
  split?: string
  /** Earlier uploads of a file with identical content. */
  same_file?: { id: string; filename: string; created_at: string }[]
  /**
   * "Skip duplicate files" was selected for the batch import: no new document was created and the earlier one
   * is returned.
   */
  duplicate_skipped?: boolean
}

/** A batch-import folder and its document count. */
export interface FolderCount {
  folder: string
  count: number
}

/** Document list filters. All empty means everything. */
export interface DocumentFilters {
  status?: DocumentStatus[]
  template?: string
  /** YYYY-MM-DD, inclusive. */
  date_from?: string
  date_to?: string
  /** File name contains. */
  q?: string
  /** Only documents with fields awaiting confirmation. */
  pending?: boolean
  /** Field values contain (for example invoice number, merchant name). */
  value_q?: string
  /** With value_q: search only this field. */
  field?: string
  /** true: skipped only; false: exclude skipped. */
  skipped?: boolean
  /** Batch-import folder (including subfolders). */
  folder?: string
}

/** Previous / next on the review page (documents awaiting review only). */
export interface DocumentNeighbors {
  previous: string | null
  next: string | null
  /** Number of other documents awaiting review. */
  remaining: number
}

/** Dashboard (GET /dashboard). */
export interface Dashboard {
  counts: {
    queued: number
    processing: number
    to_review: number
    failed: number
    reviewed: number
    total: number
    /** Number of skipped documents awaiting review. */
    skipped?: number
    pending_fields: number
    processed_today: number
    reviewed_today: number
    average_page_seconds: number | null
    /**
     * Average seconds per step per document over the last 7 days: read (OCR), wait (queue for the model),
     * extract (model).
     */
    stage_seconds?: { read?: number; wait?: number; extract?: number } | null
  }
  /** Queued and processing documents (with progress). */
  queue: DocumentSummary[]
  failed: DocumentSummary[]
  /** Documents awaiting review with the most fields awaiting confirmation. */
  to_review: DocumentSummary[]
}

/** One export (GET /exports). */
export interface ExportRecord {
  id: string
  filename: string
  format: 'xlsx' | 'zip' | 'csv' | 'csv-zip' | 'json'
  size_bytes: number
  document_count: number
  documents: { id: string; filename: string }[]
  created_at: string
  user_name: string
  /** The file is still available for download. */
  available: boolean
}

export interface ExportPage {
  items: ExportRecord[]
  total: number
}

/** System information (GET /system). */
export interface SystemInfo {
  version: string
  mode: 'personal' | 'team'
  model: {
    server: string
    file: string | null
    loaded: string | null
    context: number | null
    /** Vision projector file (mmproj). */
    vision_file?: string | null
    /** Whether the model server can currently process images. */
    sees_images?: boolean
    /** When the page image is shown to the model: auto, always, off. */
    vision_mode?: string
  }
  ocr: { engine: string; version: string | null }
  /** Models used for page structure analysis. null: disabled. */
  structure?: { layout: string | null; table: string | null } | null
  /** Watch folder (files placed in it are imported automatically); null if not set. */
  watch_dir?: string | null
  templates: { name: string; title: string; version: number }[]
  data: {
    documents: number
    pages: number
    fields: number
    revisions: number
    document_bytes: number
    exports: number
    export_bytes: number
  }
  services: { name: string; ok: boolean; detail: string; hint: string }[]
}

/** Paginated list (backend DocumentPage). */
export interface DocumentPage {
  items: DocumentSummary[]
  total: number
}

/**
 * Evidence of a value: line number in the OCR result, the text of that line, and the box on the page image.
 */
export interface Evidence {
  /**
   * Visual line number. null for evidence selected from source text when the selected blocks belong to no
   * line.
   */
  row: number | null
  row_text: string
  /** [left, top, right, bottom] in pixels of the /pages/{n}/image image. */
  bbox: [number, number, number, number]
  /** OCR confidence of the evidence text (0–1). */
  confidence: number
  /** Similarity of value and source text (0–1); 1 means identical. */
  similarity: number
  /** Text of the evidence blocks. Absent for documents processed by older versions. */
  text?: string
  /** Source notation copied by the model; numbers, dates and date ranges only. */
  quote?: string
  /** Text blocks (indices) the model says it used. Absent for documents processed by older versions. */
  blocks?: number[]
  /**
   * Page in the file (a document may span several pages). Absent for older documents, meaning the first page
   * of the document.
   */
  page?: number
  /**
   * Result of checking against the PDF text layer: none (no text layer), absent (no text-layer characters
   * here), agree (consistent), ambiguous (unclear which line), conflict (different).
   */
  text_layer?: 'none' | 'absent' | 'agree' | 'ambiguous' | 'conflict'
}

/**
 * matched: identical value found; fuzzy: only a similar value; not_found: absent from the source; empty: the
 * model reported it absent; rejected: the model gave a value that could not be verified in the source, so it
 * was not filled.
 */
export type AlignStatus =
  | 'matched'
  | 'fuzzy'
  | 'not_found'
  | 'empty'
  | 'rejected'
  | 'computed'
  /** Read from the page image: OCR misread or missed it, so a reviewer should check it against the image. */
  | 'seen'

/**
 * Candidate values of a field: differing values from different sources, offered for selection; never applied
 * automatically.
 */
export interface Candidate {
  /** ocr: full-page OCR; text_layer: PDF text layer; zoom: zoomed re-recognition; form: PDF form field. */
  source: 'ocr' | 'text_layer' | 'zoom' | 'form'
  text: string
  /** Value normalized by field type (number, date); null if not normalizable, in which case text is used. */
  value: string | null
  page: number
  /** Box on this page image [left, top, right, bottom]. */
  box: [number, number, number, number] | null
  confidence: number | null
  /** Form fields: visible on the page / not visible on the page / visibility unconfirmed. */
  note: string
  dpi: number | null
  /**
   * Whether the rules involving this field pass with this value (a hint only); null when no rule involves the
   * field.
   */
  rule_ok: boolean | null
  /** Form fields: whether the value is visible on the page (null when unconfirmed). */
  visible?: boolean | null
}

/** A field (backend ValueOut). */
export interface FieldValue {
  id: string
  name: string
  type: FieldType
  /** Start and end of a split date range, computed by the backend from the current value. */
  range?: { start: string | null; end: string | null; days: number | null; problem: string | null } | null
  /** Value extracted by the model; never changes. */
  value: string | null
  /**
   * Value given by the model but not verifiable in the source and therefore not filled. Can be accepted after
   * checking the image.
   */
  rejected_value?: string | null
  /** Current value: the post-review value shown in the UI. */
  current_value: string | null
  status: AlignStatus
  needs_review: boolean
  /** Edited or confirmed by a person. */
  reviewed: boolean
  /** Awaiting confirmation: highlighted in the UI. */
  pending_review: boolean
  /** Why confirmation is needed, for example "value not found in the source text". */
  note: string
  /** Evidence found at extraction; never changes. */
  evidence: Evidence | null
  /** Candidates: present only for conflicts and possibly missed values. */
  candidates?: { items: Candidate[] } | null
  /**
   * Current evidence: the selected blocks if selected from source text, otherwise evidence. Drawn on the page
   * image.
   */
  current_evidence: Evidence | null
  /** model: extracted by the model; manual: row added manually during review. */
  origin: 'model' | 'manual' | 'computed'
  /** The row containing this cell was deleted (tables only). */
  deleted: boolean
  /**
   * Evidence (as found at extraction): matches the source, doubtful, not found in the source, absent from the
   * source.
   */
  evidence_state: EvidenceState
  /**
   * Source value: the value from the evidence text, present only when it differs from the current value. Used
   * by "use source value".
   */
  source_value: string | null
  /** Validation (on the current value): ok, violates a warning rule, violates an error rule. */
  check_state: CheckState | null
  /** Color: evidence × validation × reviewed. */
  level: Level | null
  /** Display name: the name in the template; in key-value discovery, the label on the document. */
  label?: string | null
  /** After reprocessing: results reviewed in the previous run that can be carried over. */
  suggestion?: Suggestion | null
}

/**
 * Carry over a review from the previous run: confirm confirms the current value; edit applies the previously
 * edited value.
 */
export interface Suggestion {
  action: 'confirm' | 'edit'
  value: string | null
  run: number
}

export type EvidenceState = 'verified' | 'pending' | 'missing' | 'empty'
export type CheckState = 'ok' | 'warn' | 'fail'
export type Level = 'red' | 'yellow' | 'grey' | 'green'

/** A failed rule, for example "cash − total = change". */
export interface CheckIssue {
  rule: string
  title: string
  message: string
  fields: string[]
  severity: 'error' | 'warning'
}

export interface Checks {
  passed: string[]
  skipped: string[]
  issues: CheckIssue[]
}

/** Extraction result of one page. */
/** Details of automatic template selection. */
export interface Classified {
  picked: { template_id: string; version_id: string; title: string; version: number } | null
  reason: string
  /** model: chosen by the model; keywords: by keyword score; none: no templates available. */
  by: 'model' | 'keywords' | 'none'
  candidates: { template_id: string; title: string; score: number }[]
}

export interface Extraction {
  id: string
  /** First page of this document. */
  page: number
  /** Last page of this document; null for a single page. */
  last_page?: number | null
  /**
   * Special conditions while reading text, for example "page 2: garbled PDF text layer; OCR used for this
   * page".
   */
  source_notes?: string[]
  source: string
  model: string
  prompt_version: string
  /**
   * Built-in template name, id of one of my templates, or discover (no template; all key-value pairs
   * extracted).
   */
  template: string
  template_version: number
  /** Template display name. */
  template_title?: string
  /** Details of automatic template selection; null when the template was chosen at upload. */
  classified?: Classified | null
  /** Why the model was shown the page image; null if it was not. */
  vision?: string | null
  run?: number
  run_reason?: string | null
  /** Field ID → display name. Table columns as items.amount, table names as items. */
  labels?: Record<string, string>
  /**
   * Languages available for field names; the first is the default. Empty when the template has a single
   * language.
   */
  languages?: string[]
  /** Labels in other languages: language code → {field ID → name}. */
  label_sets?: Record<string, Record<string, string>>
  /** Rule results at extraction time; never change. */
  checks: Checks
  /**
   * Rule results re-evaluated on the current post-review values (deleted rows excluded). Shown on the page.
   */
  current_checks: Checks | null
  values: FieldValue[]
  /** Model input of this run. null for documents processed before this was recorded. */
  sent?: SentSummary | null
}

/** Summary of the model input. */
export interface SentSummary {
  blocks: number
  total_blocks: number
  chars: number
  images: number
  lessons: number
  has_snapshot: boolean
}

/** Document text sent to the model. */
export interface SentText {
  blocks: number[]
  text: string
  sha256: string
}

/** Document details: the document plus the extraction result of each page. */
export interface DocumentDetail extends DocumentSummary {
  extractions: Extraction[]
  /** Every processed run (newest first). */
  runs?: RunInfo[]
  /** Run of extractions. Read-only when it is not current_run. */
  viewing_run?: number
  /** Number of fields whose previous review can be carried over. */
  suggestions?: number
}

/** A processed run. */
export interface RunInfo {
  run: number
  template: string
  template_title: string
  template_version: number
  reason: string | null
  created_at: string
  current: boolean
}

/** Reprocessing reason. */
export type ReprocessReason =
  | 'template_changed'
  | 'wrong_template'
  | 'bad_result'
  | 'model_changed'
  | 'batch'
  | 'other'

export const REPROCESS_REASONS: Record<ReprocessReason, string> = {
  get template_changed() { return t('模板改了') },
  get wrong_template() { return t('选错了模板') },
  get bad_result() { return t('结果不好，再跑一次') },
  get model_changed() { return t('换了模型或 OCR') },
  get batch() { return t('批量重新处理') },
  get other() { return t('其他') },
}

export type FieldType = 'string' | 'number' | 'date' | 'date_range'

/** Date range export format: uniform, source text, or two columns. */
export type RangeExport = 'normalized' | 'original' | 'columns'

/** A template field (or a table column). */
export interface TemplateField {
  /**
   * Field ID: starts with a lowercase letter; only lowercase letters, digits and underscores. Do not change
   * after saving.
   */
  name: string
  label: string
  type: FieldType
  required: boolean
  description: string
  /** Other notations on documents, passed to the model as well. */
  aliases: string[]
  /** Whether this column is included in Excel exports. */
  export: boolean
  /** Sensitive information (identity document numbers and the like); not exported by default. */
  sensitive: boolean
  /** Allowed values: warn when the extracted value is not among them. Empty means unrestricted. */
  options: string[]
  /** false = manual entry: not sent to the model; entered during review. */
  extract: boolean
  /** Formula of a computed field, for example check_out - check_in. Empty if not computed. */
  compute?: string
  /**
   * Must be unique (for example invoice numbers): warn about a possible duplicate claim when another document
   * has the same value.
   */
  unique?: boolean
  /** Date range export format; uniform format by default. */
  range_export?: RangeExport
  /** Separator between start and end in the uniform format; " ~ " by default. */
  range_separator?: string
  /** Names in other languages: language code → name. label is the default name. */
  names?: Record<string, string>
}

export interface TemplateTable {
  name: string
  label: string
  description: string
  required: boolean
  export: boolean
  columns: TemplateField[]
  /** Table name in other languages. */
  names?: Record<string, string>
}

export interface TemplateRule {
  id: string
  title: string
  needs: string[]
  severity: 'error' | 'warning'
}

/** A rule from the rule library (selectable in the editor). */
export interface RuleInfo extends TemplateRule {
  per_row: string | null
  /** The rule applies only when the template contains these fields (values may be empty). */
  requires?: string[]
  /** Country-specific rule: country code (CN, AU); empty for generic rules. */
  country: string
}

/** Custom rule: a condition such as sum(items.amount) == total. */
export interface CustomRule {
  title: string
  check: string
  severity: 'error' | 'warning'
  /** Table name: each row is checked separately, and the condition uses this table's column names. */
  per_row: string | null
}

/** mine: my templates; starter: starter library; builtin: built-in (evaluation and CLI). */
export type TemplateKind = 'mine' | 'starter' | 'builtin'

export interface Template {
  name: string
  version: number
  title: string
  /** Template name in other languages. */
  titles?: Record<string, string>
  /** Available languages; the first is the default. */
  languages?: string[]
  /** Basis of a starter template: regulations, use cases, sources. */
  basis?: string[]
  description: string
  category: string
  kind: TemplateKind
  id: string | null
  archived: boolean
  source: string
  updated_at: string | null
  fields: TemplateField[]
  tables: TemplateTable[]
  rules: TemplateRule[]
  custom_rules?: CustomRule[]
  /** Domains (invoice, transport, …); several allowed. */
  domains?: string[]
  /** Country or region code (CN, AU, …); empty means generic. */
  countries?: string[]
  /** Custom tags. */
  tags?: string[]
  /** Last time a document used it. */
  last_used_at?: string | null
  /**
   * For copies of a starter template: formulas the starter template has since added (field ID → formula) that
   * this copy lacks.
   */
  starter_formulas?: Record<string, string>
  versions: { version: number; note: string; created_at: string }[]
  used: number
  /**
   * Subjects: several documents referring to the same person or company are merged into one row by identifier
   * on export.
   */
  entities?: { name: string; label: string; key: string; fields: string[] }[]
  /** Reference tables: fixed lists used for matching and filling identifiers. */
  references?: {
    name: string
    label: string
    columns: string[]
    row_count: number
    match_field: string
    match_column: string
  }[]
  /** Layout for export to the original workbook. */
  workbook?: {
    filename: string
    sheets: {
      name: string
      role: SheetRole
      header_row: number
      same_as: string
      table: string
      entity: string
      reference: string
      columns: { index: number; header: string; ref: string }[]
    }[]
  } | null
}

export type SheetRole = 'document' | 'items' | 'entity' | 'reference' | 'options' | 'ignore'
export type ColumnSource = 'extract' | 'manual' | 'doc' | 'auto' | 'lookup' | 'none'

/** A workbook column: header, first few values, guessed source. Editable in the preview. */
export interface ExcelColumn {
  index: number
  header: string
  samples: string[]
  source: ColumnSource
  field: string
  label: string
  type: FieldType
  required: boolean
  sensitive: boolean
  description: string
  aliases: string[]
  options: string[]
  auto: string
  /** "reference sheet name::column name" */
  lookup: string
}

export interface ExcelSheet {
  name: string
  role: SheetRole
  header_row: number
  rows: number
  hidden: boolean
  same_as: string
  /**
   * Entity and reference sheets: the identifier column; line-item sheets: the column linking to the document.
   */
  key_header: string
  /** Reference sheets: "sheet name::header" of the column whose values are matched. */
  match_field: string
  reason: string
  columns: ExcelColumn[]
}

/**
 * Result of uploading a workbook: a standard template workbook (the template directly) or a business report
 * (a guess for each sheet).
 */
export interface ExcelAnalysis {
  kind: 'standard' | 'report'
  filename: string
  template: (TemplateIn & { version: number }) | null
  upload_id: string | null
  sheets: ExcelSheet[]
  starter: string
  starter_title: string
  auto_columns: Record<string, string>
}

/** Save a template (every save creates a new version). */
export interface TemplateIn {
  title: string
  titles?: Record<string, string>
  category: string
  description: string
  fields: TemplateField[]
  tables: TemplateTable[]
  rules: string[]
  custom_rules: CustomRule[]
  domains: string[]
  countries: string[]
  tags: string[]
  note: string
  /**
   * Templates created from Excel: change what a column of the original workbook is filled with. Keys are
   * "sheet name::header", values a field ID or something like period.start.
   */
  column_refs?: Record<string, string>
}

/**
 * Creating a template from a sample: one selected field. source is the key-value pair in the sample (kv.3);
 * empty for added fields.
 */
export interface SampleField extends TemplateField {
  source: string | null
}

export interface TrialResult {
  template: string
  version: number
  duration_ms: number
  pages: {
    page: number
    fields: { name: string; label: string; value: string | null; status: AlignStatus; note: string }[]
    issues: { rule: string; title: string; message: string; severity: 'error' | 'warning' }[]
    passed: string[]
  }[]
}

/** A review (POST /values/{id}/revisions). edit: change to value; confirm: the current value is correct. */
export interface RevisionIn {
  action: 'edit' | 'confirm'
  /** New value. Used only by edit; null means the field is indeed absent from the source. */
  value?: string | null
  /** Reason for the change. Required for edits (optional when selecting from source text). */
  reason: string
  /**
   * Select from source text: indices of OCR text blocks on this page (OcrLine.index). The value is parsed
   * from their text and they become the evidence.
   */
  source_lines?: number[]
  /**
   * Region selection: the region [x0, y0, x1, y1] drawn on the page image (the value confirmed after
   * re-recognition goes in value). It becomes the evidence.
   */
  source_box?: Box
  /**
   * Transcription: the text exactly as on the image. Only with a single source block or a region selection;
   * when selecting from source text, the value is parsed from it. When the team has OCR sample collection
   * enabled, an OCR sample is stored as well.
   */
  transcription?: string
  /** Page for source text or region selection (a document may span several pages). */
  page?: number
}

/** A box on the page [left, top, right, bottom], in page image pixel coordinates. */
export type Box = [number, number, number, number]

/** Result of re-recognizing a region (POST /documents/{id}/pages/{n}/ocr-region). */
export interface RegionOut {
  text: string
  confidence: number | null
}

/** A text block on the page image (GET /documents/{id}/pages/{n}/ocr). */
export interface OcrLine {
  index: number
  text: string
  /** [left, top, right, bottom], in page image pixel coordinates. */
  box: [number, number, number, number]
  polygon: [number, number][]
  confidence: number
  row: number | null
}

/** OCR result of a page. available=false: OCR results were not stored when this page was processed. */
export interface PageOcr {
  available: boolean
  lines: OcrLine[]
}

/** A review record. */
export interface Revision {
  id: string
  seq: number
  /**
   * edit, confirm, delete (delete this row), restore (restore this row), compute (recalculated by formula).
   */
  action: 'edit' | 'confirm' | 'delete' | 'restore' | 'compute'
  new_value: string | null
  reason: string
  /** New evidence when selected from source text. */
  evidence: Evidence | null
  /** Actor. */
  user_id: string
  created_at: string
}

/** Complete history of a field: model value plus every review. */
export interface ValueHistory {
  value: FieldValue
  revisions: Revision[]
}

/** A permission administrators can grant to members (GET /users/permissions). */
export interface PermissionOption {
  name: string
  label: string
}

export type Role = 'admin' | 'member'

/** Create a user (POST /users). */
export interface UserCreate {
  username: string
  password: string
  display_name: string
  role: Role
  permissions: string[]
}

/** Update a user (PATCH /users/{id}): send only the fields to change. */
export interface UserUpdate {
  role?: Role
  permissions?: string[]
  is_active?: boolean
  display_name?: string
}

/** One-time reset code generated by an administrator (shown only once). */
export interface ResetCode {
  username: string
  code: string
  expires_at: string
}

/** An audit log entry. */
export interface AuditLog {
  id: string
  created_at: string
  /** Actor. null for failed logins with an unknown user name. */
  user_id: string | null
  action: string
  target_type: string | null
  target_id: string | null
  detail: Record<string, unknown>
}

export interface AuditPage {
  items: AuditLog[]
  total: number
}

/**
 * Structure of a page: layout regions, table cells, label → value pairs. Drawn over the page image in the
 * layout view of the review page.
 */
export interface PageStructure {
  /** Absent for documents processed before 2026-11-14. */
  available: boolean
  sources: Record<string, string>
  regions: { kind: string; box: Box }[]
  tables: {
    box: Box
    rows: number
    cols: number
    /** model: recognized by the table structure model; rule: derived from text positions. */
    by: string
    cells: { row: number; col: number; row_span: number; col_span: number; box: Box; text: string }[]
  }[]
  pairs: { key: string; key_box: Box; value: string; value_box: Box; rule: string }[]
}
