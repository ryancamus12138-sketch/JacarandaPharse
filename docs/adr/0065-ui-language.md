# 0065 界面语言：中文和英文切换

状态：已采纳（2026-11）

## 背景

界面、接口返回给网页的说明（报错、规则提示、设置说明、处理进度）原来都只有中文。要在右上角加一个按钮，
在"全中文"和"全英文"之间切换。

模板里的字段名、表名、模板名本来就有自己的多语言（ADR 0048：审核页"字段名"、导出时的列名可以单独切换），
那是**单据内容**的语言，和这里的**界面语言**是两回事，互不影响：界面切成英文，字段名照样按模板和用户选的语言显示；
反过来也一样。

## 决定

### 语言

- 两种：`zh-CN`（默认）和 `en`。第一次打开按浏览器的语言（`navigator.language` 以 `zh` 开头用中文，否则英文），
  之后按用户点的，记在这个浏览器里（`localStorage`，和"字段名语言"分开存）。
- 右上角一个按钮切换，按钮上写的是要切过去的那种语言（中文界面显示 "English"，英文界面显示"中文"），
  照 Ant Design 官网顶栏的做法。切换马上生效，不刷新页面。

### 前端

- 用 **i18next + react-i18next**（MIT）。组件里 `const { t } = useTranslation()`，`t('中文原文')`。
- **键就是中文原文**（gettext 的做法：msgid 就是源语言的原文；GitLab 前端 `__('...')` 也是拿原文当 msgid）。
  i18next 官方 FAQ 说明可以用自然语言当键，要把 `keySeparator`、`nsSeparator` 设成 `false`（原文里有"."和":"）。
  好处是代码里看得到原文、改动小；代价是改了中文原文，英文要跟着改（下面的检查会报出来）。
- 中文不需要翻译文件（找不到就显示键本身，也就是中文原文）；英文在 `web/src/i18n/en.json`：`{"中文原文": "English"}`。
- 带变量：`t('共 {{count}} 页', { count })`。英文复数用 i18next 的复数后缀（`_one`、`_other`，按 `Intl.PluralRules`）。
- 模块里的常量（状态名、分类名这些在组件外定义的表）：定义时用 `msg('中文')` 标记（什么都不做，只是让检查脚本认得出，
  相当于 gettext 的 `gettext_noop`），显示时再 `t(...)`。
- Ant Design 自带的文字（分页、"确定/取消"、空状态）：`ConfigProvider` 的 `locale` 跟着换 `zh_CN` / `en_US`。
- 日期时间格式、排序（`localeCompare`）、国家名（`Intl.DisplayNames`）跟着界面语言。
- 每个请求带上 `Accept-Language: zh-CN` 或 `en`，后端按它返回对应语言的说明。
- 切换语言时整个界面按语言重新挂载（`<App key={语言}>`）：组件里算好就缓存的文字（下拉选项、表格列、当前用户名）
  都按新语言重算；查询缓存在外层不丢，切换后全部重新请求一次，拿到后端按新语言返回的说明。
- 检查脚本 `web/scripts/i18n-check.mjs`（`npm run i18n:check`，CI 里跑）：用 Vite 自带的解析器（`parseAst`，oxc）
  读所有代码，1）`t()`、`msg()` 里的每个键在 `en.json` 里都有；2）`en.json` 里没有用不到的键、没有空翻译；
  3）`t()` / `msg()` 以外不能再有中文字符串（注释、`console` 除外；写模板内容的地方用 `// i18n-ignore` 标明）。

### 后端

- 用 Python 自带的 **gettext**，翻译文件是 GNU gettext 的 `.po` / `.mo`，放在 `backend/src/jacara/locale/en/LC_MESSAGES/`。
  msgid 也是中文原文，和前端一致。`.po` 用 **Babel**（BSD，只在开发时用）整理，`.mo` 提交进仓库（运行时不需要 Babel）：

  ```
  uv run pybabel extract -F babel.cfg -k tr -k trc:1c,2 -k ntr:1,2 -k N_ -k N_c:1c,2 -k stored \
      --no-default-keywords --sort-by-file --no-wrap --project=JacarandaParse \
      -o backend/src/jacara/locale/messages.pot backend/src/jacara
  uv run pybabel update -i backend/src/jacara/locale/messages.pot -d backend/src/jacara/locale -D messages --no-wrap
  uv run pybabel compile -d backend/src/jacara/locale -D messages --statistics
  ```

- `jacara.core.i18n`：
  - 当前语言放在 `contextvars`（每个请求一份，互不干扰）；默认中文。
  - `LanguageMiddleware` 读 `Accept-Language`（RFC 9110 第 12.5.4 节，按 q 值排序），取第一个支持的语言，和 Django 的
    `LocaleMiddleware` 一样；不认识的语言用默认的中文。
  - `tr("共 {n} 页", n=...)`：先翻译整句再填变量（照 GitLab 的做法，不拼接半句话）；`ntr` 是复数；
    `trc(上下文, 原文)` 是带上下文的翻译（gettext 的 `pgettext`），同一个中文在不同地方英文不同时用，例如 sheet 名"字段"
    是 Fields、列名"字段"是 Field；`N_` / `N_c` 只做标记（gettext 的 `gettext_noop`），用于模块里的常量表，显示时再 `tr`。
- **存进数据库、之后再显示的说明**（处理进度 `documents.stage`、失败原因 `documents.error`、重新处理的原因、字段的提示
  `extracted_values.note`、候选的说明、修改记录里系统写的原因、规则结果、OCR 评测的错误、样本裁图失败的原因、分组的原因、
  数据集里跳过的行的原因）：后台 worker 写的时候不知道看的人用什么语言，所以不存翻译后的句子，存"模板 + 参数"
  （照 Sentry 事件里 Message 接口的 `message` + `params`）：`stored("抽取第 {page} 页", page=2)` 得到
  `{"msg": "抽取第 {page} 页", "params": {"page": 2}}`，接口返回时 `render()` 按请求的语言翻译再填参数。
  - 参数本身也是存起来的说明时，按 JSON 对象嵌进去（不是转义后的字符串：转义一层长度翻一倍，嵌十几层就会爆掉）。
  - 几条说明连在一起（`join_notes`，中文用"；"，英文用"; "）、列一串名字（`join_items`，中文用"、"，英文用", "）存成
    `{"join": 分隔符, "items": [...]}`，分隔符显示时再翻译。
  - 以前存的纯文字照样显示（能整句对上翻译的就翻译，对不上原样显示）。`documents.stage` 原来是 64 个字符，放不下，
    迁移 `249b1c3144ba` 改成 `Text`。
  - 显示的地方：响应模型里这些字段用 `Rendered` 类型（pydantic `AfterValidator`），JSON 字段（规则结果、候选、自动选模板的理由、
    审计日志的明细）用 `render_checks` / `render_candidates` / `render_classified` / `render_stored`；
    各模块的路由把业务错误的 `err.message` 先 `render()` 再返回；命令行打印错误时也 `render()`（命令行仍是中文）。
  - 共用代码里抛出的异常（公式、模板、日期范围、模型服务、文件读取这些，既可能在请求里、也可能在 worker 里）用 `stored()`；
    只在请求里出现的报错直接用 `tr()`。`tr()` 的参数如果是存起来的说明，会先翻译再填进去。
- **导出文件按请求的语言写**：Excel（sheet 名、表头、状态、批注）、CSV（跳过的 sheet 按当前语言的名字比较）、JSON 里的规则提示、
  OCR 样本和检测样本导出包里的 README、审核数据包里的 README。
- **标准模板工作簿**（模板 ↔ Excel）：写的时候按请求的语言写 sheet 名、表头、类型、是/否、级别；读的时候两种语言都认
  （每个固定标签和它的所有翻译都算，`variants` / `variants_c`），所以英文界面导出的工作簿在中文界面也能导入，反过来也一样。
- 前端表单字段的候选里加了 `visible`（PDF 表单字段在页面上能不能看到：true / false / null），颜色按它判断，不再比较中文说明。
- 检查：`backend/tests/test_i18n.py` 用 Babel 从代码里抽一遍，确认和 `.po` 的条目一一对应、每条都有英文翻译、
  占位符一致、`.mo` 是最新编译的；`backend/tests/test_i18n_api.py` 用 `Accept-Language: en` 调接口，确认报错、
  处理结果、Excel 导出、标准模板工作簿都按语言返回，并且没有存储格式漏到前端。

### 不在这次范围

- 命令行（`jacara ...`）的输出、开发用的评测报告：给开发者看的，仍然是中文。
- 接口文档（`/docs` 里的接口说明）：给开发者看的，仍然是中文。
- 给大模型的提示词：模型看的，不跟着界面语言变。
- 认中文的正则和解析数据（日期、金额、大写金额、页码、"合计"行这些）：是识别单据内容用的，不是界面文字。
- 版面区域的名字（正文、标题、表格……）在结构数据里按中文存（提示词和判断要用），显示时再翻译。
- 模板内容（模板名、字段名、起步模板的"依据"）：按模板自己的多语言（ADR 0048），不跟着界面语言变。
  后来起步模板库做了中英两个版本、我的模板有了主语言，见 ADR 0066。
- 用户自己写的内容（备注、修改原因、跳过原因）：原样显示。

## 依据

- i18next FAQ（自然语言当键、`keySeparator: false`、`nsSeparator: false`）：https://www.i18next.com/how-to/faq
- i18next 复数（`_one` / `_other` 后缀，`Intl.PluralRules`）：https://www.i18next.com/translation-function/plurals
- GitLab 国际化（原文当 msgid、先翻译整句再填变量）：https://docs.gitlab.com/development/i18n/externalization/
- Babel 命令行（extract、init、update、compile）：https://babel.pocoo.org/en/stable/cmdline.html
- Django `LocaleMiddleware`（按 `Accept-Language` 选语言）：https://docs.djangoproject.com/en/stable/topics/i18n/translation/#how-django-discovers-language-preference
- RFC 9110 第 12.5.4 节 Accept-Language：https://www.rfc-editor.org/rfc/rfc9110#section-12.5.4
- Sentry Message 接口（`message` 模板 + `params`）：https://develop.sentry.dev/sdk/data-model/event-payloads/message/
- Ant Design 国际化（`ConfigProvider locale`）：https://ant.design/docs/react/i18n
- Python gettext（`pgettext`、`NullTranslations`）：https://docs.python.org/3/library/gettext.html
- GNU gettext 手册，消息上下文（msgctxt）：https://www.gnu.org/software/gettext/manual/html_node/Contexts.html
- pydantic `AfterValidator`：https://docs.pydantic.dev/latest/concepts/validators/
