"""Tests for vendor_scorecard.py. Scorecards are synthetic CSV files written in each test."""

from __future__ import annotations

from conftest import load_script, run_json, run_main, write_files

mod = load_script("vendor-scorecard", "vendor_scorecard.py")

CLEAR = """
    criterion,weight,Alpha,Beta,Gamma,notes
    Security,3,5,3,2,assurance report reviewed
    Cost,2,4,3,3,
    Support,1,5,2,3,
    """

CLOSE = """
    criterion,weight,Alpha,Beta,Gamma
    Security,3,4,3,5
    Cost,2,3,5,2
    Support,1,5,4,3
    """


def csv_file(tmp_path, text, name="scores.csv"):
    return str(write_files(tmp_path, {name: text}) / name)


def test_clear_leader_survives_every_move_and_exits_0(tmp_path):
    rc, rep = run_json(mod, [csv_file(tmp_path, CLEAR)])
    assert rc == 0
    assert rep["weighted_scores"] == {"Alpha": "4.67", "Beta": "2.83", "Gamma": "2.50"}
    assert rep["ranking"] == [["Alpha"], ["Beta"], ["Gamma"]]
    assert rep["leader_survives"] and rep["ranking_survives"]
    assert len(rep["sensitivity"]) == 6 and all(m["tested"] for m in rep["sensitivity"])
    assert rep["findings"] == []


def test_tie_for_first_and_leader_not_robust_are_flagged(tmp_path):
    rc, rep = run_json(mod, [csv_file(tmp_path, CLOSE)])
    assert rc == 1
    assert rep["ranking"][0] == ["Alpha", "Beta"]
    rules = [f["rule"] for f in rep["findings"]]
    assert rules[0] == "tie-for-first"
    assert rules.count("leader-not-robust") == 6
    move = next(m for m in rep["sensitivity"] if m["criterion"] == "Cost" and m["move"] == "+1")
    assert move["leader"] == ["Beta"] and move["weight"] == "3"


def test_move_below_zero_is_not_tested(tmp_path):
    text = """
        criterion,weight,Alpha,Beta
        Security,0,5,1
        Cost,2,4,2
        """
    rc, rep = run_json(mod, [csv_file(tmp_path, text)])
    assert rc == 0
    minus = next(m for m in rep["sensitivity"] if m["criterion"] == "Security" and m["move"] == "-1")
    assert minus["tested"] is False and minus["weight"] is None
    rc, out, _ = run_main(mod, [csv_file(tmp_path, text)])
    assert "not tested (weight below 0)" in out


def test_malformed_and_invalid_rows_are_data_problems(tmp_path):
    text = """
        criterion,weight,Alpha,Beta
        Security,3,5,2
        Cost,2,4
        Support,heavy,3,3
        Docs,1,6,3
        Security,1,1,1
        ,1,2,2
        """
    rc, rep = run_json(mod, [csv_file(tmp_path, text)])
    assert rc == 1
    found = {(f["rule"], f["line"]) for f in rep["findings"]}
    assert found == {
        ("malformed-row", 3),
        ("invalid-weight", 4),
        ("invalid-score", 5),
        ("duplicate-criterion", 6),
        ("missing-criterion", 7),
    }
    assert [c["criterion"] for c in rep["criteria"]] == ["Security"]


def test_empty_and_bad_inputs_exit_2(tmp_path):
    assert run_main(mod, [csv_file(tmp_path, "", "empty.csv")])[0] == 2
    assert run_main(mod, [csv_file(tmp_path, "name,score,Alpha\nx,1,2\n", "nohead.csv")])[0] == 2
    assert run_main(mod, [csv_file(tmp_path, "criterion,weight\nSecurity,1\n", "novendor.csv")])[0] == 2
    assert run_main(mod, [csv_file(tmp_path, "criterion,weight,Alpha\nSecurity,1,9\n", "nouse.csv")])[0] == 2
    assert run_main(mod, [csv_file(tmp_path, CLEAR), "--step", "0"])[0] == 2
    rc, _, err = run_main(mod, [str(tmp_path / "missing.csv")])
    assert rc == 2 and "file not found" in err


def test_decimal_step_and_weights(tmp_path):
    text = """
        criterion,weight,Alpha,Beta
        Security,1.5,4,3
        Cost,1,3,4
        """
    rc, rep = run_json(mod, [csv_file(tmp_path, text), "--step", "0.5"])
    assert rep["step"] == "0.5"
    assert rep["weighted_scores"] == {"Alpha": "3.60", "Beta": "3.40"}
    moves = {(m["criterion"], m["move"]): m for m in rep["sensitivity"]}
    assert moves[("Security", "-0.5")]["weight"] == "1"
    assert moves[("Security", "-0.5")]["leader"] == ["Alpha", "Beta"]
    assert rc == 1


def test_golden_markdown_and_out_file(tmp_path):
    out_file = tmp_path / "scorecard.md"
    rc, out, _ = run_main(mod, [csv_file(tmp_path, CLEAR), "--out", str(out_file)])
    assert rc == 0 and out == ""
    text = out_file.read_text(encoding="utf-8")
    assert text.splitlines()[:12] == [
        "# Vendor scorecard: scores.csv",
        "",
        "3 criteria, 3 vendor(s). Scores 1 to 5; weighted score = sum(weight x score) / sum(weight). "
        "Sensitivity step: 1.",
        "",
        "## Scores",
        "",
        "| Criterion | Weight | Alpha | Beta | Gamma |",
        "|---|---:|---:|---:|---:|",
        "| Security | 3 | 5 | 3 | 2 |",
        "| Cost | 2 | 4 | 3 | 3 |",
        "| Support | 1 | 5 | 2 | 3 |",
        "| **Weighted score** | | **4.67** | **2.83** | **2.50** |",
    ]
    assert "1. Alpha: 4.67" in text
    assert "The full ranking survives every weight moved by 1 in either direction." in text
    assert "- Security: assurance report reviewed" in text
    assert text.endswith("- Chosen vendor, decided by, date and reason: ____\n")
    assert run_main(mod, [csv_file(tmp_path, CLEAR)])[1] == text


def test_help_exits_0():
    rc, out, _ = run_main(mod, ["--help"])
    assert rc == 0 and out.startswith("usage: vendor_scorecard.py")
