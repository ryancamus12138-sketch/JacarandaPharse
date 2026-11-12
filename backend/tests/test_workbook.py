"""从 Excel 建模板、按原工作簿导出、对象合并、参照表、标准模板 Excel（ADR 0031）。"""

from __future__ import annotations

import io
import json
import uuid

from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.datavalidation import DataValidation

from jacara.pipeline.process import process_document
from jacara.pipeline.run import Pipeline
from jacara.plugins.extractors import LlmExtractor
from jacara.plugins.model_backends import ChatResult
from jacara.workbook.analyze import analyze, find_header_row

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def xlsx(book: Workbook) -> bytes:
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def reimbursement() -> bytes:
    """报销表：标题行 + 汇总（一张单据一行）+ 明细（一项一行）+ 按月复制的一张 + 选项 + 说明。"""
    book = Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "报销汇总"
    ws.append(["2026 年 10 月报销汇总"])
    ws.append([])
    ws.append(["序号", "报销人", "单号", "商家", "日期", "总额", "费用类别"])
    ws.append([1, "李", "A001", "某店", "2026-10-01", 106, "餐饮"])
    ws.append(["合计", None, None, None, None, 106, None])
    items = book.create_sheet("报销明细")
    items.append(["单号", "商品", "金额"])
    items.append(["A001", "午餐", 100])
    items.append(["A001", "服务费", 6])
    month = book.create_sheet("9月")
    month.append(["序号", "报销人", "单号", "商家", "日期", "总额", "费用类别"])
    options = book.create_sheet("选项")
    for value in ("费用类别", "餐饮", "交通", "住宿"):
        options.append([value])
    check = DataValidation(type="list", formula1="选项!$A$2:$A$4")
    ws.add_data_validation(check)
    check.add("G4:G200")
    book.create_sheet("填表说明").append(["日期写成 YYYY-MM-DD"])
    return xlsx(book)


def pharma() -> bytes:
    """药企：实验数据（一个指标一行）+ 病人信息（一个病人一行）+ 实验室名单（参照）。"""
    book = Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "实验数据"
    ws.append(["病人编号", "实验室名称", "实验室编号", "检测项目", "结果"])
    ws.append(["P001", "第一实验室", "L01", "血糖", 5.1])
    ws.append(["P001", "第一实验室", "L01", "血脂", 1.2])
    ws.append(["P002", "第二实验室", "L02", "血糖", 6.0])
    patients = book.create_sheet("病人信息")
    patients.append(["病人编号", "姓名", "性别"])
    patients.append(["P001", "张三", "男"])
    patients.append(["P002", "李四", "女"])
    labs = book.create_sheet("实验室名单")
    labs.append(["实验室编号", "实验室名称", "城市"])
    labs.append(["L01", "第一实验室", "北京"])
    labs.append(["L02", "第二实验室", "上海"])
    return xlsx(book)


# ---------- 分析 ----------


def test_header_row_skips_title_lines():
    rows = [["2026 年报销汇总", None], [None, None], ["日期", "金额"], ["2026-10-01", 5]]
    assert find_header_row(rows) == 3


def test_analyze_reimbursement():
    result = analyze(reimbursement(), "报销.xlsx")
    roles = {s.name: s.role for s in result.sheets}
    assert roles == {
        "报销汇总": "document",
        "报销明细": "items",
        "9月": "document",
        "选项": "options",
        "填表说明": "ignore",
    }
    sheets = {s.name: s for s in result.sheets}
    assert sheets["9月"].same_as == "报销汇总"
    assert sheets["报销汇总"].header_row == 3
    columns = {c.header: c for c in sheets["报销汇总"].columns}
    assert columns["序号"].source == "auto" and columns["序号"].auto == "seq"
    assert columns["报销人"].source == "manual"
    assert columns["费用类别"].options == ["餐饮", "交通", "住宿"]
    assert columns["总额"].type == "number" and columns["日期"].type == "date"
    # 对上了通用收据：字段 ID 用它的
    assert result.starter == "receipt_general"
    assert columns["商家"].field == "merchant" and columns["总额"].field == "total"
    link = next(c for c in sheets["报销明细"].columns if c.header == "单号")
    assert link.source == "doc" and link.field == columns["单号"].field


def test_analyze_pharma():
    result = analyze(pharma(), "实验.xlsx")
    sheets = {s.name: s for s in result.sheets}
    assert sheets["实验数据"].role == "items"
    assert sheets["病人信息"].role == "entity" and sheets["病人信息"].key_header == "病人编号"
    assert sheets["实验室名单"].role == "reference"
    assert sheets["实验室名单"].match_field == "实验数据::实验室名称"
    code = next(c for c in sheets["实验数据"].columns if c.header == "实验室编号")
    assert code.source == "lookup" and code.lookup == "实验室名单::实验室编号"


# ---------- 建模板、处理、导出 ----------


class Backend:
    """假模型：按 JSON Schema 回答。values：字段 ID → (值, 行号)；tables：表名 → 行。"""

    name = "fake"

    def __init__(self, values, tables=None) -> None:
        self.values = values
        self.tables = tables or {}
        self.prompts: list[str] = []

    def chat(self, messages, *, json_schema=None):
        assert json_schema is not None
        self.prompts.append(messages[-1].content)
        answer = {}
        for key, spec in json_schema["properties"].items():
            if spec.get("type") == "array":
                answer[key] = self.tables.get(key, [])
            else:
                value, row = self.values.get(key, (None, None))
                answer[key] = {"value": value, "row": row}
        return ChatResult(json.dumps(answer), "fake-model", self.name, 0, 0, 1)


class FakeOcr:
    name = "fake-ocr"

    def __init__(self, page) -> None:
        self.page = page

    def recognize(self, image):
        return self.page


def upload_excel(client, data: bytes, name: str = "表.xlsx"):
    response = client.post("/api/v1/templates/excel", files={"file": (name, data, XLSX)})
    assert response.status_code == 200, response.text
    return response.json()


def build(client, analysis, title="报销模板", edit=None):
    sheets = analysis["sheets"]
    if edit:
        edit(sheets)
    response = client.post(
        f"/api/v1/templates/excel/{analysis['upload_id']}/build",
        json={"title": title, "starter": analysis["starter"], "sheets": sheets},
    )
    assert response.status_code == 201, response.text
    return response.json()


def process(client, factory, storage, page, template_id, backend):
    response = client.post(
        "/api/v1/documents",
        files={"file": ("r.jpg", b"x", "image/jpeg")},
        data={"template": template_id},
    )
    document_id = uuid.UUID(response.json()["id"])
    pipeline = Pipeline(ocr=FakeOcr(page), extractor=LlmExtractor(backend))
    assert (
        process_document(document_id, storage=storage, pipeline=pipeline, factory=factory) == "done"
    )
    return str(document_id)


def test_build_process_and_export_into_original(client, factory, storage, receipt_page):
    analysis = upload_excel(client, reimbursement())
    assert analysis["kind"] == "report"
    template = build(client, analysis)
    fields = {f["name"]: f for f in template["fields"]}
    assert fields["reimburser" if "reimburser" in fields else "field_1"]["extract"] is False
    expense = next(f for f in template["fields"] if f["label"] == "费用类别")
    assert expense["options"] == ["餐饮", "交通", "住宿"]
    assert template["tables"][0]["label"] == "报销明细"
    assert template["workbook"]["filename"] == "表.xlsx"
    assert "total_positive" in [r["id"] for r in template["rules"]]  # 起步模板的规则带过来了

    backend = Backend(
        {
            "document_no": ("A001", None),
            "merchant": ("BOOK TA _K (TAMAN DAYA) SDN BHD", 1),
            "date": ("2018-12-25", 4),
            "total": (9.0, 6),
            expense["name"]: ("购物", None),
        },
        {
            template["tables"][0]["name"]: [
                {"row": 6, "description": "1 PC", "amount": 9.0},
            ]
        },
    )
    document_id = process(client, factory, storage, receipt_page, template["id"], backend)
    # 人工填的字段不交给模型
    assert "报销人" not in backend.prompts[0]

    detail = client.get(f"/api/v1/documents/{document_id}").json()
    values = {v["name"]: v for v in detail["extractions"][0]["values"]}
    manual = next(v for v in values.values() if v["label"] == "报销人")
    assert manual["origin"] == "manual" and manual["value"] is None
    rules = {i["rule"] for i in detail["extractions"][0]["current_checks"]["issues"]}
    assert "options" in rules  # “购物”不在可选值里

    second = process(client, factory, storage, receipt_page, template["id"], backend)
    response = client.get(f"/api/v1/documents/export.xlsx?ids={document_id}&ids={second}")
    book = load_workbook(io.BytesIO(response.content))
    assert book.sheetnames[:5] == ["报销汇总", "报销明细", "9月", "选项", "填表说明"]
    # "报销汇总"和"9月"表头一样（按月分的）：原来的都不动，填进复制出来的一张
    assert book["报销汇总"]["B4"].value == "李"
    copy = next(n for n in book.sheetnames if n.startswith("报销汇总 "))
    assert copy.endswith("导出")
    summary = book[copy]
    assert summary["A1"].value == "2026 年 10 月报销汇总"  # 标题行留着
    assert [c.value for c in summary[4]][:4] == [1, None, "A001", "BOOK TA _K (TAMAN DAYA) SDN BHD"]
    assert summary["A5"].value == 2
    assert summary["F4"].value == 9.0
    assert summary["A6"].value == "合计" and summary["F6"].value == "=SUM(F4:F5)"
    items = book["报销明细"]
    assert [c.value for c in items[2]] == ["A001", "1 PC", 9.0]
    assert items.max_row == 3  # 两份单据各一行，原来的示例数据清掉了


def test_entity_merge_conflict_and_lookup(client, factory, storage, receipt_page):
    analysis = upload_excel(client, pharma())
    template = build(client, analysis, "实验数据")
    assert template["entities"][0]["label"] == "病人信息"
    assert template["references"][0]["row_count"] == 2
    patient = {f["label"]: f["name"] for f in template["fields"]}
    table = template["tables"][0]
    column = {c["label"]: c["name"] for c in table["columns"]}

    def backend(gender: str, lab: str) -> Backend:
        return Backend(
            {
                patient["病人编号"]: ("P001", 1),
                patient["姓名"]: ("张三", 1),
                patient["性别"]: (gender, 1),
            },
            {
                table["name"]: [
                    {
                        "row": 6,
                        column["实验室名称"]: lab,
                        column["检测项目"]: "血糖",
                        column["结果"]: 9.0,
                    }
                ]
            },
        )

    first = process(
        client, factory, storage, receipt_page, template["id"], backend("男", "第一实验室")
    )
    second = process(
        client, factory, storage, receipt_page, template["id"], backend("女", "不认识的实验室")
    )

    issues = client.get(f"/api/v1/documents/{second}").json()["extractions"][0]["current_checks"][
        "issues"
    ]
    rules = {i["rule"] for i in issues}
    assert "entity_conflict" in rules  # 同一个病人，性别对不上
    assert "lookup" in rules  # 实验室名单里没有

    response = client.get(f"/api/v1/documents/export.xlsx?ids={first}&ids={second}")
    book = load_workbook(io.BytesIO(response.content))
    patients = book["病人信息"]
    assert patients.max_row == 2  # 同一个病人合并成一行
    assert patients["C2"].value == "男" and patients["C2"].comment is not None
    data = book["实验数据"]
    assert [c.value for c in data[2]] == ["P001", "第一实验室", "L01", "血糖", 9.0]
    assert data["C3"].value is None  # 对照不上，编号留空
    assert book["实验室名单"].max_row == 3  # 参照表原样不动


def test_editor_save_keeps_workbook(client):
    template = build(client, upload_excel(client, reimbursement()))
    body = {
        "title": template["title"],
        "fields": [f for f in template["fields"] if f["label"] != "商家"],
        "tables": template["tables"],
        "rules": [],
    }
    saved = client.put(f"/api/v1/templates/{template['id']}", json=body)
    assert saved.status_code == 200, saved.text
    sheet = saved.json()["workbook"]["sheets"][0]
    merchant = next(c for c in sheet["columns"] if c["header"] == "商家")
    assert merchant["ref"] == ""  # 删掉的字段，导出时这一列留空


# ---------- 标准模板 Excel（方法一） ----------


def test_standard_excel_round_trip(client):
    template = build(client, upload_excel(client, pharma()), "实验数据")
    exported = client.get(f"/api/v1/templates/{template['id']}/export")
    assert exported.headers["content-type"] == XLSX
    book = load_workbook(io.BytesIO(exported.content))
    assert {"模板", "字段", "表格", "规则", "对象", "参照表"} <= set(book.sheetnames)

    analysis = upload_excel(client, exported.content, "实验数据.xlsx")
    assert analysis["kind"] == "standard"
    imported = client.post("/api/v1/templates/import", json=analysis["template"]).json()
    assert [f["name"] for f in imported["fields"]] == [f["name"] for f in template["fields"]]
    assert imported["references"][0]["row_count"] == 2
    assert imported["workbook"] is None  # 原表样式要用 JSON 搬


def test_standard_excel_error_says_which_row(client):
    book = Workbook()
    info = book.active
    assert info is not None
    info.title = "模板"
    info.append(["项目", "内容"])
    info.append(["模板名", "坏的"])
    fields = book.create_sheet("字段")
    fields.append(["中文名", "字段ID", "类型", "必填"])
    fields.append(["总额", "total", "金额", "是"])
    response = client.post("/api/v1/templates/excel", files={"file": ("x.xlsx", xlsx(book), XLSX)})
    assert response.status_code == 422
    assert "第 2 行" in response.json()["detail"] and "类型" in response.json()["detail"]


def test_json_export_carries_original_workbook(client):
    template = build(client, upload_excel(client, reimbursement()))
    data = client.get(f"/api/v1/templates/{template['id']}/export?format=json").json()
    assert data["workbook_file"] and data["workbook"]["filename"] == "表.xlsx"
    imported = client.post("/api/v1/templates/import", json=data).json()
    assert imported["workbook"]["filename"] == "表.xlsx"


def test_not_an_excel(client):
    response = client.post("/api/v1/templates/excel", files={"file": ("x.xlsx", b"nope", XLSX)})
    assert response.status_code == 422
    assert ".xlsx" in response.json()["detail"]
