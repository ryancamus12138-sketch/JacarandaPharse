# 0024：模板配置化——字段和规则写在 TOML 文件里

- 状态：已采纳
- 日期：2026-11-11

## 背景

v1 的字段清单写在 `plugins/extractors/presets.py` 里，规则清单写在 `rules.py` 里，字段的中文名又在前端 `fields.ts` 里写了一份。
v2 要做商品明细、发票，以后还要让用户自己建模板，字段定义必须集中到一个地方，并且能看出"这条结果是按哪一版定义抽的"。

## 决定

1. **一个模板一个 TOML 文件**，放在 `backend/src/jacara/templates/`：`receipt.toml`（常用 4 个字段，默认）、`receipt_full.toml`（14 个）。
   文件里写：名字、版本、标题、说明、字段（name、label 中文名、type、required、description 给模型的说明）、规则编号。
2. **用 TOML 不用 YAML**：Python 自带 `tomllib`，不用加依赖；能写注释；写法不容易出错（YAML 的缩进和类型猜测容易坑人）。
3. **规则的代码还在 `rules.py`**，模板只按编号选用（`RULES` 登记表）。规则是代码，要测试；模板只是配置。
4. **读模板时检查**：名字和文件名一致、版本是正整数、字段名只用小写英文数字下划线、类型合法、不重复、规则编号存在。写错了说清楚哪一项。
5. **每次抽取记下模板名和版本**：`extractions.template_version`（新列，旧数据记为 1）；`DocumentReport.template_version` 一路带到导出。
6. **改了模板的字段、说明或规则，version 加 1，并跑评测。**
7. **只读接口** `GET /api/v1/templates`、`/templates/{name}`，登录就能看，给以后的模板页、审核页的中文名用。
   模板没有数据库表，所以 `modules/templates/` 只有 schemas、service、router 三层。
8. 命令行 `jacara extract --fields full` 改为 `--template receipt_full`；新增 `jacara templates` 列出模板。

## 这次不做

- `required` 只记录、不校验：做"必填"校验会改变标黄结果，放到字段双维度状态那一课。
- 前端还用自己的 `fields.ts` 中文名；改成读接口，放到模板只读页那一课。
- Excel 表头还用英文字段名，改成中文名放到导出那一课。
- 网页上编辑模板（v3）。

## 影响

- 这一课只是"搬家"：receipt 模板的字段、顺序、给模型的说明和 v1 完全一样，评测结果应该不变。
- 加发票模板时，只要新建 `invoice.toml`、写需要的规则，不用改流水线。
