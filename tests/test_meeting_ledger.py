"""Tests for meeting_ledger.py. Each test writes a synthetic notes folder into tmp_path."""

from __future__ import annotations

import json

from conftest import load_script, run_json, run_main, write_files

mod = load_script("meeting-actions-ledger", "meeting_ledger.py")
AS_OF = ["--as-of", "2026-10-05"]

NOTES = {
    "2026-09-01-sync.md": """
        # Platform sync
        - [ ] Platform team: update the restore runbook (due 2026-09-10)
        Action: rotate the shared test keys
        - [ ] @sam book the access review
        """,
    "2026-09-08-sync.md": """
        - [ ] Platform team: Update the restore runbook.
        - [x] @sam book the access review
        """,
    "2026-09-15-sync.md": """
        - [ ] Platform team: update the restore runbook
        ACTION: Security team: draft the logging standard by 2026-10-30
        """,
}


def notes(tmp_path, files=None):
    return str(write_files(tmp_path / "notes", files or NOTES))


def by_text(rep: dict) -> dict:
    return {a["text"].lower().rstrip("."): a for a in rep["actions"]}


def test_extracts_owner_due_and_status_from_each_format(tmp_path):
    rc, rep = run_json(mod, [notes(tmp_path), *AS_OF])
    assert rc == 1
    items = by_text(rep)
    assert items["draft the logging standard"]["owner"] == "Security team"
    assert items["draft the logging standard"]["due"] == "2026-10-30"
    assert items["book the access review"]["owner"] == "sam"
    assert items["book the access review"]["status"] == "done"
    assert items["rotate the shared test keys"]["owner"] is None
    assert [a["id"] for a in rep["actions"]] == ["A001", "A002", "A003", "A004"]


def test_dedupes_carry_overs_and_flags_overdue_ownerless_and_carried(tmp_path):
    _, rep = run_json(mod, [notes(tmp_path), *AS_OF])
    items = by_text(rep)
    runbook = items["update the restore runbook"]
    assert runbook["meetings_open"] == 3
    assert runbook["sources"] == ["2026-09-01-sync.md:2", "2026-09-08-sync.md:1", "2026-09-15-sync.md:1"]
    assert runbook["flags"] == ["overdue", "carried"]
    assert items["rotate the shared test keys"]["flags"] == ["ownerless"]
    assert items["draft the logging standard"]["flags"] == []
    assert items["book the access review"]["flags"] == []


def test_carry_threshold_is_configurable(tmp_path):
    _, rep = run_json(mod, [notes(tmp_path), *AS_OF, "--carry", "4"])
    assert "carried" not in by_text(rep)["update the restore runbook"]["flags"]


def test_malformed_line_is_reported_and_the_run_continues(tmp_path):
    folder = notes(
        tmp_path,
        {
            "2026-09-01-sync.md": """
        - [ ] Platform team: fix the backup alert (due 2026-13-40)
        - [ ]
        - [ ] Platform team: renew the certificate (due 2026-11-01)
        """
        },
    )
    rc, rep = run_json(mod, [folder, *AS_OF])
    assert rc == 1
    assert [(b["line"], b["problem"]) for b in rep["not_understood"]] == [
        (1, "due date '2026-13-40' is not a valid YYYY-MM-DD date"),
        (2, "action has no text"),
    ]
    assert [a["text"] for a in rep["actions"]] == ["renew the certificate"]
    rc, _, err = run_main(mod, [folder, *AS_OF])
    assert "2026-09-01-sync.md:1" in err


def test_empty_and_missing_inputs(tmp_path):
    (tmp_path / "empty").mkdir()
    rc, _, err = run_main(mod, [str(tmp_path / "empty")])
    assert rc == 2 and "no meeting notes" in err
    rc, _, err = run_main(mod, [str(tmp_path / "nope")])
    assert rc == 2 and "not a folder" in err
    folder = notes(tmp_path, {"2026-09-01-sync.md": "Just discussion, no actions.\n"})
    rc, out, _ = run_main(mod, [folder, *AS_OF])
    assert rc == 0 and "No action lines found" in out
    rc, _, err = run_main(mod, [folder, "--as-of", "05/10/2026"])
    assert rc == 2 and "--as-of" in err


def test_date_line_and_undated_files_set_the_order(tmp_path):
    folder = notes(
        tmp_path,
        {
            "b-retro.md": "Date: 2026-09-01\n- [ ] Ops: archive old tickets\n",
            "a-misc.txt": "- [x] Ops: archive old tickets\n",
        },
    )
    _, rep = run_json(mod, [folder, *AS_OF])
    assert rep["meetings"] == [{"file": "b-retro.md", "date": "2026-09-01"}, {"file": "a-misc.txt", "date": None}]
    assert rep["actions"][0]["status"] == "done"


def test_out_writes_exactly_ledger_md_and_ledger_json(tmp_path):
    folder = notes(tmp_path)
    out = tmp_path / "result"
    rc, stdout, _ = run_main(mod, [folder, *AS_OF, "--out", str(out)])
    assert rc == 1 and "wrote" in stdout
    assert sorted(p.name for p in out.iterdir()) == ["ledger.json", "ledger.md"]
    assert json.loads((out / "ledger.json").read_text(encoding="utf-8"))["as_of"] == "2026-10-05"
    assert sorted(p.name for p in (tmp_path / "notes").iterdir()) == sorted(NOTES)


GOLDEN = """\
# Action ledger

As of 2026-10-05. 3 meeting file(s), 4 action(s): 3 open, 1 done.

## Overdue (1)

- A001 update the restore runbook (Platform team, due 2026-09-10; 2026-09-15-sync.md:1)

## No owner stated (1)

- A002 rotate the shared test keys (no owner stated; 2026-09-01-sync.md:3)

## Carried over in 3 or more meetings (1)

- A001 update the restore runbook (Platform team, due 2026-09-10, open in 3 meetings; 2026-09-15-sync.md:1)

## Open by owner

### Platform team

- [ ] A001 update the restore runbook (due 2026-09-10)

### Security team

- [ ] A004 draft the logging standard (due 2026-10-30)

### No owner stated

- [ ] A002 rotate the shared test keys

## All actions

| ID | Action | Owner | Due | Status | Meetings open | Flags | Sources |
|---|---|---|---|---|---|---|---|
| A001 | update the restore runbook | Platform team | 2026-09-10 | open | 3 | overdue, carried | \
2026-09-01-sync.md:2; 2026-09-08-sync.md:1; 2026-09-15-sync.md:1 |
| A002 | rotate the shared test keys |  |  | open | 1 | ownerless | 2026-09-01-sync.md:3 |
| A003 | book the access review | sam |  | done | 1 |  | 2026-09-01-sync.md:4; 2026-09-08-sync.md:2 |
| A004 | draft the logging standard | Security team | 2026-10-30 | open | 1 |  | 2026-09-15-sync.md:2 |

## Lines not understood (0)

None.
"""


def test_golden_markdown_ledger(tmp_path):
    rc, out, _ = run_main(mod, [notes(tmp_path), *AS_OF])
    assert rc == 1
    assert out == GOLDEN
