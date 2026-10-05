#!/usr/bin/env python3
"""Quarter-on-quarter refresh of a risk register kept as CSV, against saved findings exports. Files only; no network.

Inputs:
  register             the current register CSV with columns id, title, owner, likelihood, impact, treatment,
                       review_date, status (header case, spaces and hyphens do not matter) and an optional
                       finding_refs column: finding ids, control ids or rule ids separated by ";" or ","
  --previous FILE      last quarter's register snapshot, same columns
  --findings FILE      a findings export, repeatable: Security Hub ASFF JSON ({"Findings": [...]}, as saved by
                       aws securityhub get-findings), a JSON list of objects, or CSV with id, title and a rule,
                       control or check column

Reports, each row citing its register id or finding key:
  no owner             open risks whose owner is blank or a placeholder (tbc, tbd, unassigned, none, n/a, -)
  overdue review       open risks whose review_date (YYYY-MM-DD) is before --as-of; missing or unreadable dates
                       are listed separately, never guessed
  new findings         open findings whose id, control id, rule or title is not in any risk's finding_refs,
                       grouped by control or rule
  no supporting        open risks with no open finding matching their finding_refs (or no refs recorded)
  likelihood/impact    values that changed between --previous and the register, shown as written
  added/removed        risk ids present in only one snapshot
Then a refresh agenda. Likelihood and impact are the register's own values; the script never scores or ranks
risk. Closed risks are status closed or retired; closed findings are resolved, suppressed, archived or passed.

Exit codes: 0 nothing flagged, 1 at least one row needs review, 2 bad input.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from datetime import date
from pathlib import Path

REQUIRED = ("id", "title", "owner", "likelihood", "impact", "treatment", "review_date", "status")
ALIASES = {
    "risk_id": "id",
    "next_review": "review_date",
    "review": "review_date",
    "findings": "finding_refs",
    "finding_ids": "finding_refs",
    "controls": "finding_refs",
    "sources": "finding_refs",
}
NO_OWNER = {"", "tbc", "tbd", "unassigned", "none", "n/a", "na", "-", "unknown"}
CLOSED_RISK = {"closed", "retired"}
CLOSED_FINDING = {"resolved", "suppressed", "archived", "passed", "pass", "closed", "fixed"}
KEY_FIELDS = ("rule", "rule_id", "check_id", "check", "control", "control_id", "security_control_id", "generator_id")


class InputError(Exception):
    """Bad input: exit code 2."""


def norm_header(name: str) -> str:
    key = re.sub(r"[\s\-]+", "_", (name or "").strip().lower())
    return ALIASES.get(key, key)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise InputError(f"file not found: {path}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(f"cannot read {path}: {exc}") from exc


def read_csv(path: Path, warnings: list[str]) -> tuple[list[str], list[dict]]:
    text = read_text(path)
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return [], []
    columns = [norm_header(h) for h in header]
    rows = []
    for record in reader:
        line = reader.line_num
        if not any(cell.strip() for cell in record):
            continue
        if len(record) != len(columns):
            warnings.append(f"{path.name}:{line}: {len(record)} fields, expected {len(columns)}; row skipped")
            continue
        row = {col: cell.strip() for col, cell in zip(columns, record, strict=True)}
        row["_line"] = line
        rows.append(row)
    return columns, rows


def read_register(path: Path, warnings: list[str], links: bool = True) -> dict[str, dict]:
    columns, rows = read_csv(path, warnings)
    if not columns:
        raise InputError(f"{path}: empty file, expected a header row with {', '.join(REQUIRED)}")
    missing = [c for c in REQUIRED if c not in columns]
    if missing:
        raise InputError(f"{path}: missing column(s) {', '.join(missing)}")
    register: dict[str, dict] = {}
    for row in rows:
        if not row["id"]:
            warnings.append(f"{path.name}:{row['_line']}: row has no id; skipped")
            continue
        if row["id"] in register:
            warnings.append(f"{path.name}:{row['_line']}: duplicate id {row['id']}; the first row is used")
            continue
        refs = re.split(r"[;,|]", row.get("finding_refs", ""))
        row["refs"] = sorted({r.strip() for r in refs if r.strip()}, key=str.lower)
        row["open"] = row["status"].lower() not in CLOSED_RISK
        register[row["id"]] = row
    if links and "finding_refs" not in columns:
        warnings.append(
            f"{path.name}: no finding_refs column, so no finding can be matched to a risk; add one to link them"
        )
    return register


def dig(obj, *path):
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def finding_from(obj: dict, source: str, index) -> dict | None:
    lower = {norm_header(k): v for k, v in obj.items()}
    asff = "SchemaVersion" in obj or "ProductArn" in obj or "GeneratorId" in obj
    fid = obj.get("Id") or lower.get("id") or lower.get("finding_id") or lower.get("findingid")
    title = obj.get("Title") or lower.get("title") or lower.get("name") or ""
    control = (
        dig(obj, "Compliance", "SecurityControlId")
        or dig(obj, "ProductFields", "ControlId")
        or dig(obj, "ProductFields", "RuleId")
        or next((lower[k] for k in KEY_FIELDS if lower.get(k)), None)
    )
    generator = obj.get("GeneratorId")
    group = control or generator or title or fid
    if not group:
        return None
    if asff:
        closed = (
            str(obj.get("RecordState", "")).upper() == "ARCHIVED"
            or str(dig(obj, "Workflow", "Status") or "").upper() in ("RESOLVED", "SUPPRESSED")
            or str(dig(obj, "Compliance", "Status") or "").upper() == "PASSED"
        )
        severity = dig(obj, "Severity", "Label") or ""
    else:
        closed = str(lower.get("status", "")).strip().lower() in CLOSED_FINDING
        severity = lower.get("severity", "") or ""
        if isinstance(severity, dict):
            severity = severity.get("label", "") or severity.get("Label", "")
    keys = {str(v).strip().lower() for v in (fid, title, control, generator) if v}
    return {
        "id": str(fid or f"{source}#{index}"),
        "title": str(title),
        "group": str(group),
        "keys": keys,
        "severity": str(severity).upper(),
        "closed": closed,
        "source": source,
    }


def read_findings(path: Path, warnings: list[str]) -> list[dict]:
    raw: list = []
    if path.suffix.lower() == ".csv":
        _, rows = read_csv(path, warnings)
        raw = [(r.pop("_line"), r) for r in rows]
    else:
        try:
            data = json.loads(read_text(path))
        except json.JSONDecodeError as exc:
            raise InputError(f"{path}: invalid JSON ({exc.msg} at line {exc.lineno})") from exc
        if isinstance(data, dict):
            data = data.get("Findings", data.get("findings", data.get("results")))
        if not isinstance(data, list):
            raise InputError(f'{path}: expected ASFF {{"Findings": [...]}}, a JSON list, or a findings/results list')
        raw = list(enumerate(data, start=1))
    findings = []
    for index, obj in raw:
        if not isinstance(obj, dict):
            warnings.append(f"{path.name}: entry {index} is not an object; skipped")
            continue
        found = finding_from(obj, path.name, index)
        if found is None:
            warnings.append(f"{path.name}: entry {index} has no id, title, rule or control; skipped")
            continue
        findings.append(found)
    return findings


def analyse(args) -> dict:
    try:
        as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    except ValueError as exc:
        raise InputError(f"--as-of must be YYYY-MM-DD, got {args.as_of!r}") from exc
    warnings: list[str] = []
    register = read_register(args.register, warnings)
    previous = read_register(args.previous, warnings, links=False) if args.previous else None
    findings: list[dict] = []
    finding_sources = []
    for file in args.findings or []:
        rows = read_findings(Path(file), warnings)
        findings += rows
        closed = sum(1 for f in rows if f["closed"])
        finding_sources.append({"file": Path(file).name, "read": len(rows), "open": len(rows) - closed})
    open_findings = [f for f in findings if not f["closed"]]
    open_risks = [r for r in register.values() if r["open"]]

    def risk_row(r: dict, **extra) -> dict:
        return {"id": r["id"], "title": r["title"], "owner": r["owner"], "line": r["_line"], **extra}

    no_owner = [risk_row(r, status=r["status"]) for r in open_risks if r["owner"].strip().lower() in NO_OWNER]
    overdue, no_date = [], []
    for r in open_risks:
        try:
            when = date.fromisoformat(r["review_date"].replace("/", "-")) if r["review_date"] else None
        except ValueError:
            no_date.append(risk_row(r, review_date=r["review_date"], reason="unreadable (use YYYY-MM-DD)"))
            continue
        if when is None:
            no_date.append(risk_row(r, review_date="", reason="missing"))
        elif when < as_of:
            overdue.append(risk_row(r, review_date=when.isoformat(), days_overdue=(as_of - when).days))

    ref_index: dict[str, list[str]] = {}
    for r in register.values():
        for ref in r["refs"]:
            ref_index.setdefault(ref.lower(), []).append(r["id"])
    groups: dict[str, dict] = {}
    supported: set[str] = set()
    for f in open_findings:
        matched = sorted({rid for key in f["keys"] for rid in ref_index.get(key, [])})
        if matched:
            supported.update(matched)
            continue
        g = groups.setdefault(
            f["group"].lower(),
            {"key": f["group"], "title": f["title"], "count": 0, "severities": set(), "ids": [], "sources": set()},
        )
        g["count"] += 1
        if f["severity"]:
            g["severities"].add(f["severity"])
        g["ids"].append(f["id"])
        g["sources"].add(f["source"])
    new_findings = [
        {
            "key": g["key"],
            "title": g["title"],
            "count": g["count"],
            "severities": sorted(g["severities"]),
            "example_id": sorted(g["ids"])[0],
            "sources": sorted(g["sources"]),
        }
        for g in sorted(groups.values(), key=lambda g: g["key"].lower())
    ]
    unsupported = [
        risk_row(r, refs=r["refs"], reason="no finding_refs recorded" if not r["refs"] else "no open finding matches")
        for r in open_risks
        if r["id"] not in supported
    ]

    changes, added, removed = [], [], []
    if previous is not None:
        for rid, r in register.items():
            old = previous.get(rid)
            if old is None:
                added.append(risk_row(r))
                continue
            diff = {}
            for field in ("likelihood", "impact"):
                if old[field].strip().lower() != r[field].strip().lower():
                    diff[field] = {"from": old[field], "to": r[field]}
            if diff:
                changes.append(risk_row(r, likelihood=r["likelihood"], impact=r["impact"], changed=diff))
        removed = [risk_row(r) for rid, r in previous.items() if rid not in register]

    for rows in (no_owner, overdue, no_date, unsupported, changes, added, removed):
        rows.sort(key=lambda x: x["id"])
    rep = {
        "as_of": as_of.isoformat(),
        "register": {"file": args.register.name, "risks": len(register), "open": len(open_risks)},
        "previous": {"file": args.previous.name, "risks": len(previous)} if previous is not None else None,
        "findings": {
            "sources": finding_sources,
            "open": len(open_findings),
            "closed": len(findings) - len(open_findings),
        },
        "no_owner": no_owner,
        "overdue_reviews": overdue,
        "missing_review_dates": no_date,
        "new_findings": new_findings,
        "unsupported_risks": unsupported,
        "rating_changes": changes,
        "added": added,
        "removed": removed,
        "warnings": warnings,
    }
    rep["agenda"] = agenda(rep)
    rep["flagged"] = bool(no_owner or overdue or no_date or new_findings or unsupported or changes)
    return rep


def agenda(rep: dict) -> list[str]:
    items = []
    for r in rep["no_owner"]:
        items.append(f"Name an owner for {r['id']} {r['title']}.")
    for r in rep["overdue_reviews"]:
        items.append(
            f"Review {r['id']} {r['title']} (owner {r['owner']}): due {r['review_date']}, "
            f"{r['days_overdue']} days overdue."
        )
    for r in rep["missing_review_dates"]:
        items.append(
            f"Set a review date (YYYY-MM-DD) for {r['id']} {r['title']}; the register has {r['review_date'] or 'none'}."
        )
    for r in rep["rating_changes"]:
        moved = "; ".join(f"{k} {v['from']} to {v['to']}" for k, v in r["changed"].items())
        items.append(f"Confirm the change to {r['id']} {r['title']}: {moved}.")
    for f in rep["new_findings"]:
        items.append(
            f"Decide on {f['key']} ({f['count']} open finding{'s' if f['count'] != 1 else ''}, no register entry): "
            "a new risk, a link to an existing one, or not a risk."
        )
    for r in rep["unsupported_risks"]:
        items.append(f"Check {r['id']} {r['title']}: {r['reason']}; record other evidence, update refs, or close.")
    for r in rep["added"]:
        items.append(f"Note {r['id']} {r['title']} as added since the previous snapshot.")
    for r in rep["removed"]:
        items.append(f"Confirm {r['id']} {r['title']} was meant to leave the register.")
    return items


def cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ") if value not in (None, "") else "-"


def table(header: list[str], rows: list[list]) -> list[str]:
    if not rows:
        return ["None.", ""]
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return out + [""]


def render_markdown(rep: dict) -> str:
    reg, prev, fnd = rep["register"], rep["previous"], rep["findings"]
    srcs = ", ".join(f"{s['file']} ({s['read']} read, {s['open']} open)" for s in fnd["sources"]) or "none"
    out = [
        f"# Risk register refresh: as of {rep['as_of']}",
        "",
        f"Register: {reg['file']} ({reg['risks']} risks, {reg['open']} open). "
        f"Previous snapshot: {prev['file'] + ' (' + str(prev['risks']) + ' risks)' if prev else 'none'}. "
        f"Findings: {srcs}.",
        "Likelihood and impact are the register's own values, shown as written; nothing here scores or ranks risk.",
        "",
        "## Refresh agenda",
        "",
    ]
    out += [f"{i}. {line}" for i, line in enumerate(rep["agenda"], start=1)] or ["Nothing to review."]
    out += ["", "## Risks without an owner", ""]
    out += table(["ID", "Title", "Status"], [[r["id"], r["title"], r["status"]] for r in rep["no_owner"]])
    out += ["## Overdue reviews", ""]
    out += table(
        ["ID", "Title", "Owner", "Review date", "Days overdue"],
        [[r["id"], r["title"], r["owner"], r["review_date"], r["days_overdue"]] for r in rep["overdue_reviews"]],
    )
    out += ["## Missing or unreadable review dates", ""]
    out += table(
        ["ID", "Title", "Owner", "Review date", "Problem"],
        [[r["id"], r["title"], r["owner"], r["review_date"], r["reason"]] for r in rep["missing_review_dates"]],
    )
    out += ["## New findings with no register entry", ""]
    out += table(
        ["Finding key", "Title", "Open findings", "Severity", "Example id", "Source"],
        [
            [f["key"], f["title"], f["count"], ", ".join(f["severities"]), f["example_id"], ", ".join(f["sources"])]
            for f in rep["new_findings"]
        ],
    )
    out += ["## Register risks with no supporting finding", ""]
    out += table(
        ["ID", "Title", "Owner", "Finding refs", "Reason"],
        [[r["id"], r["title"], r["owner"], "; ".join(r["refs"]), r["reason"]] for r in rep["unsupported_risks"]],
    )
    out += ["## Likelihood or impact changes since the previous snapshot", ""]
    if prev is None:
        out += ["No previous snapshot given (--previous).", ""]
    else:
        rows = []
        for r in rep["rating_changes"]:
            lik = r["changed"].get("likelihood")
            imp = r["changed"].get("impact")
            rows.append(
                [
                    r["id"],
                    r["title"],
                    f"{lik['from']} to {lik['to']}" if lik else f"{r['likelihood']} (no change)",
                    f"{imp['from']} to {imp['to']}" if imp else f"{r['impact']} (no change)",
                ]
            )
        out += table(["ID", "Title", "Likelihood", "Impact"], rows)
        out += ["## Risks added or removed since the previous snapshot", ""]
        out += table(
            ["ID", "Title", "Change"],
            [[r["id"], r["title"], "added"] for r in rep["added"]]
            + [[r["id"], r["title"], "removed"] for r in rep["removed"]],
        )
    if rep["warnings"]:
        out += ["## Warnings", "", *[f"- {w}" for w in rep["warnings"]], ""]
    return "\n".join(out).rstrip("\n") + "\n"


def write_output(text: str, output: Path | None) -> None:
    if output is None:
        sys.stdout.write(text)
        return
    if not output.parent.is_dir():
        raise InputError(f"output folder does not exist: {output.parent}")
    output.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="risk_register_refresh.py",
        description="Quarter-on-quarter refresh of a CSV risk register against saved findings exports: new "
        "findings with no entry, risks with no finding, likelihood or impact changes, overdue reviews and risks "
        "without an owner (offline; no scoring).",
        epilog="Exit codes: 0 nothing flagged, 1 at least one row needs review, 2 bad input.",
    )
    parser.add_argument("register", type=Path, help="current register CSV")
    parser.add_argument("--previous", type=Path, help="previous register snapshot CSV")
    parser.add_argument("--findings", action="append", metavar="FILE", help="findings export (ASFF JSON, JSON or CSV)")
    parser.add_argument("--as-of", help="date for overdue reviews, YYYY-MM-DD (default today)")
    parser.add_argument("--json", action="store_true", help="JSON instead of Markdown")
    parser.add_argument("--output", type=Path, help="write the refresh to this file instead of stdout")
    args = parser.parse_args(argv)
    try:
        rep = analyse(args)
        write_output(json.dumps(rep, indent=2) + "\n" if args.json else render_markdown(rep), args.output)
    except InputError as exc:
        print(f"risk_register_refresh.py: {exc}", file=sys.stderr)
        return 2
    return 1 if rep["flagged"] else 0


if __name__ == "__main__":
    sys.exit(main())
