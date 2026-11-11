"""评测：读数据集、统一格式后比对、算指标、写结果文件。不需要模型和真的数据集。"""

from __future__ import annotations

import json

import pytest

from jacara.eval.run import run_eval, write_result
from jacara.eval.score import FieldScore, close_enough, same, score_report, summarize
from jacara.eval.sroie import load_sroie


@pytest.mark.parametrize(
    ("kind", "predicted", "gold", "expected"),
    [
        ("number", 9.0, "9.00", True),
        ("number", 1234.5, "RM 1,234.50", True),
        ("number", 100.0, "10.00", False),
        ("date", "2018-12-25", "25/12/2018", True),
        ("date", "2019-01-15", "15-01-19", True),
        ("date", "2018-03-02", "02 MAR 2018", True),
        ("date", "2018-03-22", "2018-03-22", True),
        ("date", "2017-12-28", "12/28/2017", True),
        ("date", "2018-03-04", "20180304", True),
        ("date", "2018-03-25", "25032018", True),
        ("date", "2016-10-03", "OCT 3, 2016", True),
        ("date", "2018-12-26", "25/12/2018", False),
        ("string", "BOOK TA _K (TAMAN DAYA) SDN BHD", "BOOK TA .K (TAMAN DAYA) SDN BHD", True),
        ("string", "book ta k taman daya sdn bhd", "BOOK TA .K (TAMAN DAYA) SDN BHD", True),
        ("string", "TAMAN DAYA", "BOOK TA .K (TAMAN DAYA) SDN BHD", False),
        ("string", None, "ABC", False),
        ("string", None, "", True),
    ],
)
def test_same_ignores_format_differences(kind, predicted, gold, expected):
    assert same(kind, predicted, gold) is expected


@pytest.mark.parametrize(
    ("predicted", "gold", "expected"),
    [
        ("LJAN HING STATIONERY SDN BHD", "LIAN HING STATIONERY SDN BHD", True),  # OCR 错一个字母
        ("99 SPEED MART S/B (519537-X)", "99 SPEED MART S/B", False),  # 多抄了注册号
        ("BAR WANG RICE", "UNIHAKKA INTERNATIONAL SDN BHD", False),  # 选了另一个名字
        (None, "ABC", False),
    ],
)
def test_close_enough_only_forgives_ocr_typos(predicted, gold, expected):
    assert close_enough("string", predicted, gold) is expected


def test_load_sroie_maps_field_names(tmp_path):
    (tmp_path / "key").mkdir()
    (tmp_path / "img").mkdir()
    for i in range(3):
        key = {"company": f"SHOP {i}", "date": "25/12/2018", "address": "X", "total": "9.00"}
        (tmp_path / "key" / f"00{i}.json").write_text(json.dumps(key))
        (tmp_path / "img" / f"00{i}.jpg").write_bytes(b"fake")
    (tmp_path / "key" / "009.json").write_text("{}")  # 没有图片的答案跳过

    samples = load_sroie(tmp_path, limit=2)
    assert [s.sample_id for s in samples] == ["000", "001"]
    assert samples[0].answers == {"merchant": "SHOP 0", "date": "25/12/2018", "total": "9.00"}


def test_load_sroie_missing_says_how_to_download(tmp_path):
    with pytest.raises(FileNotFoundError, match="jacara eval download"):
        load_sroie(tmp_path / "nope")


def test_score_report_uses_the_same_flags_as_review(report):
    answers = {"merchant": "BOOK TA .K (TAMAN DAYA) SDN BHD", "total": "9.00"}
    scores = {s.field: s for s in score_report("000", answers, report)}
    assert set(scores) == {"merchant", "total"}  # 只评有答案的字段
    assert scores["merchant"].correct and scores["merchant"].has_evidence
    # 总额对、找到了出处，但"现金 − 总额 = 找零"不通过（找零读成 100），所以也标黄
    assert scores["total"].correct and scores["total"].flagged


def _score(field, correct, flagged):
    return FieldScore("x", field, "g", "p", correct, has_evidence=not flagged, flagged=flagged)


def test_summary_rates():
    scores = [
        _score("total", correct=True, flagged=False),
        _score("total", correct=True, flagged=True),  # 误报
        _score("total", correct=False, flagged=True),  # 拦住了
        _score("total", correct=False, flagged=False),  # 漏了：没标黄的错值
    ]
    total, overall = summarize(scores)
    assert total.field == "total" and overall.field == "全部"
    assert total.accuracy == 0.5
    assert total.close_accuracy == 0.5
    assert total.catch_rate == 0.5
    assert total.false_alarm_rate == 0.5
    assert total.pass_rate == 0.5
    assert total.pass_accuracy == 0.5


def test_run_eval_records_failures_and_writes_files(tmp_path, report):
    from jacara.eval.sroie import Sample

    good = Sample("000", tmp_path / "000.jpg", {"total": "9.00"})
    bad = Sample("001", tmp_path / "001.jpg", {"total": "1.00"})

    def process(path):
        if path.name == "001.jpg":
            raise RuntimeError("模型没响应")
        return [report]

    result = run_eval([good, bad], process)
    assert result.failures == {"001": "RuntimeError: 模型没响应"}
    assert result.model == "test-model"

    write_result(result, tmp_path / "out", "SROIE")
    summary = json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))
    assert summary["samples"] == 2 and summary["failed"] == 1
    assert len((tmp_path / "out" / "fields.jsonl").read_text().splitlines()) == 1
    markdown = (tmp_path / "out" / "summary.md").read_text(encoding="utf-8")
    assert "放行准确率" in markdown and "宽松准确率" in markdown
