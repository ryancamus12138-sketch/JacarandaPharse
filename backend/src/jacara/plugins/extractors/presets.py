"""内置的字段清单。以后有了"模板"功能，用户可以在网页上自己定义字段。"""

from jacara.plugins.extractors.base import FieldSpec

RECEIPT_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("merchant", "string", "商家名称，通常在单据最上方"),
    FieldSpec("document_no", "string", "单号、收据号或发票号"),
    FieldSpec("date", "date", "开票或交易日期"),
    FieldSpec("total", "number", "最终应付总额（含税、四舍五入之后的金额）"),
)
