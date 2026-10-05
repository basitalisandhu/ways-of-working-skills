#!/usr/bin/env python3
"""exception_register.py: lint a register of time-bound security exceptions and risk acceptances.

Input: one file, either a CSV (.csv) or a Markdown file holding a pipe table (any other suffix). Header names are
matched without regard to case, spaces, hyphens or underscores.
  Required columns: id, system, control, reason, approver, granted, expires, compensating_controls
  Optional columns: requester, status (open or closed; empty means open), renewal_of (the id this row renews)
Dates are YYYY-MM-DD. For a Markdown file the first table whose header has both "id" and "expires" is read.

Rules (each finding cites the row id and its line in the file):
  * expired: expires before --as-of;
  * expiring: expires on or after --as-of and within --window days (default 30);
  * missing-approver: approver empty;
  * missing-compensating-control: compensating_controls empty, or "none", "n/a", "na" or "-";
  * approver-is-requester: requester given and equal to approver (case-insensitive);
  * repeated-renewal: the same exception was granted more than --renewal-limit + 1 times (default limit 2, so a third
    renewal is flagged). Rows belong to the same exception when linked by renewal_of, or, for rows without
    renewal_of links, when they share system and control (case-insensitive);
  * invalid-date, duplicate-id, malformed-row (wrong number of cells), missing-id, broken-renewal-link (renewal_of
    names no id in the file): data problems.
Closed rows, and rows renewed by a later row, are not checked for expiry. Nothing is decided: the agenda lists what
needs a decision and leaves the decision blank.

Output: a review agenda in Markdown on standard output (or --out FILE); --json prints the computed data instead. The
output is deterministic for the same file and --as-of.

Exit codes: 0 no findings, 1 at least one finding, 2 bad input (file missing or empty, no table, missing required
columns, bad --as-of).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import re
import sys
from pathlib import Path

REQUIRED = ("id", "system", "control", "reason", "approver", "granted", "expires", "compensating_controls")
OPTIONAL = ("requester", "status", "renewal_of")
EMPTY_CONTROL = {"", "none", "n/a", "na", "-"}
SECTIONS = [
    ("expired", "Expired"),
    ("expiring", "Expiring soon"),
    ("missing-approver", "No approver recorded"),
    ("missing-compensating-control", "No compensating control recorded"),
    ("approver-is-requester", "Approver is also the requester"),
    ("repeated-renewal", "Renewed repeatedly"),
    ("data", "Data problems"),
]
DATA_RULES = {"invalid-date", "duplicate-id", "malformed-row", "missing-id", "broken-renewal-link"}


class InputError(Exception):
    """Bad input: the message is printed and the script exits 2."""


def key(name: str) -> str:
    return re.sub(r"[\s_-]+", "_", name.strip().lower())


def split_md_row(line: str) -> list[str]:
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", body)]


def read_rows(path: Path) -> tuple[list[str], list[tuple[int, list[str]]]]:
    if not path.is_file():
        raise InputError(f"{path}: file not found")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if not text.strip():
        raise InputError(f"{path}: file is empty")
    if path.suffix.lower() == ".csv":
        reader = csv.reader(io.StringIO(text))
        rows = [(reader.line_num, r) for r in reader]
        rows = [(n, r) for n, r in rows if any(c.strip() for c in r)]
        if not rows:
            raise InputError(f"{path}: no rows")
        return [key(c) for c in rows[0][1]], [(n, [c.strip() for c in r]) for n, r in rows[1:]]
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        header = [key(c) for c in split_md_row(line)]
        if (
            "id" in header
            and "expires" in header
            and i + 1 < len(lines)
            and re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", lines[i + 1])
        ):
            rows = []
            for n in range(i + 2, len(lines)):
                if not lines[n].strip().startswith("|"):
                    break
                rows.append((n + 1, split_md_row(lines[n])))
            return header, rows
    raise InputError(f"{path}: no Markdown table with 'id' and 'expires' columns found")


def parse_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def analyse(
    header: list[str], rows: list[tuple[int, list[str]]], as_of: dt.date, window: int, renewal_limit: int
) -> dict:
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise InputError("missing required column(s): " + ", ".join(missing))
    findings: list[dict] = []
    records: list[dict] = []

    def add(rule: str, rec: dict | None, line: int, detail: str) -> None:
        findings.append({"rule": rule, "id": rec["id"] if rec else None, "line": line, "detail": detail})

    for line, cells in rows:
        if len(cells) != len(header):
            add(
                "malformed-row",
                {"id": cells[0]} if cells and cells[0] else None,
                line,
                f"expected {len(header)} cells, found {len(cells)}",
            )
            continue
        rec = {c: cells[header.index(c)] if c in header else "" for c in REQUIRED + OPTIONAL}
        rec["line"] = line
        if not rec["id"]:
            add("missing-id", None, line, "row has no id")
            continue
        records.append(rec)
    seen: dict[str, int] = {}
    for rec in records:
        if rec["id"] in seen:
            add("duplicate-id", rec, rec["line"], f"id also used on line {seen[rec['id']]}")
        else:
            seen[rec["id"]] = rec["line"]
        for field in ("granted", "expires"):
            rec[field + "_date"] = parse_date(rec[field])
            if rec[field + "_date"] is None:
                add(
                    "invalid-date",
                    rec,
                    rec["line"],
                    f"{field} {rec[field]!r} is not a YYYY-MM-DD date" if rec[field] else f"{field} is empty",
                )

    # Group rows into exceptions: renewal_of chains first, then system and control for unlinked rows.
    by_id = {r["id"]: r for r in records}
    parent: dict[str, str] = {}

    def root(i: str) -> str:
        visited = set()
        while i in parent and i not in visited:
            visited.add(i)
            i = parent[i]
        return i

    linked = set()
    for r in records:
        if r["renewal_of"]:
            linked.update((r["id"], r["renewal_of"]))
            if r["renewal_of"] in by_id:
                parent[root(r["id"])] = root(r["renewal_of"])
            else:
                add("broken-renewal-link", r, r["line"], f"renewal_of {r['renewal_of']!r} matches no id")
    groups: dict[str, list[dict]] = {}
    for r in records:
        gk = "chain:" + root(r["id"]) if r["id"] in linked else f"pair:{r['system'].lower()}|{r['control'].lower()}"
        groups.setdefault(gk, []).append(r)
    renewed: set[str] = set()
    for members in groups.values():
        members.sort(key=lambda r: (r["granted_date"] or dt.date.min, r["line"]))
        renewed.update(m["id"] for m in members[:-1])
        if len(members) - 1 > renewal_limit:
            last = members[-1]
            add(
                "repeated-renewal",
                last,
                last["line"],
                f"granted {len(members)} times ({', '.join(m['id'] for m in members)}) for "
                f"{last['system']} / {last['control']}",
            )

    for r in records:
        active = r["status"].strip().lower() != "closed" and r["id"] not in renewed
        exp = r["expires_date"]
        if active and exp is not None:
            days = (exp - as_of).days
            if days < 0:
                add("expired", r, r["line"], f"expired {exp.isoformat()} ({-days} days before {as_of.isoformat()})")
            elif days <= window:
                add("expiring", r, r["line"], f"expires {exp.isoformat()} (in {days} days)")
        if not active:
            continue
        if not r["approver"]:
            add("missing-approver", r, r["line"], "approver is empty")
        if r["compensating_controls"].strip().lower() in EMPTY_CONTROL:
            add(
                "missing-compensating-control",
                r,
                r["line"],
                f"compensating_controls is {r['compensating_controls']!r}"
                if r["compensating_controls"]
                else "compensating_controls is empty",
            )
        if r["requester"] and r["approver"] and r["requester"].strip().lower() == r["approver"].strip().lower():
            add("approver-is-requester", r, r["line"], "requester and approver are the same")

    for f in findings:
        if f["rule"] in DATA_RULES:
            f["section"] = "data"
        else:
            f["section"] = f["rule"]
    findings.sort(key=lambda f: ([s for s, _ in SECTIONS].index(f["section"]), f["line"], f["rule"]))
    return {
        "as_of": as_of.isoformat(),
        "window_days": window,
        "renewal_limit": renewal_limit,
        "rows": len(rows),
        "exceptions": [
            {
                "id": r["id"],
                "system": r["system"],
                "control": r["control"],
                "expires": r["expires"],
                "status": "closed"
                if r["status"].strip().lower() == "closed"
                else ("renewed" if r["id"] in renewed else "open"),
                "line": r["line"],
            }
            for r in records
        ],
        "findings": findings,
    }


def render(rep: dict, source: str) -> str:
    out = [f"# Exception review agenda: {source}", ""]
    open_n = sum(1 for e in rep["exceptions"] if e["status"] == "open")
    out.append(
        f"As of {rep['as_of']}. {rep['rows']} row(s), {open_n} open exception(s), {len(rep['findings'])} "
        f"finding(s). Expiring means within {rep['window_days']} days; renewals above {rep['renewal_limit']} "
        "are flagged."
    )
    out.append("")
    n = 0
    for section, heading in SECTIONS:
        rows = [f for f in rep["findings"] if f["section"] == section]
        if not rows:
            continue
        n += 1
        out.append(f"## {n}. {heading} ({len(rows)})")
        out.append("")
        for f in rows:
            who = f["id"] or "(no id)"
            rule = f" [{f['rule']}]" if section == "data" else ""
            out.append(f"- {who} (line {f['line']}){rule}: {f['detail']}")
            if section != "data":
                out.append("  - Decision (renew, close or escalate), owner and date: ____")
        out.append("")
    if n == 0:
        out.append("No findings. Record the review date and who attended.")
        out.append("")
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="exception_register.py",
        description="Lint a register of security exceptions and risk acceptances (CSV or Markdown table) and print "
        "a review agenda.",
        epilog="Exit codes: 0 no findings, 1 at least one finding, 2 bad input.",
    )
    p.add_argument("register", help="register file: .csv, or Markdown with a pipe table")
    p.add_argument("--as-of", default=None, help="date to judge expiry against, YYYY-MM-DD (default: today)")
    p.add_argument("--window", type=int, default=30, help="days ahead that count as expiring (default 30)")
    p.add_argument(
        "--renewal-limit",
        type=int,
        default=2,
        help="renewals allowed before a row is flagged as renewed repeatedly (default 2)",
    )
    p.add_argument("--json", action="store_true", help="print the computed data as JSON instead of Markdown")
    p.add_argument("--out", default=None, help="write the output to this file instead of standard output")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        as_of = dt.date.today() if args.as_of is None else parse_date(args.as_of)
        if as_of is None:
            raise InputError(f"--as-of {args.as_of!r} is not a YYYY-MM-DD date")
        if args.window < 0 or args.renewal_limit < 0:
            raise InputError("--window and --renewal-limit must be 0 or more")
        path = Path(args.register)
        header, rows = read_rows(path)
        rep = analyse(header, rows, as_of, args.window, args.renewal_limit)
    except InputError as exc:
        print(f"exception_register.py: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(rep, indent=2, sort_keys=True) + "\n" if args.json else render(rep, path.name)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 1 if rep["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
