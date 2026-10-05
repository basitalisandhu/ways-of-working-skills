"""Tests for shift_handover.py. Incident and alert exports are synthetic files written in each test."""

from __future__ import annotations

import json

from conftest import load_script, run_json, run_main, write_files

mod = load_script("shift-handover", "shift_handover.py")
AS_OF = ["--as-of", "2026-10-05T08:00:00Z"]

INCIDENTS = [
    {
        "id": "INC-101",
        "title": "Checkout latency",
        "status": "investigating",
        "opened": "2026-10-05T01:00:00Z",
        "last_update": "2026-10-05T07:30:00Z",
        "owner": "Payments on-call",
    },
    {
        "id": "INC-98",
        "title": "Queue backlog",
        "status": "monitoring",
        "opened": "2026-09-26T10:00:00Z",
        "updated": "2026-10-04T22:00:00Z",
        "owner": "",
    },
    {
        "id": "INC-102",
        "title": "Login errors",
        "status": "open",
        "opened": "2026-10-05T07:00:00+02:00",
        "owner": "Identity on-call",
    },
    {"id": "INC-99", "title": "Old disk alert", "status": "Resolved", "opened": "2026-10-01T10:00:00Z"},
]

ALERTS = """
    name,count,first_seen,last_seen,silenced_until
    DiskFull web-3,42,2026-10-04T20:00:00Z,2026-10-05T07:55:00Z,2026-10-05T14:00:00Z
    CPUHigh,3,2026-10-05T02:00:00Z,2026-10-05T03:00:00Z,
    CertExpiry,1,2026-10-01T00:00:00Z,2026-10-01T00:00:00Z,2026-10-04T00:00:00Z
    """


def make(tmp_path, incidents=INCIDENTS, alerts=ALERTS, notes=None):
    spec = {"incidents.json": incidents if isinstance(incidents, str) else json.dumps(incidents)}
    args = [str(tmp_path / "incidents.json")]
    if alerts is not None:
        spec["alerts.csv"] = alerts
        args += ["--alerts", str(tmp_path / "alerts.csv")]
    if notes is not None:
        spec["notes.txt"] = notes
        args += ["--notes", str(tmp_path / "notes.txt")]
    write_files(tmp_path, spec)
    return [*args, *AS_OF]


def rules(rep: dict) -> list[tuple[str, str]]:
    return [(f["rule"], f["ref"]) for f in rep["findings"]]


def test_open_incidents_grouped_by_age_and_closed_counted(tmp_path):
    rc, rep = run_json(mod, make(tmp_path, alerts=None))
    assert rc == 1
    assert rep["closed"] == 1
    assert [(i["id"], i["age_group"]) for i in rep["open"]] == [
        ("INC-98", "over-7d"),
        ("INC-101", "4h-24h"),
        ("INC-102", "under-4h"),
    ]
    assert next(i for i in rep["open"] if i["id"] == "INC-102")["opened"] == "2026-10-05T05:00Z"


def test_stale_and_ownerless_incidents_are_flagged(tmp_path):
    _, rep = run_json(mod, make(tmp_path, alerts=None))
    assert rules(rep) == [("no-update", "INC-98"), ("no-owner", "INC-98")]
    _, rep = run_json(mod, [*make(tmp_path, alerts=None), "--stale-hours", "2"])
    assert ("no-update", "INC-102") in rules(rep)


def test_noisy_alerts_and_silences(tmp_path):
    rc, rep = run_json(mod, make(tmp_path))
    assert rc == 1
    found = rules(rep)
    assert ("noisy-alert", "DiskFull web-3") in found
    assert ("silence-expires", "DiskFull web-3") in found
    assert ("silence-expired", "CertExpiry") in found
    assert [s["state"] for s in rep["silences"]] == ["expired", "expires this shift"]
    _, rep = run_json(mod, [*make(tmp_path), "--horizon-hours", "2", "--noisy-count", "50"])
    assert {r for r, _ in rules(rep)} == {"no-update", "no-owner", "silence-expired"}


def test_empty_incident_list_is_clean_and_empty_file_is_bad_input(tmp_path):
    rc, rep = run_json(mod, make(tmp_path, incidents="[]", alerts=None))
    assert rc == 0 and rep["open"] == [] and rep["findings"] == []
    rc, out, _ = run_main(mod, make(tmp_path, incidents="[]", alerts=None))
    assert "No open incidents in the export." in out and "Nothing flagged." in out
    rc, _, err = run_main(mod, make(tmp_path, incidents="", alerts=None))
    assert rc == 2 and "empty" in err


def test_malformed_rows_and_bad_values_are_data_problems(tmp_path):
    (tmp_path / "inc.csv").write_text(
        "id,title,status,opened,last_update,owner\n"
        "INC-1,Slow search,open,2026-10-05T07:00:00Z,,Search on-call\n"
        "INC-2,Broken,open\n"
        "INC-1,Duplicate,open,yesterday,,Search on-call\n",
        encoding="utf-8",
    )
    (tmp_path / "al.csv").write_text("name,count\nPodRestart,many\n", encoding="utf-8")
    rc, rep = run_json(mod, [str(tmp_path / "inc.csv"), "--alerts", str(tmp_path / "al.csv"), *AS_OF])
    assert rc == 1
    found = {(f["rule"], f["where"]) for f in rep["findings"]}
    assert found == {
        ("malformed-row", "line 3"),
        ("duplicate-id", "line 4"),
        ("invalid-time", "line 4"),
        ("invalid-count", "line 2"),
    }
    assert next(i for i in rep["open"] if i["title"] == "Duplicate")["age_group"] == "unknown"


def test_bad_inputs_exit_2(tmp_path):
    assert run_main(mod, [*make(tmp_path, incidents=[{"id": "X", "title": "t"}], alerts=None)])[0] == 2
    assert run_main(mod, [*make(tmp_path, incidents="{broken", alerts=None)])[0] == 2
    assert run_main(mod, [*make(tmp_path, alerts=None)[:1], "--as-of", "soon"])[0] == 2
    assert run_main(mod, [str(tmp_path / "missing.json"), *AS_OF])[0] == 2
    assert run_main(mod, [*make(tmp_path, alerts="label,value\nx,1\n")])[0] == 2


def test_golden_markdown_with_notes(tmp_path):
    out_file = tmp_path / "handover.md"
    rc, out, _ = run_main(mod, [*make(tmp_path, notes="Deploy freeze until Tuesday.\n"), "--out", str(out_file)])
    assert rc == 1 and out == ""
    text = out_file.read_text(encoding="utf-8")
    assert text.splitlines()[:9] == [
        "# Shift handover: 2026-10-05T08:00Z",
        "",
        "3 open incident(s), 1 closed in the export. Stale means no update for more than 4 hours; noisy means 10 "
        "or more firings; the next shift is 12 hours.",
        "",
        "## Open incidents",
        "",
        "### Opened over 7 days ago (1)",
        "",
        "- INC-98: Queue backlog (monitoring, no owner, opened 2026-09-26T10:00Z, last update 10h ago) "
        "**[no-update, no-owner]**",
    ]
    assert "- [silence-expires] DiskFull web-3: silence ends 2026-10-05T14:00Z (in 6 hours)" in text
    assert "| DiskFull web-3 | 42 | 2026-10-04T20:00Z | 2026-10-05T07:55Z |" in text
    assert "> Deploy freeze until Tuesday." in text
    assert text.endswith("- Incoming, acknowledged at: ____\n")


def test_help_exits_0():
    rc, out, _ = run_main(mod, ["--help"])
    assert rc == 0 and out.startswith("usage: shift_handover.py")
