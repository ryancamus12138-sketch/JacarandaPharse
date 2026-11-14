# 0066 模板主语言、起步模板中英双版本

日期：2026-11-14
状态：已采纳

## 背景

界面能切成英文以后（ADR 0065），模板内容还是中文：起步模板库的模板名、说明、领域、字段名、字段说明、依据都是中文，
英文界面里看到一屏中文。ADR 0048 的多语言名字只覆盖了字段名、表名和模板名，`label`、`title` 固定是中文。

用户要求：

1. 我的模板每个自己定语言；
2. 起步模板库做中英两个版本，全部内容都翻译。

## 决定

### 主语言

- 模板加一个"主语言"`language`（BCP 47 简写，和 ADR 0048 的语言代码一样），不写就是 `zh`，以前的模板不用改。
- `title`、`description`、每个字段和表格的 `label`、`description` 都是用主语言写的；
  别的语言放在 `titles`、`descriptions`、`names`、`descriptions` 里（`{ en = "...", zh = "..." }`）。
  这和 Contentful、Strapi 的"默认语言 + 各语言版本、没写的退回默认语言"是同一个做法，
  也就是 ADR 0048 里"没写的语言显示中文"换成"没写的语言显示主语言的"。
- 主语言的作用：
  - 审核页、导出默认用主语言的名字（`languages` 第一个就是主语言）；
  - 给模型的字段说明用主语言的 `description`（别的语言的说明只给人看）；
  - 别的语言的名字照旧当"单据上可能这么写"告诉模型。
- 模板名、说明在库里照旧存在 `templates` 表的 `title`、`description` 列（主语言的），
  其他语言和 `language` 存在版本的定义里（和 `.toml` 一样的结构），所以不用改表、不用迁移。
- 导出、导入模板文件（JSON）带上 `language`、`titles`、`descriptions`。
- 导出 Excel 的 `lang` 参数不写时用各模板的主语言；前端选了哪种语言就明确传哪种（选中文也传 `zh`），
  主语言是英文的模板选中文才会出中文列名。

### 换主语言

- 换主语言就是"对调"：新语言的文字搬到 `label`、`title`、`description`，原来的文字搬进 `names` 等，记在原来的语言下面。
  这种语言没写的那一处保持原样，什么都不丢，换回去就还原。后端是 `with_language()`，编辑器里是同样逻辑的 `withLanguage()`。
- 编辑器顶部选"主语言"；有几处还没有新语言版本的，弹提示说保留了原来的文字。
  "编辑语言"选主语言时改 `label` / `description`，选别的语言时改 `names` / `descriptions` 里那种语言的。
- 领域、自定义标签是用户自己的标签，编辑器里换主语言时不动。

### 起步模板中英双版本

- 40 个起步模板的 `.toml` 还是用中文写（主语言中文），每处说明后面加英文版：
  模板、字段、表格、列的 `descriptions = { en = "..." }`；文件开头的"依据"注释加一个 `[basis]` 表，`en = [ ... ]` 和注释一条对一条。
  字段名、表名、模板名的英文版本来就有（ADR 0048）。
- `load_starter_in(name, language)` 按语言读起步模板：中文是文件原样；英文用 `with_language()` 对调，
  依据取 `[basis].en`，领域按固定的对照表 `DOMAIN_NAMES` 翻译（发票 → Invoice 这些，只有起步模板库用到的 12 个）。
- 起步模板库列表、预览、导出按看的人的界面语言给；从起步模板加到我的模板时，主语言就是当时的界面语言，之后可以改。
  从 Excel 建的模板也一样，主语言是当时的界面语言，对表头时英文名字也算数。
- 加起步模板的英文翻译、改中文说明时，两边一起改；`backend/tests/test_i18n_api.py` 检查每处说明都有英文版、
  依据条数对得上、换过去再换回来和原来一样。

### 顺带修的

- 预览里"类型"一列在英文界面还是中文（模块加载时就算好了），改成显示时再取。
- "关闭"按钮在英文界面显示成 Off（和"版面分析：关闭"共用了一条翻译），版面分析那里改成"没开"。

## 不做的

- 不自动判断 Excel 表头是什么语言：主语言就用界面语言，不对的话在编辑器里改。
- 模型提示词不跟着主语言变：提示词本身还是中文，只是字段说明换成主语言的。

## 依据

- Contentful 本地化（每个空间一个默认语言，请求的语言没写时用 fallback 语言的内容）：
  https://www.contentful.com/developers/docs/tutorials/general/setting-locales/
- Strapi 国际化（整个应用一个默认语言、各语言版本）：https://docs.strapi.io/cms/features/internationalization
- django-modeltranslation（每种语言一个字段，如 `title_en`、`title_de`；没写的退回 `DEFAULT_LANGUAGE`）：
  https://django-modeltranslation.readthedocs.io/en/latest/usage.html
- BCP 47 语言标签：https://www.rfc-editor.org/rfc/rfc5646
- ADR 0048（模板多语言名字）、ADR 0065（界面语言）
