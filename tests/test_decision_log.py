"""Tests for decision_log.py. Each test writes a synthetic decisions folder into tmp_path."""
from __future__ import annotations

from conftest import load_script, run_json, run_main, write_files

mod = load_script("decision-log", "decision_log.py")
AS_OF = ["--as-of", "2026-10-05"]
BODY = "\n## Context\nLogs grew. Nobody reads old ones.\n\n## Decision\nKeep 30 days.\n"


def decision(**fields: str) -> str:
    base = {"title": "T", "status": "active", "decided-by": "platform leads", "date": "2026-01-01",
            "review-date": "2027-01-01"}
    base.update({k.replace("_", "-"): v for k, v in fields.items()})
    return "---\n" + "".join(f"{k}: {v}\n" for k, v in base.items()) + "---\n" + BODY


def log(tmp_path, files):
    return str(write_files(tmp_path / "decisions", files))


def rules(rep: dict) -> set[tuple[str, str]]:
    return {(f["id"], f["rule"]) for f in rep["findings"]}


def test_lists_decisions_sorted_by_id_and_clean_log_exits_0(tmp_path):
    files = {
        "0012-logs.md": decision(id="D-0012", title="Keep build logs for 30 days", supersedes="D-0004"),
        "0004-logs.md": decision(id="D-0004", title="Keep build logs for a year", status="superseded",
                                 superseded_by="D-0012"),
    }
    rc, rep = run_json(mod, [log(tmp_path, files), *AS_OF])
    assert rc == 0 and rep["findings"] == []
    assert [d["id"] for d in rep["decisions"]] == ["D-0004", "D-0012"]
    assert rep["decisions"][1]["supersedes"] == ["D-0004"]


def test_overdue_review_only_for_active_and_proposed(tmp_path):
    files = {
        "a.md": decision(id="A", review_date="2026-09-01"),
        "b.md": decision(id="B", status="proposed", review_date="2026-10-04"),
        "c.md": decision(id="C", status="retired", review_date="2026-01-01"),
        "d.md": decision(id="D", review_date="2026-10-05"),
    }
    rc, rep = run_json(mod, [log(tmp_path, files), *AS_OF])
    assert rc == 1
    assert rules(rep) == {("A", "review-overdue"), ("B", "review-overdue")}


def test_broken_one_way_and_missing_supersede_links(tmp_path):
    files = {
        "a.md": decision(id="A", supersedes="B, Z"),
        "b.md": decision(id="B", status="superseded"),
        "c.md": decision(id="C", superseded_by="a.md"),
    }
    _, rep = run_json(mod, [log(tmp_path, files), *AS_OF])
    assert rules(rep) == {
        ("A", "broken-link"),
        ("A", "one-way-link"),
        ("B", "superseded-without-link"),
        ("C", "one-way-link"),
    }


def test_supersede_cycle(tmp_path):
    files = {
        "x.md": decision(id="X", status="superseded", supersedes="Y", superseded_by="Y"),
        "y.md": decision(id="Y", status="superseded", supersedes="X", superseded_by="X"),
    }
    _, rep = run_json(mod, [log(tmp_path, files), *AS_OF])
    assert rules(rep) == {("X", "supersede-cycle"), ("Y", "supersede-cycle")}


def test_missing_fields_bad_values_and_malformed_front_matter(tmp_path):
    files = {
        "0001-x.md": "---\ntitle: No status\ndate: 2026-02-30\n---\n## Decision\nX.\n",
        "0002-y.md": decision(id="0001-x", status="maybe"),
        "0003-z.md": "---\ntitle: never closed\nstatus: active\n",
    }
    _, rep = run_json(mod, [log(tmp_path, files), *AS_OF])
    got = rules(rep)
    assert {("0001-x", "missing-field"), ("0001-x", "invalid-date"), ("0001-x", "missing-section"),
            ("0001-x", "duplicate-id"), ("0001-x", "invalid-status"),
            ("0003-z", "malformed-front-matter")} <= got
    missing = sorted(f["detail"] for f in rep["findings"] if f["rule"] == "missing-field" and f["file"] == "0001-x.md")
    assert missing == ["decided-by is empty or absent", "review-date is empty or absent", "status is empty or absent"]


def test_empty_and_bad_inputs_exit_2(tmp_path):
    (tmp_path / "empty").mkdir()
    (tmp_path / "empty" / "README.md").write_text("# Decisions\n")
    rc, _, err = run_main(mod, [str(tmp_path / "empty")])
    assert rc == 2 and "no decision files" in err
    rc, _, err = run_main(mod, [str(tmp_path / "missing")])
    assert rc == 2 and "not a folder" in err
    rc, _, err = run_main(mod, [log(tmp_path, {"a.md": decision()}), "--as-of", "2026-1-5x"])
    assert rc == 2 and "--as-of" in err


def test_index_writes_only_the_named_file(tmp_path):
    folder = log(tmp_path, {"a.md": decision(id="A", title="Use one on-call rota")})
    index = tmp_path / "decisions" / "INDEX.md"
    rc, out, _ = run_main(mod, [folder, *AS_OF, "--index", str(index)])
    assert rc == 0
    assert index.read_text().startswith("# Decision log\n")
    assert "| [A](a.md) | Use one on-call rota | active |" in index.read_text()
    assert sorted(p.name for p in (tmp_path / "decisions").iterdir()) == ["INDEX.md", "a.md"]
    rc, _, _ = run_main(mod, [folder, *AS_OF])  # INDEX.md is skipped on the next run
    assert rc == 0


GOLDEN = """\
# Decision log

| ID | Title | Status | Date | Review date | Decided by | Supersedes | Superseded by |
|---|---|---|---|---|---|---|---|
| [D-0004](0004-logs.md) | Keep build logs for a year | superseded | 2026-01-01 | 2027-01-01 | platform leads |  | \
D-0012 |
| [D-0012](0012-logs.md) | Keep build logs for 30 days | active | 2026-01-01 | 2026-09-14 | platform leads | D-0004 \
|  |

As of 2026-10-05: 2 decision(s), 1 finding(s).

## Findings

- D-0012 (`0012-logs.md`) review-overdue: review-date 2026-09-14 is 21 days before 2026-10-05
"""


def test_golden_report(tmp_path):
    files = {
        "0012-logs.md": decision(id="D-0012", title="Keep build logs for 30 days", supersedes="D-0004",
                                 review_date="2026-09-14"),
        "0004-logs.md": decision(id="D-0004", title="Keep build logs for a year", status="superseded",
                                 superseded_by="D-0012"),
    }
    rc, out, _ = run_main(mod, [log(tmp_path, files), *AS_OF])
    assert rc == 1
    assert out == GOLDEN
