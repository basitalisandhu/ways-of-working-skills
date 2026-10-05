"""Tests for risk_register_refresh.py. Registers and findings are synthetic, written under tmp_path by each test.

As of 2026-10-05. Finding ids are short example strings, not real ARNs.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import load_script, run_json, run_main

mod = load_script("risk-register-refresh", "risk_register_refresh.py")
ASOF = ["--as-of", "2026-10-05"]
HEADER = "ID,Title,Owner,Likelihood,Impact,Treatment,Review Date,Status,Finding Refs\n"
CURRENT = HEADER + (
    "R-01,Public storage buckets,Platform lead,Medium,High,Mitigate,2026-12-01,Open,S3.1;S3.8\n"
    "R-02,Root account use,Security lead,Low,High,Mitigate,2026-09-01,Open,IAM.6\n"
    "R-03,Supplier outage,TBC,Medium,Medium,Accept,2026-11-30,Open,\n"
    "R-04,Retired system,Ops lead,Low,Low,Avoid,2026-01-01,Closed,\n"
    "R-05,Weak MFA,Security lead,High,High,Mitigate,31/12/2026,Open,IAM.5\n"
)
PREVIOUS = (
    "id,title,owner,likelihood,impact,treatment,review_date,status\n"
    "R-01,Public storage buckets,Platform lead,Low,High,Mitigate,2026-09-01,Open\n"
    "R-02,Root account use,Security lead,Low,High,Mitigate,2026-09-01,Open\n"
    "R-09,Legacy VPN,Ops lead,Low,Low,Avoid,2026-06-01,Open\n"
)


def asff(n: int, control: str, title: str, severity: str = "HIGH", **extra) -> dict:
    finding = {
        "SchemaVersion": "2018-10-08",
        "Id": f"example-finding-{n}",
        "Title": title,
        "Compliance": {"SecurityControlId": control, "Status": "FAILED"},
        "Severity": {"Label": severity},
        "Workflow": {"Status": "NEW"},
        "RecordState": "ACTIVE",
    }
    finding.update(extra)
    return finding


FINDINGS = {
    "Findings": [
        asff(1, "S3.8", "Buckets should block public access"),
        asff(2, "EC2.8", "Instances should use IMDSv2"),
        asff(3, "EC2.8", "Instances should use IMDSv2", severity="MEDIUM"),
        asff(4, "IAM.6", "Root MFA", Compliance={"SecurityControlId": "IAM.6", "Status": "PASSED"}),
        asff(5, "IAM.5", "Console users need MFA", Workflow={"Status": "SUPPRESSED"}),
    ]
}


def files(tmp_path: Path, current=CURRENT, previous=PREVIOUS, findings=FINDINGS) -> list[str]:
    (tmp_path / "register.csv").write_text(current, encoding="utf-8")
    argv = [str(tmp_path / "register.csv")]
    if previous is not None:
        (tmp_path / "previous.csv").write_text(previous, encoding="utf-8")
        argv += ["--previous", str(tmp_path / "previous.csv")]
    if findings is not None:
        (tmp_path / "findings.json").write_text(json.dumps(findings), encoding="utf-8")
        argv += ["--findings", str(tmp_path / "findings.json")]
    return argv


def ids(rows: list[dict]) -> list[str]:
    return [r["id"] for r in rows]


def test_header_only_register_has_nothing_to_review(tmp_path):
    rc, out, _ = run_main(mod, [*files(tmp_path, current=HEADER, previous=None, findings=None), *ASOF])
    assert rc == 0
    assert "Nothing to review." in out
    (tmp_path / "register.csv").write_text("", encoding="utf-8")
    rc, _, err = run_main(mod, [str(tmp_path / "register.csv"), *ASOF])
    assert rc == 2 and "empty file" in err


def test_malformed_register_and_findings(tmp_path):
    rc, _, err = run_main(mod, [*files(tmp_path, current="id,title,owner\nR-1,x,y\n", previous=None), *ASOF])
    assert rc == 2
    assert "missing column(s) likelihood, impact, treatment, review_date, status" in err
    _, rep = run_json(mod, [*files(tmp_path, current=CURRENT + "R-06,short,row\n"), *ASOF, "--json"])
    assert "register.csv:7: 3 fields, expected 9; row skipped" in rep["warnings"]
    (tmp_path / "findings.json").write_text("{broken", encoding="utf-8")
    rc, _, err = run_main(mod, [str(tmp_path / "register.csv"), "--findings", str(tmp_path / "findings.json")])
    assert rc == 2 and "findings.json: invalid JSON" in err


def test_owner_and_review_date_checks(tmp_path):
    rc, rep = run_json(mod, [*files(tmp_path), *ASOF, "--json"])
    assert rc == 1
    assert ids(rep["no_owner"]) == ["R-03"]
    assert [(r["id"], r["days_overdue"]) for r in rep["overdue_reviews"]] == [("R-02", 34)]
    assert [(r["id"], r["reason"]) for r in rep["missing_review_dates"]] == [("R-05", "unreadable (use YYYY-MM-DD)")]
    assert "R-04" not in ids(rep["overdue_reviews"])  # closed risks are not reviewed


def test_new_findings_are_grouped_and_closed_ones_ignored(tmp_path):
    _, rep = run_json(mod, [*files(tmp_path), *ASOF, "--json"])
    assert rep["findings"] == {"sources": [{"file": "findings.json", "read": 5, "open": 3}], "open": 3, "closed": 2}
    assert rep["new_findings"] == [
        {
            "key": "EC2.8",
            "title": "Instances should use IMDSv2",
            "count": 2,
            "severities": ["HIGH", "MEDIUM"],
            "example_id": "example-finding-2",
            "sources": ["findings.json"],
        }
    ]


def test_csv_findings_and_risks_without_a_supporting_finding(tmp_path):
    argv = files(tmp_path, findings=None)
    (tmp_path / "scan.csv").write_text(
        "id,title,check_id,severity,status\nf-1,Root key present,IAM.6,critical,open\nf-2,Old TLS,TLS.1,low,fixed\n",
        encoding="utf-8",
    )
    _, rep = run_json(mod, [*argv, "--findings", str(tmp_path / "scan.csv"), *ASOF, "--json"])
    assert [(r["id"], r["reason"]) for r in rep["unsupported_risks"]] == [
        ("R-01", "no open finding matches"),
        ("R-03", "no finding_refs recorded"),
        ("R-05", "no open finding matches"),
    ]
    assert rep["new_findings"] == []


def test_likelihood_impact_changes_and_membership_without_scoring(tmp_path):
    _, rep = run_json(mod, [*files(tmp_path), *ASOF, "--json"])
    assert rep["rating_changes"] == [
        {
            "id": "R-01",
            "title": "Public storage buckets",
            "owner": "Platform lead",
            "line": 2,
            "likelihood": "Medium",
            "impact": "High",
            "changed": {"likelihood": {"from": "Low", "to": "Medium"}},
        }
    ]
    assert ids(rep["added"]) == ["R-03", "R-04", "R-05"] and ids(rep["removed"]) == ["R-09"]
    assert not any("score" in key for key in json.dumps(rep).lower().split('"'))


GOLDEN = """\
# Risk register refresh: as of 2026-10-05

Register: register.csv (5 risks, 4 open). Previous snapshot: previous.csv (3 risks). Findings: findings.json (5 \
read, 3 open).
Likelihood and impact are the register's own values, shown as written; nothing here scores or ranks risk.

## Refresh agenda

1. Name an owner for R-03 Supplier outage.
2. Review R-02 Root account use (owner Security lead): due 2026-09-01, 34 days overdue.
3. Set a review date (YYYY-MM-DD) for R-05 Weak MFA; the register has 31/12/2026.
4. Confirm the change to R-01 Public storage buckets: likelihood Low to Medium.
5. Decide on EC2.8 (2 open findings, no register entry): a new risk, a link to an existing one, or not a risk.
6. Check R-02 Root account use: no open finding matches; record other evidence, update refs, or close.
7. Check R-03 Supplier outage: no finding_refs recorded; record other evidence, update refs, or close.
8. Check R-05 Weak MFA: no open finding matches; record other evidence, update refs, or close.
9. Note R-03 Supplier outage as added since the previous snapshot.
10. Note R-04 Retired system as added since the previous snapshot.
11. Note R-05 Weak MFA as added since the previous snapshot.
12. Confirm R-09 Legacy VPN was meant to leave the register.

## Risks without an owner

| ID | Title | Status |
|---|---|---|
| R-03 | Supplier outage | Open |

## Overdue reviews

| ID | Title | Owner | Review date | Days overdue |
|---|---|---|---|---|
| R-02 | Root account use | Security lead | 2026-09-01 | 34 |

## Missing or unreadable review dates

| ID | Title | Owner | Review date | Problem |
|---|---|---|---|---|
| R-05 | Weak MFA | Security lead | 31/12/2026 | unreadable (use YYYY-MM-DD) |

## New findings with no register entry

| Finding key | Title | Open findings | Severity | Example id | Source |
|---|---|---|---|---|---|
| EC2.8 | Instances should use IMDSv2 | 2 | HIGH, MEDIUM | example-finding-2 | findings.json |

## Register risks with no supporting finding

| ID | Title | Owner | Finding refs | Reason |
|---|---|---|---|---|
| R-02 | Root account use | Security lead | IAM.6 | no open finding matches |
| R-03 | Supplier outage | TBC | - | no finding_refs recorded |
| R-05 | Weak MFA | Security lead | IAM.5 | no open finding matches |

## Likelihood or impact changes since the previous snapshot

| ID | Title | Likelihood | Impact |
|---|---|---|---|
| R-01 | Public storage buckets | Low to Medium | High (no change) |

## Risks added or removed since the previous snapshot

| ID | Title | Change |
|---|---|---|
| R-03 | Supplier outage | added |
| R-04 | Retired system | added |
| R-05 | Weak MFA | added |
| R-09 | Legacy VPN | removed |
"""


def test_golden_markdown(tmp_path):
    rc, out, _ = run_main(mod, [*files(tmp_path), *ASOF])
    assert rc == 1
    assert out == GOLDEN


def test_output_file_and_help(tmp_path):
    out = tmp_path / "refresh.md"
    rc, stdout, _ = run_main(mod, [*files(tmp_path), *ASOF, "--output", str(out)])
    assert rc == 1 and stdout == "" and out.read_text(encoding="utf-8").startswith("# Risk register refresh")
    rc, text, _ = run_main(mod, ["--help"])
    assert rc == 0
    for flag in ("--previous", "--findings", "--as-of", "--json", "--output"):
        assert flag in text
