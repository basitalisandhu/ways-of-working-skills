"""Tests for one_on_one_ledger.py. Notes are synthetic, written under tmp_path by each test; names are examples.

As of 2026-10-05 unless a test says otherwise. Test names avoid the word that the shared-path heuristic looks for,
because pytest puts the test name into tmp_path; the refusal tests build such a path on purpose.
"""

from __future__ import annotations

from pathlib import Path

from conftest import load_script, run_json, run_main

mod = load_script("one-on-one-ledger", "one_on_one_ledger.py")
ASOF = ["--as-of", "2026-10-05"]

ALEX = """\
# Alex Example
cadence: 7

## 2026-09-01
- topic: On-call rota
- [ ] me: send the training budget link (due 2026-09-10)
- [ ] them: draft the runbook outline
Free text that is not read.

## 2026-09-20
- topic: on-call rota!
- [x] them: draft the runbook outline
- [ ] them: pick a conference talk (due 2026-10-20)
"""

DATED = """\
Agenda for the day, not read.

## Sam Sample
- topic: Mentoring plan
- [ ] me: introduce a mentor
- [ ] book a room

## Alex Example
- topic: On-call rota
"""


def notes(tmp_path: Path, files: dict[str, str]) -> str:
    folder = tmp_path / "notes"
    folder.mkdir()
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    return str(folder)


def person(rep: dict, name: str) -> dict:
    return next(p for p in rep["people"] if p["person"] == name)


def test_empty_folder_exits_zero(tmp_path):
    folder = notes(tmp_path, {})
    rc, out, _ = run_main(mod, [folder, *ASOF])
    assert rc == 0
    assert "No 1:1 notes found." in out
    rc, _, err = run_main(mod, [str(tmp_path / "missing"), *ASOF])
    assert rc == 2 and "notes folder not found" in err


def test_malformed_heading_and_checkbox_are_reported(tmp_path):
    folder = notes(tmp_path, {"kim-example.md": "## 2026-09-30\n- [?] me: odd mark\n## next week\n- topic: lost\n"})
    rc, rep = run_json(mod, [folder, *ASOF, "--json"])
    assert "kim-example.md:3: heading 'next week' is not a date (YYYY-MM-DD); section skipped" in rep["warnings"]
    assert "kim-example.md:2: checkbox [?] not understood (use [ ] or [x]); line skipped" in rep["warnings"]
    kim = person(rep, "kim example")
    assert kim["open_commitments"] == [] and kim["sessions"] == ["2026-09-30"]


def test_cadence_gap_is_flagged_and_per_person_cadence_applies(tmp_path):
    folder = notes(tmp_path, {"alex-example.md": ALEX})
    rc, rep = run_json(mod, [folder, *ASOF, "--json"])
    alex = person(rep, "Alex Example")
    assert rc == 1
    assert alex["cadence_days"] == 7 and alex["days_since_last"] == 15 and alex["past_cadence"] is True
    assert alex["gaps_over_cadence"] == [{"from": "2026-09-01", "to": "2026-09-20", "days": 19}]
    rc, rep = run_json(mod, [folder, *ASOF, "--json", "--cadence", "Alex Example=30", "--as-of", "2026-09-25"])
    assert person(rep, "Alex Example")["past_cadence"] is False


def test_open_commitments_by_side_with_overdue_and_closed(tmp_path):
    folder = notes(tmp_path, {"alex-example.md": ALEX})
    _, rep = run_json(mod, [folder, *ASOF, "--json"])
    alex = person(rep, "Alex Example")
    assert [(c["side"], c["text"], c["overdue_days"]) for c in alex["open_commitments"]] == [
        ("mine", "send the training budget link", 25),
        ("theirs", "pick a conference talk", None),
    ]
    assert alex["closed_commitments"] == 1
    assert alex["open_commitments"][0]["source"] == "alex-example.md:6"


def test_topics_raised_more_than_once_across_both_formats(tmp_path):
    folder = notes(tmp_path, {"alex-example.md": ALEX, "2026-09-28.md": DATED})
    _, rep = run_json(mod, [folder, *ASOF, "--json"])
    alex = person(rep, "Alex Example")
    assert alex["sessions"] == ["2026-09-01", "2026-09-20", "2026-09-28"]
    assert alex["repeated_topics"] == [{"topic": "On-call rota", "dates": ["2026-09-01", "2026-09-20", "2026-09-28"]}]
    sam = person(rep, "Sam Sample")
    assert [c["side"] for c in sam["open_commitments"]] == ["mine", "side not stated"]
    assert [p["person"] for p in rep["people"]] == ["Alex Example", "Sam Sample"]


def test_no_scores_ratings_or_comparisons_in_the_output(tmp_path):
    folder = notes(tmp_path, {"alex-example.md": ALEX, "2026-09-28.md": DATED})
    _, rep = run_json(mod, [folder, *ASOF, "--json"])
    expected = {
        "person",
        "cadence_days",
        "sessions",
        "last",
        "days_since_last",
        "past_cadence",
        "gaps_over_cadence",
        "open_commitments",
        "closed_commitments",
        "repeated_topics",
        "last_topics",
    }
    for ledger in rep["people"]:
        assert set(ledger) == expected
    assert set(rep) == {"as_of", "default_cadence_days", "people", "warnings", "flagged"}


def test_refuses_a_folder_inside_a_repository(tmp_path):
    (tmp_path / ".git").mkdir()
    folder = notes(tmp_path, {"alex-example.md": ALEX})
    rc, out, err = run_main(mod, [folder, *ASOF])
    assert rc == 3 and out == ""
    assert ".git exists, so the path is inside a repository" in err
    rc, out, err = run_main(mod, [folder, *ASOF, "--allow-shared"])
    assert rc == 1 and "Alex Example" in out
    assert "warning" in err and "--allow-shared" in err


def test_refuses_a_path_with_a_team_drive_name_and_output_into_a_repo(tmp_path):
    team = tmp_path / "Team Shared Drive"
    team.mkdir()
    folder = notes(team, {"alex-example.md": ALEX})
    rc, _, err = run_main(mod, [folder, *ASOF])
    assert rc == 3 and 'the path contains "Team Shared Drive"' in err
    private = tmp_path / "private"
    private.mkdir()
    (private / "alex-example.md").write_text(ALEX, encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    rc, _, err = run_main(mod, [str(private), *ASOF, "--output", str(repo / "ledger.md")])
    assert rc == 3 and "refusing to use the output" in err
    assert not (repo / "ledger.md").exists()


def test_person_filter_and_output_file(tmp_path):
    folder = notes(tmp_path, {"alex-example.md": ALEX, "2026-09-28.md": DATED})
    out = tmp_path / "prep.md"
    rc, stdout, _ = run_main(mod, [folder, *ASOF, "--person", "sam sample", "--output", str(out)])
    text = out.read_text(encoding="utf-8")
    assert rc == 0 and stdout == ""
    assert "## Sam Sample" in text and "Alex Example" not in text
    rc, _, err = run_main(mod, [folder, *ASOF, "--person", "Nobody Here"])
    assert rc == 2 and "no notes found" in err


GOLDEN = """\
# 1:1 ledger (private)

As of 2026-10-05. Expected cadence 14 days unless set per person. Keep this file local. It holds no ratings, \
rankings, sentiment or comparisons between people.

## Alex Example

- 1:1s on record: 3 (first 2026-09-01, last 2026-09-28)
- Days since last 1:1: 7 (expected every 7 days): within cadence
- Earlier gaps longer than cadence: 2026-09-01 to 2026-09-20 (19 days), 2026-09-20 to 2026-09-28 (8 days)
- Commitments closed on record: 1

### Open commitments: mine

- [ ] send the training budget link (raised 2026-09-01, due 2026-09-10, overdue by 25 days; alex-example.md:6)

### Open commitments: theirs

- [ ] pick a conference talk (raised 2026-09-20, due 2026-10-20; alex-example.md:13)

### Topics raised more than once

- On-call rota (2026-09-01, 2026-09-20, 2026-09-28)

### Topics at the last 1:1

- On-call rota

## Sam Sample

- 1:1s on record: 1 (first 2026-09-28, last 2026-09-28)
- Days since last 1:1: 7 (expected every 14 days): within cadence
- Earlier gaps longer than cadence: none
- Commitments closed on record: 0

### Open commitments: mine

- [ ] introduce a mentor (raised 2026-09-28; 2026-09-28.md:5)

### Open commitments: theirs

None.

### Open commitments: side not stated

- [ ] book a room (raised 2026-09-28; 2026-09-28.md:6)

### Topics raised more than once

None.

### Topics at the last 1:1

- Mentoring plan
"""


def test_golden_markdown(tmp_path):
    folder = notes(tmp_path, {"alex-example.md": ALEX, "2026-09-28.md": DATED})
    rc, out, _ = run_main(mod, [folder, *ASOF])
    assert rc == 1
    assert out == GOLDEN


def test_help():
    rc, out, _ = run_main(mod, ["--help"])
    assert rc == 0
    for flag in ("--as-of", "--cadence-days", "--cadence", "--person", "--allow-shared", "--json", "--output"):
        assert flag in out
