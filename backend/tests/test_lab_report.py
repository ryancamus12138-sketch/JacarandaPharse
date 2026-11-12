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
