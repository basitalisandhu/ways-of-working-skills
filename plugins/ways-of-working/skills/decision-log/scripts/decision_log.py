#!/usr/bin/env python3
"""decision_log.py: list, lint and index a folder of lightweight decision records (one file per decision).

Input: a folder of decision files, *.md at any depth (README.md, INDEX.md and files starting with "_" are skipped).
Each file starts with a front matter block of simple "key: value" lines:

    ---
    id: D-0012                 # optional; the file name without .md is used when missing
    title: Keep build logs for 30 days
    status: active             # proposed, active, superseded or retired
    decided-by: platform leads
    date: 2026-09-14
    review-date: 2027-03-14
    supersedes: D-0004         # optional; one id, or several separated by commas
    superseded-by:             # optional; required when status is superseded
    ---

and has a "## Context" section (two sentences) and a "## Decision" section.

Rules (each finding cites the file):
  * missing-field: title, status, decided-by, date or review-date is empty or absent;
  * invalid-status, invalid-date, duplicate-id, malformed-front-matter (opened with --- and never closed);
  * review-overdue: status active or proposed and review-date before --as-of;
  * broken-link: supersedes or superseded-by names an id that no file has;
  * one-way-link: A supersedes B but B does not name A in superseded-by, or the reverse;
  * superseded-without-link: status superseded and no superseded-by;
  * supersede-cycle: following superseded-by from a decision comes back to it;
  * missing-section: no "Context" or no "Decision" heading.
A link may name an id or a file name with or without .md.

Output: Markdown on standard output (or --out FILE): the index table, sorted by id, then the findings. --index FILE
writes only the index table to FILE (for example decisions/INDEX.md). --json prints the computed data instead.
Deterministic for the same files and --as-of.

Exit codes: 0 no findings, 1 at least one finding, 2 bad input (folder missing, no decision files, bad --as-of).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

STATUSES = ("proposed", "active", "superseded", "retired")
REQUIRED = ("title", "status", "decided-by", "date", "review-date")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")


class InputError(Exception):
    """Bad input: the message is printed and the script exits 2."""


def front_matter(lines: list[str]) -> tuple[dict[str, str], int]:
    """Return (fields, index of the first body line); start is 0 when there is no closed block."""
    if not lines or lines[0].strip() != "---":
        return {}, 0
    fields: dict[str, str] = {}
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return fields, i + 1
        m = re.match(r"^([A-Za-z][\w-]*)\s*:\s*(.*)$", lines[i])
        if m:
            value = re.split(r"\s+#", m.group(2), maxsplit=1)[0].strip().strip("\"'")
            fields[m.group(1).lower().replace("_", "-")] = value
    return {}, 0


def parse_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def ids(value: str) -> list[str]:
    out = []
    for part in re.split(r"[,\s]+", value or ""):
        part = part.strip().strip("[]")
        if part.lower().endswith(".md"):
            part = part[:-3]
        if part:
            out.append(Path(part).name)
    return out


def read(folder: Path) -> list[dict]:
    if not folder.is_dir():
        raise InputError(f"{folder}: not a folder")
    files = sorted(
        p
        for p in folder.rglob("*.md")
        if p.is_file() and p.name.lower() not in ("readme.md", "index.md") and not p.name.startswith("_")
    )
    if not files:
        raise InputError(f"{folder}: no decision files (*.md) found")
    records = []
    for p in files:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        fm, start = front_matter(lines)
        headings = {m.group(1).strip().lower().rstrip(":") for line in lines[start:] if (m := HEADING_RE.match(line))}
        records.append(
            {
                "file": p.relative_to(folder).as_posix(),
                "stem": p.stem,
                "id": fm.get("id") or p.stem,
                "fm": fm,
                "unclosed": bool(lines) and lines[0].strip() == "---" and start == 0,
                "headings": headings,
            }
        )
    return records


def analyse(records: list[dict], as_of: dt.date) -> dict:
    findings: list[dict] = []

    def add(rule: str, rec: dict, detail: str) -> None:
        findings.append({"rule": rule, "id": rec["id"], "file": rec["file"], "detail": detail})

    by_key: dict[str, dict] = {}
    for r in records:
        if r["id"] in by_key:
            add("duplicate-id", r, f"id {r['id']} is also used by {by_key[r['id']]['file']}")
        else:
            by_key[r["id"]] = r
    for r in records:
        by_key.setdefault(r["stem"], r)

    rows = []
    for r in records:
        fm = r["fm"]
        if r["unclosed"]:
            add("malformed-front-matter", r, "front matter opened with --- but never closed")
        for field in REQUIRED:
            if not fm.get(field):
                add("missing-field", r, f"{field} is empty or absent")
        status = fm.get("status", "").lower()
        if status and status not in STATUSES:
            add("invalid-status", r, f"status {status!r} is not one of {', '.join(STATUSES)}")
        dates = {}
        for field in ("date", "review-date"):
            if fm.get(field):
                dates[field] = parse_date(fm[field])
                if dates[field] is None:
                    add("invalid-date", r, f"{field} {fm[field]!r} is not YYYY-MM-DD")
        review = dates.get("review-date")
        if status in ("active", "proposed") and review and review < as_of:
            add(
                "review-overdue",
                r,
                f"review-date {review.isoformat()} is {(as_of - review).days} days before {as_of.isoformat()}",
            )
        for heading in ("context", "decision"):
            if heading not in r["headings"]:
                add("missing-section", r, f"no '{heading.capitalize()}' section")
        sup, sup_by = ids(fm.get("supersedes", "")), ids(fm.get("superseded-by", ""))
        r["supersedes"], r["superseded_by"] = sup, sup_by
        for target in sup + sup_by:
            if target not in by_key:
                add("broken-link", r, f"links to {target!r}, which no decision file has")
        if status == "superseded" and not sup_by:
            add("superseded-without-link", r, "status superseded but superseded-by is empty")
        rows.append(
            {
                "id": r["id"],
                "title": fm.get("title", ""),
                "status": status or None,
                "date": fm.get("date") or None,
                "review_date": fm.get("review-date") or None,
                "decided_by": fm.get("decided-by") or None,
                "supersedes": sup,
                "superseded_by": sup_by,
                "file": r["file"],
            }
        )

    def resolve(x: str) -> dict | None:
        return by_key.get(x)

    for r in records:
        for target in r["supersedes"]:
            t = resolve(target)
            if t and r["id"] not in t["superseded_by"] and r["stem"] not in t["superseded_by"]:
                add("one-way-link", r, f"supersedes {t['id']} but {t['id']} does not name it in superseded-by")
        for target in r["superseded_by"]:
            t = resolve(target)
            if t and r["id"] not in t["supersedes"] and r["stem"] not in t["supersedes"]:
                add("one-way-link", r, f"superseded-by {t['id']} but {t['id']} does not name it in supersedes")
        seen, cur = [r["id"]], r
        while cur["superseded_by"]:
            nxt = resolve(cur["superseded_by"][0])
            if nxt is None:
                break
            if nxt["id"] == r["id"]:
                add("supersede-cycle", r, "superseded-by chain returns to it: " + " -> ".join(seen + [r["id"]]))
                break
            if nxt["id"] in seen:
                break
            seen.append(nxt["id"])
            cur = nxt

    rows.sort(key=lambda x: (x["id"], x["file"]))
    findings.sort(key=lambda f: (f["id"], f["file"], f["rule"], f["detail"]))
    return {"as_of": as_of.isoformat(), "decisions": rows, "findings": findings}


def index_md(rep: dict) -> str:
    out = [
        "# Decision log",
        "",
        "| ID | Title | Status | Date | Review date | Decided by | Supersedes | Superseded by |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for d in rep["decisions"]:
        title = d["title"].replace("|", "/") or "(no title)"
        out.append(
            f"| [{d['id']}]({d['file']}) | {title} | {d['status'] or ''} | {d['date'] or ''} | "
            f"{d['review_date'] or ''} | {(d['decided_by'] or '').replace('|', '/')} | "
            f"{', '.join(d['supersedes'])} | {', '.join(d['superseded_by'])} |"
        )
    out.append("")
    return "\n".join(out)


def render(rep: dict) -> str:
    out = [
        index_md(rep).rstrip("\n"),
        "",
        f"As of {rep['as_of']}: {len(rep['decisions'])} decision(s), {len(rep['findings'])} finding(s).",
        "",
        "## Findings",
        "",
    ]
    if not rep["findings"]:
        out.append("None.")
    for f in rep["findings"]:
        out.append(f"- {f['id']} (`{f['file']}`) {f['rule']}: {f['detail']}")
    out.append("")
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="decision_log.py",
        description="List, lint and index a folder of one-file-per-decision records.",
        epilog="Exit codes: 0 no findings, 1 at least one finding, 2 bad input.",
    )
    p.add_argument("folder", help="folder of decision Markdown files")
    p.add_argument("--as-of", default=None, help="date to judge review dates against, YYYY-MM-DD (default: today)")
    p.add_argument("--index", default=None, help="also write the index table to this file (for example INDEX.md)")
    p.add_argument("--json", action="store_true", help="print the computed data as JSON instead of Markdown")
    p.add_argument("--out", default=None, help="write the report to this file instead of standard output")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        as_of = dt.date.today() if args.as_of is None else parse_date(args.as_of)
        if as_of is None:
            raise InputError(f"--as-of {args.as_of!r} is not a YYYY-MM-DD date")
        rep = analyse(read(Path(args.folder)), as_of)
    except InputError as exc:
        print(f"decision_log.py: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(rep, indent=2, sort_keys=True) + "\n" if args.json else render(rep)
    if args.index:
        Path(args.index).write_text(index_md(rep), encoding="utf-8")
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 1 if rep["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
