"""检验报告（化验单）起步模板和"超出参考范围标了高低"规则。"""

from __future__ import annotations

from jacara.plugins.extractors.base import ExtractedField
from jacara.plugins.validators.rules import check_lab_flag
from jacara.templates import load_starter


def test_lab_template_is_generic_and_complete():
    lab = load_starter("lab_report")
    assert lab.countries == () and lab.foreign_rules() == []
    assert {f.name for f in lab.fields} >= {"patient_name", "specimen", "reported_at", "reviewer"}
    assert [c.name for c in lab.tables[0].columns] == [
        "code",
        "name",
        "result",
        "flag",
        "unit",
        "reference",
        "method",
    ]
    assert {f.name for f in lab.fields if f.sensitive} == {"patient_name", "record_no", "diagnosis"}


def test_flags_match_the_reference_range():
    def check(result, reference, flag=""):
        return check_lab_flag({"result": result, "reference": reference, "flag": flag})

    assert check("12.3", "3.5-9.5", "↑") is None
    assert "没有标 ↑" in (check("12.3", "3.5-9.5") or "")
    assert "没有标 ↓" in (check("2.1", "3.5～9.5", "") or "")
    assert check("2.1", "3.5-9.5", "L") is None
    assert "却标了偏高" in (check("5.0", "3.5-9.5", "H") or "")
    assert "没有标 ↑" in (check("6.2", "<5.0") or "")
    assert check("阴性", "阴性") is None
    assert check("<0.1", "0-1") is None
    assert check("8", "见备注") is None


def test_lab_rule_runs_on_each_result_row():
    lab = load_starter("lab_report")
    fields = [
        ExtractedField("results.1.name", "string", "白细胞"),
        ExtractedField("results.1.result", "string", "12.3"),
        ExtractedField("results.1.reference", "string", "3.5-9.5"),
        ExtractedField("results.2.name", "string", "血红蛋白"),
        ExtractedField("results.2.result", "string", "140"),
        ExtractedField("results.2.reference", "string", "130-175"),
    ]
    issues = [i for i in lab.validator().validate(fields).issues if i.rule == "lab_flag"]
    assert len(issues) == 1 and "results.1.result" in issues[0].fields


def test_template_excel_lists_only_rules_it_can_use(client):
    """导出模板 Excel 的"规则"表只列用得上的规则：化验单只有 lab_flag，不列发票、收据的。"""
    import io

    from openpyxl import load_workbook

    created = client.post("/api/v1/templates", json={"source": "starter", "starter": "lab_report"})
    exported = client.get(f"/api/v1/templates/{created.json()['id']}/export")
    assert exported.status_code == 200, exported.text
    sheet = load_workbook(io.BytesIO(exported.content))["规则"]
    ids = [str(row[0]) for row in sheet.iter_rows(min_row=2, values_only=True) if row[0]]
    assert ids == ["lab_flag"]

    hotel = client.post("/api/v1/templates", json={"source": "starter", "starter": "hotel_folio"})
    exported = client.get(f"/api/v1/templates/{hotel.json()['id']}/export")
    sheet = load_workbook(io.BytesIO(exported.content))["规则"]
    ids = {str(row[0]) for row in sheet.iter_rows(min_row=2, values_only=True) if row[0]}
    assert {"stay_order", "stay_nights"} <= ids and "cn_invoice_no" not in ids


def test_every_starter_only_has_rules_it_can_run():
    """每个起步模板：选的规则字段都有（不会永远跳过），国家对得上（通用模板没有某国的规则）。"""
    from jacara.templates import STARTERS
    from jacara.workbook.standard import _usable

    for name in STARTERS:
        template = load_starter(name)
        shape = {**template.definition(), "countries": list(template.countries)}
        assert template.foreign_rules() == [], name
        assert all(_usable(r, shape) for r in template.rules), name


def test_radical_code_points_in_pdf_text_become_normal_characters():
    """有的 PDF 把汉字记成部首编码（⽰ U+2F70、⼩ U+2F29、⻔ U+2ED4、⺠ U+2EA0）：换成平常的字。"""
    from jacara.core.cjk import plain_cjk

    assert plain_cjk("⽰例市第⼀⼈⺠医院") == "示例市第一人民医院"
    assert plain_cjk("姓名：王⼩明") == "姓名：王小明"
    assert plain_cjk("科别：⼼内科⻔诊") == "科别：心内科门诊"
    assert plain_cjk("全血（EDTA-K2）") == "全血（EDTA-K2）"  # 全角括号不动
    assert plain_cjk("⺯") == "⺯"  # 偏旁（糹）不换


def test_stream_reports_how_much_the_model_wrote(monkeypatch):
    """模型边写边传：数出写了多少字，最后拼成完整的回答（处理进度按它走）。"""
    import contextlib
    import json

    import httpx

    from jacara.plugins.model_backends import ChatMessage
    from jacara.plugins.model_backends.llama_server import LlamaServerBackend

    pieces = ['{"a"', ": 1", "}"]
    lines = [
        "data: " + json.dumps({"model": "m", "choices": [{"delta": {"content": p}}]})
        for p in pieces
    ]
    lines += ['data: {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}']
    lines += ["data: [DONE]"]

    class Response:
        status_code = 200

        def iter_lines(self):
            return iter(lines)

    @contextlib.contextmanager
    def fake_stream(method, url, **kwargs):
        assert kwargs["json"]["stream"] is True
        yield Response()

    monkeypatch.setattr(httpx, "stream", fake_stream)
    seen: list[int] = []
    result = LlamaServerBackend().chat_stream(
        [ChatMessage("user", "x")], json_schema=None, on_text=seen.append
    )
    assert result.text == '{"a": 1}' and result.completion_tokens == 3
    assert seen == [4, 7, 8]


def test_progress_moves_while_the_model_writes():
    from jacara.pipeline.run import writing

    stages: list[tuple[str, int]] = []
    tell = writing(lambda stage, percent: stages.append((stage, percent)), "第 1 页", 20, 95)
    tell(500, 1000)
    assert stages and "模型已写 50%" in stages[0][0] and stages[0][1] == 58
    tell(5000, 1000)  # 一秒之内不重复报
    assert len(stages) == 1
