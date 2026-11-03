"""内置的字段清单。以后有了"模板"功能，用户可以在网页上自己定义字段。"""

from jacara.plugins.extractors.base import FieldSpec

RECEIPT_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "merchant",
        "string",
        "商家（公司或店铺）名称，常带 SDN BHD、Ltd、有限公司等字样；不是收银员或顾客的人名",
    ),
    FieldSpec("document_no", "string", "单号、收据号或发票号"),
    FieldSpec("date", "date", "开票或交易日期"),
    FieldSpec("total", "number", "最终应付总额（含税、四舍五入之后的金额）"),
)
"""常用的 4 个字段。"""

RECEIPT_FULL_FIELDS: tuple[FieldSpec, ...] = (
    *RECEIPT_FIELDS,
    FieldSpec("company_reg_no", "string", "公司注册号，通常紧跟在商家名称下面，如 123456-X"),
    FieldSpec("address", "string", "商家地址，可能跨好几行，全部照抄并按原顺序连起来"),
    FieldSpec("time", "string", "交易时间，照抄原文，如 8:13:39 PM"),
    FieldSpec("cashier", "string", "收银员名字"),
    FieldSpec("member", "string", "会员号或会员名"),
    FieldSpec("subtotal", "number", "四舍五入之前的总额（Total、Sub Total）"),
    FieldSpec("tax", "number", "税额（GST、SST、Tax）"),
    FieldSpec("rounding", "number", "四舍五入调整金额（Rounding Adjustment）"),
    FieldSpec("cash", "number", "顾客付的现金"),
    FieldSpec("change", "number", "找零（Change）"),
)
"""收据上能找到的所有单个字段。商品明细是表格，要等表格识别做好后再抽。"""

PRESETS: dict[str, tuple[FieldSpec, ...]] = {
    "receipt": RECEIPT_FIELDS,
    "full": RECEIPT_FULL_FIELDS,
}
"""命令行 --fields 可选的清单名。"""
