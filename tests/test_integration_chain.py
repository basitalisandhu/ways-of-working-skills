"""One end-to-end chain across four skills, run through the `ways-of-working` dispatcher in child processes the way a
user runs it: a Terraform plan goes to change-request-writer; its JSON drives the CAB meeting note and the decision
record; meeting-actions-ledger and decision-log read those; weekly-status-rollup reports the change PR, and its own
note feeds the next week's run as --last-week. Synthetic data only."""

from __future__ import annotations

import json
import subprocess
import sys

from conftest import ROOT

CLI = ROOT / "scripts" / "cli.py"
PLAN = {
    "format_version": "1.2",
    "terraform_version": "1.9.5",
    "resource_changes": [
        {"address": "aws_iam_role.ci", "mode": "managed", "type": "aws_iam_role", "change": {"actions": ["update"]}},
        {"address": "aws_db_instance.main", "mode": "managed", "type": "aws_db_instance",
         "change": {"actions": ["delete", "create"]}},
    ],
}


def cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, encoding="utf-8",
                          timeout=60, check=False)


def test_change_request_to_cab_actions_decision_and_weekly_status(tmp_path):
    # 1. The change record.
    (tmp_path / "plan.json").write_text(json.dumps(PLAN), encoding="utf-8")
    cr = cli("change-request", str(tmp_path / "plan.json"), "--change-id", "CHG-1042", "--json")
    assert cr.returncode in (0, 1), cr.stderr
    record = json.loads(cr.stdout)
    assert record["risk_tier"] == "high"
    replaced = [r["address"] for r in record["resources"] if r["action"] == "replace"]
    reviewed = [r["item"] for r in record["needs_reviewer"]]
    assert replaced == ["aws_db_instance.main"] and reviewed == ["aws_iam_role.ci"]

    # 2. The CAB meeting note, written from the change record: one action per replaced or flagged resource.
    notes = tmp_path / "notes"
    notes.mkdir()
    lines = ["# CAB 2026-10-14", ""]
    lines += [f"- [ ] Platform team: rehearse the restore of {a} before CHG-1042 (due 2026-10-20)" for a in replaced]
    lines += [f"- [x] Security: review the {a} change" for a in reviewed]
    (notes / "2026-10-14-cab.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    ledger = cli("meeting-ledger", str(notes), "--as-of", "2026-10-26", "--out", str(tmp_path / "ledger"))
    assert ledger.returncode == 1, ledger.stderr  # one overdue action needs a person
    actions = json.loads((tmp_path / "ledger" / "ledger.json").read_text(encoding="utf-8"))["actions"]
    overdue = [a for a in actions if "overdue" in a["flags"]]
    assert [a["text"] for a in overdue] == ["rehearse the restore of aws_db_instance.main before CHG-1042"]
    assert overdue[0]["sources"] == ["2026-10-14-cab.md:3"]
    assert [a["status"] for a in actions if a["owner"] == "Security"] == ["done"]

    # 3. The CAB decision, recorded and indexed.
    decisions = tmp_path / "decisions"
    decisions.mkdir()
    (decisions / "D-0031.md").write_text(
        "---\nid: D-0031\ntitle: Approve CHG-1042 once the restore is rehearsed\nstatus: active\n"
        f"decided-by: change advisory board\ndate: 2026-10-14\nreview-date: 2027-04-14\n---\n\n## Context\n"
        f"The plan is risk tier {record['risk_tier']} and replaces {replaced[0]}.\n\n## Decision\n"
        "Approved once the restore is rehearsed.\n", encoding="utf-8")
    log = cli("decision-log", str(decisions), "--as-of", "2026-10-26", "--index", str(decisions / "INDEX.md"))
    assert log.returncode == 0, log.stdout + log.stderr
    index = (decisions / "INDEX.md").read_text(encoding="utf-8")
    assert "D-0031" in index and "| Approve CHG-1042 once the restore is rehearsed | active | 2026-10-14 |" in index

    # 4. The lead's weekly status shows the change PR in review; next week's run reads that note and carries it.
    pr = {"number": 41, "title": "Replace the main database (CHG-1042)", "state": "OPEN", "isDraft": False,
          "url": "https://github.com/OWNER/infra/pull/41", "updatedAt": "2026-10-15T09:00:00Z", "labels": []}
    (tmp_path / "prs.json").write_text(json.dumps([pr]), encoding="utf-8")
    week1 = cli("weekly-status", "--prs", str(tmp_path / "prs.json"), "--as-of", "2026-10-16",
                "--output", str(tmp_path / "week1.md"))
    assert week1.returncode == 0, week1.stderr
    note1 = (tmp_path / "week1.md").read_text(encoding="utf-8")
    assert "infra#41" in note1 and "CHG-1042" in note1
    week2 = cli("weekly-status", "--prs", str(tmp_path / "prs.json"), "--as-of", "2026-10-23",
                "--last-week", str(tmp_path / "week1.md"), "--json")
    assert week2.returncode == 0, week2.stderr
    carried = json.loads(week2.stdout)["groups"]["carried"]
    assert [(i["ref"], i["carried_weeks"]) for i in carried] == [("infra#41", 1)]
