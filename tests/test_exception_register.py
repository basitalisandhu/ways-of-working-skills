"""Tests for exception_register.py. Registers are synthetic CSV and Markdown files written in each test."""
from __future__ import annotations

from conftest import load_script, run_json, run_main, write_files

mod = load_script("risk-exception-register", "exception_register.py")
AS_OF = ["--as-of", "2026-10-05"]

CSV = """
    id,system,control,reason,approver,granted,expires,Compensating Controls,requester,status,renewal_of
    EX-1,legacy-ftp,A.8.20,vendor upload,Head of IT,2025-01-01,2025-07-01,source IP allow list,,,
    EX-2,legacy-ftp,A.8.20,vendor upload,Head of IT,2025-07-01,2026-01-01,source IP allow list,,,EX-1
    EX-3,legacy-ftp,A.8.20,vendor upload,Head of IT,2026-01-01,2026-07-01,source IP allow list,,,EX-2
    EX-4,legacy-ftp,A.8.20,vendor upload,Head of IT,2026-07-01,2026-10-20,source IP allow list,,,EX-3
    EX-5,build-host,A.8.9,unsupported OS,,2026-01-01,2026-09-01,none,Build team,,
    EX-6,wiki,A.5.15,shared login,Docs team,2026-02-01,2027-01-01,MFA on the proxy,Docs team,,
    EX-7,crm,A.8.5,old client,Head of IT,2025-01-01,2025-06-01,n/a,,closed,
    EX-8,crm,A.8.5,short row
    """


def reg(tmp_path, text=CSV, name="register.csv"):
    return str(write_files(tmp_path, {name: text}) / name)


def rules_for(rep: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for f in rep["findings"]:
        out.setdefault(f["rule"], []).append(f["id"])
    return out


def test_expired_and_expiring(tmp_path):
    rc, rep = run_json(mod, [reg(tmp_path), *AS_OF])
    assert rc == 1
    found = rules_for(rep)
    assert found["expired"] == ["EX-5"]
    assert found["expiring"] == ["EX-4"]
    _, rep = run_json(mod, [reg(tmp_path), *AS_OF, "--window", "10"])
    assert "expiring" not in rules_for(rep)


def test_missing_approver_compensating_control_and_self_approval(tmp_path):
    found = rules_for(run_json(mod, [reg(tmp_path), *AS_OF])[1])
    assert found["missing-approver"] == ["EX-5"]
    assert found["missing-compensating-control"] == ["EX-5"]
    assert found["approver-is-requester"] == ["EX-6"]
    assert "EX-7" not in sum(found.values(), [])  # closed rows are not checked


def test_repeated_renewals_follow_the_renewal_chain(tmp_path):
    _, rep = run_json(mod, [reg(tmp_path), *AS_OF])
    renewal = [f for f in rep["findings"] if f["rule"] == "repeated-renewal"]
    assert [(f["id"], f["line"]) for f in renewal] == [("EX-4", 5)]
    assert "EX-1, EX-2, EX-3, EX-4" in renewal[0]["detail"]
    status = {e["id"]: e["status"] for e in rep["exceptions"]}
    assert status["EX-1"] == status["EX-3"] == "renewed" and status["EX-4"] == "open"
    _, rep = run_json(mod, [reg(tmp_path), *AS_OF, "--renewal-limit", "3"])
    assert "repeated-renewal" not in rules_for(rep)


def test_renewals_without_links_group_by_system_and_control(tmp_path):
    text = "id,system,control,reason,approver,granted,expires,compensating_controls\n" + "".join(
        f"R-{i},Payroll,A.8.2,reason,CISO,2026-0{i}-01,2027-01-01,logging\n" for i in range(1, 5))
    _, rep = run_json(mod, [reg(tmp_path, text), *AS_OF])
    assert rules_for(rep) == {"repeated-renewal": ["R-4"]}


def test_malformed_rows_and_bad_dates_are_data_problems(tmp_path):
    text = CSV + ("    EX-9,crm,A.8.5,reason,CISO,2026-01-01,soon,logging,,,\n"
                  "    EX-6,dup,A.1,r,CISO,2026-01-01,2027-01-01,x,,,\n")
    _, rep = run_json(mod, [reg(tmp_path, text), *AS_OF])
    data = {(f["rule"], f["id"]) for f in rep["findings"] if f["section"] == "data"}
    assert data == {("malformed-row", "EX-8"), ("invalid-date", "EX-9"), ("duplicate-id", "EX-6")}
    row = next(f for f in rep["findings"] if f["rule"] == "malformed-row")
    assert row["detail"] == "expected 11 cells, found 4" and row["line"] == 9


def test_markdown_table_input(tmp_path):
    md = """
        # Exceptions

        | ID | System | Control | Reason | Approver | Granted | Expires | Compensating controls |
        |---|---|---|---|---|---|---|---|
        | EX-1 | wiki | A.5.15 | shared \\| team login | CISO | 2026-01-01 | 2026-10-01 | proxy MFA |
        | EX-2 | crm | A.8.5 | legacy | CISO | 2026-01-01 | 2027-01-01 | logging |

        Notes after the table.
        """
    rc, rep = run_json(mod, [reg(tmp_path, md, "register.md"), *AS_OF])
    assert rc == 1
    assert rules_for(rep) == {"expired": ["EX-1"]}
    assert rep["findings"][0]["line"] == 5


def test_empty_and_bad_inputs_exit_2(tmp_path):
    rc, _, err = run_main(mod, [reg(tmp_path, ""), *AS_OF])
    assert rc == 2 and "file is empty" in err
    rc, _, err = run_main(mod, [reg(tmp_path, "id,system\nEX-1,wiki\n"), *AS_OF])
    assert rc == 2 and "missing required column(s): control" in err
    rc, _, err = run_main(mod, [reg(tmp_path, "# no table here\n", "register.md"), *AS_OF])
    assert rc == 2 and "no Markdown table" in err
    rc, _, err = run_main(mod, [str(tmp_path / "missing.csv")])
    assert rc == 2 and "file not found" in err


def test_clean_register_exits_0(tmp_path):
    text = "id,system,control,reason,approver,granted,expires,compensating_controls\n" \
           "EX-1,wiki,A.5.15,reason,CISO,2026-01-01,2027-01-01,proxy MFA\n"
    rc, out, _ = run_main(mod, [reg(tmp_path, text), *AS_OF])
    assert rc == 0 and "No findings." in out


GOLDEN = """\
# Exception review agenda: register.csv

As of 2026-10-05. 8 row(s), 3 open exception(s), 7 finding(s). Expiring means within 30 days; renewals above 2 \
are flagged.

## 1. Expired (1)

- EX-5 (line 6): expired 2026-09-01 (34 days before 2026-10-05)
  - Decision (renew, close or escalate), owner and date: ____

## 2. Expiring soon (1)

- EX-4 (line 5): expires 2026-10-20 (in 15 days)
  - Decision (renew, close or escalate), owner and date: ____

## 3. No approver recorded (1)

- EX-5 (line 6): approver is empty
  - Decision (renew, close or escalate), owner and date: ____

## 4. No compensating control recorded (1)

- EX-5 (line 6): compensating_controls is 'none'
  - Decision (renew, close or escalate), owner and date: ____

## 5. Approver is also the requester (1)

- EX-6 (line 7): requester and approver are the same
  - Decision (renew, close or escalate), owner and date: ____

## 6. Renewed repeatedly (1)

- EX-4 (line 5): granted 4 times (EX-1, EX-2, EX-3, EX-4) for legacy-ftp / A.8.20
  - Decision (renew, close or escalate), owner and date: ____

## 7. Data problems (1)

- EX-8 (line 9) [malformed-row]: expected 11 cells, found 4
"""


def test_golden_agenda(tmp_path):
    rc, out, _ = run_main(mod, [reg(tmp_path), *AS_OF])
    assert rc == 1
    assert out == GOLDEN
