"""Lab report starter template and the rule "out-of-range results are flagged high or low"."""

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


def test_template_excel_lists_only_chosen_rules(client):
    """The rules sheet of the exported template workbook lists only rules selected by the template:
    the lab report has only lab_flag, without invoice or receipt rules."""
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
    rows = [row for row in sheet.iter_rows(min_row=2, values_only=True) if row[0]]
    chosen = [r["id"] for r in hotel.json()["rules"]]
    assert sorted(str(r[0]) for r in rows) == sorted(chosen)
    assert all(r[3] == "是" for r in rows)


def test_every_starter_only_has_rules_it_can_run():
    """Every starter template: all fields of its selected rules are present (no rule is skipped
    forever), and countries match (generic templates have no country rules)."""
    from jacara.templates import STARTERS
    from jacara.workbook.standard import usable_rule

    for name in STARTERS:
        template = load_starter(name)
        shape = {**template.definition(), "countries": list(template.countries)}
        assert template.foreign_rules() == [], name
        assert all(usable_rule(r, shape) for r in template.rules), name


def test_radical_code_points_in_pdf_text_become_normal_characters():
    """Some PDFs encode CJK ideographs as radicals (⽰ U+2F70, ⼩ U+2F29, ⻔ U+2ED4, ⺠ U+2EA0):
    normalized to regular characters."""
    from jacara.core.cjk import plain_cjk

    assert plain_cjk("⽰例市第⼀⼈⺠医院") == "示例市第一人民医院"
    assert plain_cjk("姓名：王⼩明") == "姓名：王小明"
    assert plain_cjk("科别：⼼内科⻔诊") == "科别：心内科门诊"
    assert plain_cjk("全血（EDTA-K2）") == "全血（EDTA-K2）"  # Full-width parentheses unchanged.
    assert plain_cjk("⺯") == "⺯"  # Components (糹) are not replaced.


def test_stream_reports_how_much_the_model_wrote(monkeypatch):
    """Streaming model output: characters written are counted and the full answer is assembled
    (progress follows it)."""
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
    from jacara.core.i18n import render
    from jacara.pipeline.run import writing

    stages: list[tuple[str, int]] = []
    tell = writing(lambda stage, percent: stages.append((stage, percent)), "第 1 页", 20, 95)
    tell(500, 1000)
    assert stages and "模型已写 50%" in render(stages[0][0]) and stages[0][1] == 58
    tell(5000, 1000)  # Not reported twice within one second.
    assert len(stages) == 1


def test_saving_drops_rules_the_template_cannot_run(client):
    """Rules depending on deleted fields or a removed country are dropped on save (instead of being
    skipped forever)."""
    created = client.post(
        "/api/v1/templates", json={"source": "starter", "starter": "hotel_folio"}
    ).json()
    fields = [f for f in created["fields"] if f["name"] != "issue_date"]
    saved = client.put(
        f"/api/v1/templates/{created['id']}",
        json={
            "title": created["title"],
            "category": created["category"],
            "fields": fields,
            "tables": created["tables"],
            "rules": [r["id"] for r in created["rules"]] + ["seller_abn", "cn_invoice_no"],
            "countries": [],
        },
    )
    assert saved.status_code == 200, saved.text
    ids = {r["id"] for r in saved.json()["rules"]}
    # Requires issue_date.
    assert "issue_date_exists" not in ids and "issue_date_not_future" not in ids
    # Other countries' rules and rules with missing fields.
    assert "seller_abn" not in ids and "cn_invoice_no" not in ids
    assert ids  # The others remain.
