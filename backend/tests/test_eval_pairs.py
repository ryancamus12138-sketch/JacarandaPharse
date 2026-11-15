"""Paired evaluation statistics: paired bootstrap, regression and fix counts, merging partial
results."""

from pathlib import Path

from jacara.eval.pairs import (
    ArmResult,
    FieldOutcome,
    flips,
    load_manifest,
    merge_parts,
    paired_interval,
    save_part,
    summary,
    to_markdown,
)


def outcome(doc: str, arm: str, name: str, right: bool, flagged: bool = False) -> FieldOutcome:
    return FieldOutcome(doc, arm, name, True, right, flagged)


def test_paired_interval_is_reproducible_and_brackets_the_mean():
    a = {"d1": 0.8, "d2": 0.9, "d3": 1.0}
    b = {"d1": 0.9, "d2": 0.9, "d3": 1.0}
    mean, low, high = paired_interval(a, b)  # type: ignore[misc]
    assert abs(mean - 0.1 / 3) < 1e-9
    assert low <= mean <= high
    assert paired_interval(a, b) == paired_interval(a, b)


def test_flips_count_worse_and_better():
    before = ArmResult("A", [outcome("d", "A", "x", True), outcome("d", "A", "y", False)])
    after = ArmResult("D", [outcome("d", "D", "x", False), outcome("d", "D", "y", True)])
    assert flips(before, after) == (1, 1)


def test_parts_merge_and_summary(tmp_path: Path):
    manifest = tmp_path / "pairs.toml"
    manifest.write_text(
        '[[doc]]\nid = "d"\ntemplate = "receipt_general"\npdf = "a.pdf"\n'
        '[doc.truth]\ntotal = "9"\n',
        "utf-8",
    )
    docs = load_manifest(manifest)
    assert docs[0].kind == "受控对照" and docs[0].pdf == (tmp_path / "a.pdf").resolve()
    first = {arm: ArmResult(arm) for arm in "ABCDE"}
    first["A"].fields.append(outcome("d", "A", "total", True))
    first["D"].fields.append(outcome("d", "D", "total", False, flagged=True))
    first["D"].seconds_per_page.append(1.5)
    save_part(first, tmp_path / "part-1.json")
    merged = merge_parts([tmp_path / "part-1.json"])
    s = summary(merged, docs, "oracle")
    row = {r["arm"]: r for r in s["arms"]}
    assert row["A"]["accuracy"] == 1.0 and row["D"]["accuracy"] == 0.0
    assert row["D"]["silent_wrong"] == 0  # Wrong but flagged.
    assert s["photo_to_pdf"] == {"worse": 1, "better": 0}
    assert "成对测评" in to_markdown(s)
