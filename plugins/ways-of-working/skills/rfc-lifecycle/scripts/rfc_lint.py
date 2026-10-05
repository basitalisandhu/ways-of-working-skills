#!/usr/bin/env python3
"""rfc_lint.py: lint a folder of design proposals (RFCs) for lifecycle problems.

Input: a folder of RFCs, *.md at any depth (README.md, INDEX.md and files starting with "_", such as a template,
are skipped). Each RFC starts with a front matter block:

    ---
    title: Move build runners to a private subnet
    status: review            # draft, review, accepted, rejected or superseded
    authors: platform team
    created: 2026-09-01
    review-started: 2026-09-10   # optional; created is used when it is missing
    decision-date: 2026-09-30    # required once accepted, rejected or superseded
    superseded-by: 0007-runner-pools.md   # required when superseded
    ---

Front matter is read as simple "key: value" lines (no nesting); "#" starts a comment only after a space.

Rules:
  * missing-status / invalid-status: no status, or a status outside the five above;
  * unanswered-thread: a line starting with "Q:" (after optional "-", "*" or ">" markers) with no line starting with
    "A:" after it and before the next "Q:" line or the next heading;
  * open-questions: list items under a heading named "Open questions" that are not struck through (~~...~~) and not
    marked "(resolved)" or "None"; flagged in review and accepted, listed for information in draft;
  * missing-decision-date: status accepted, rejected or superseded without a valid decision-date;
  * missing-superseded-by: status superseded without superseded-by;
  * review-overdue: status review for more than --max-review-days (default 14) since review-started, or since created
    when review-started is missing (the basis is stated); missing-review-start when neither date is valid;
  * missing-section: status review or accepted and one of the fixed sections (--sections) is absent;
  * invalid-date: a date field that is not YYYY-MM-DD; malformed-front-matter: a "---" block that is never closed.

Output: Markdown on standard output (or --out FILE) with one summary row per RFC and the findings; --json prints the
computed data instead. Deterministic for the same files and --as-of. The script decides nothing about the design.

Exit codes: 0 no findings, 1 at least one finding, 2 bad input (folder missing, no RFC files, bad --as-of).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

STATUSES = ("draft", "review", "accepted", "rejected", "superseded")
DECIDED = ("accepted", "rejected", "superseded")
DEFAULT_SECTIONS = (
    "Summary",
    "Motivation",
    "Proposal",
    "Alternatives considered",
    "Security review",
    "Rollout and rollback",
    "Open questions",
    "Decision",
)
DATE_FIELDS = ("created", "review-started", "decision-date")
QUOTE_PREFIX = r"^\s*(?:[-*>]\s*)*"
Q_RE = re.compile(QUOTE_PREFIX + r"(?:\*\*)?Q(?:\*\*)?\s*:", re.IGNORECASE)
A_RE = re.compile(QUOTE_PREFIX + r"(?:\*\*)?A(?:\*\*)?\s*:", re.IGNORECASE)
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(?P<text>.+)$")


class InputError(Exception):
    """Bad input: the message is printed and the script exits 2."""


def front_matter(lines: list[str]) -> tuple[dict[str, str], int]:
    """Return (fields, index of the first body line). Keys are lower-cased with "_" read as "-"."""
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


def lint(path: Path, rel: str, as_of: dt.date, max_days: int, sections: tuple[str, ...]) -> dict:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    fm, start = front_matter(lines)
    findings: list[dict] = []

    def add(rule: str, line: int, detail: str) -> None:
        findings.append({"rule": rule, "file": rel, "line": line, "detail": detail})

    status = fm.get("status", "").lower()
    if lines and lines[0].strip() == "---" and start == 0:
        add("malformed-front-matter", 1, "front matter opened with --- but never closed")
    elif not fm:
        add("missing-status", 1, "no front matter block with a status")
    elif not status:
        add("missing-status", 1, "front matter has no status")
    elif status not in STATUSES:
        add("invalid-status", 1, f"status {status!r} is not one of {', '.join(STATUSES)}")
    dates: dict[str, dt.date | None] = {}
    for field in DATE_FIELDS:
        if fm.get(field):
            dates[field] = parse_date(fm[field])
            if dates[field] is None:
                add("invalid-date", 1, f"{field} {fm[field]!r} is not YYYY-MM-DD")

    headings: list[tuple[int, str]] = []
    threads: list[dict] = []
    open_q: list[dict] = []
    pending: int | None = None
    in_open = False
    for n in range(start, len(lines)):
        line = lines[n]
        hm = HEADING_RE.match(line)
        if hm:
            if pending is not None:
                threads.append({"line": pending + 1, "text": lines[pending].strip()})
                pending = None
            headings.append((n + 1, hm.group(2).strip()))
            in_open = hm.group(2).strip().lower().rstrip(":") == "open questions"
            continue
        if Q_RE.match(line):
            if pending is not None:
                threads.append({"line": pending + 1, "text": lines[pending].strip()})
            pending = n
        elif A_RE.match(line):
            pending = None
        if in_open:
            im = ITEM_RE.match(line)
            if im:
                text = im.group("text").strip()
                if not (text.startswith("~~") or "(resolved)" in text.lower() or text.lower().rstrip(".") == "none"):
                    open_q.append({"line": n + 1, "text": text})
    if pending is not None:
        threads.append({"line": pending + 1, "text": lines[pending].strip()})
    for t in threads:
        add("unanswered-thread", t["line"], t["text"])
    if status in ("review", "accepted"):
        for q in open_q:
            add("open-questions", q["line"], q["text"])
        present = {h.lower().rstrip(":") for _, h in headings}
        for s in sections:
            if s.lower() not in present:
                add("missing-section", 1, f"no '{s}' section")
    if status in DECIDED and not dates.get("decision-date"):
        add("missing-decision-date", 1, f"status {status} but no valid decision-date")
    if status == "superseded" and not fm.get("superseded-by"):
        add("missing-superseded-by", 1, "status superseded but no superseded-by")
    days_in_review = None
    basis = None
    if status == "review":
        if dates.get("review-started"):
            since, basis = dates["review-started"], "review-started"
        elif dates.get("created"):
            since, basis = dates["created"], "created (no review-started)"
        else:
            since = None
            add("missing-review-start", 1, "status review but no valid review-started or created date")
        if since is not None:
            days_in_review = (as_of - since).days
            if days_in_review > max_days:
                add(
                    "review-overdue",
                    1,
                    f"in review {days_in_review} days since {since.isoformat()} ({basis}); limit {max_days}",
                )
    return {
        "file": rel,
        "title": fm.get("title") or (headings[0][1] if headings else rel),
        "status": status or None,
        "days_in_review": days_in_review,
        "review_basis": basis,
        "decision_date": fm.get("decision-date") or None,
        "open_questions": open_q,
        "unanswered_threads": threads,
        "findings": findings,
    }


def collect(folder: Path, as_of: dt.date, max_days: int, sections: tuple[str, ...]) -> list[dict]:
    if not folder.is_dir():
        raise InputError(f"{folder}: not a folder")
    files = sorted(
        p
        for p in folder.rglob("*.md")
        if p.is_file() and p.name.lower() not in ("readme.md", "index.md") and not p.name.startswith("_")
    )
    if not files:
        raise InputError(f"{folder}: no RFC files (*.md) found")
    return [lint(p, p.relative_to(folder).as_posix(), as_of, max_days, sections) for p in files]


def render(rfcs: list[dict], as_of: dt.date, max_days: int) -> str:
    total = sum(len(r["findings"]) for r in rfcs)
    out = [
        "# RFC lifecycle lint",
        "",
        f"As of {as_of.isoformat()}. {len(rfcs)} RFC(s), {total} finding(s). Review limit {max_days} days.",
        "",
        "| RFC | Title | Status | Days in review | Open questions | Unanswered threads | Decision date |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rfcs:
        days = "" if r["days_in_review"] is None else str(r["days_in_review"])
        out.append(
            f"| `{r['file']}` | {r['title'].replace('|', '/')} | {r['status'] or 'none'} | {days} | "
            f"{len(r['open_questions'])} | {len(r['unanswered_threads'])} | {r['decision_date'] or ''} |"
        )
    out.append("")
    out.append("## Findings")
    out.append("")
    if not total:
        out.append("None.")
    for r in rfcs:
        for f in r["findings"]:
            out.append(f"- `{f['file']}:{f['line']}` {f['rule']}: {f['detail']}")
    drafts = [(r, q) for r in rfcs if r["status"] == "draft" for q in r["open_questions"]]
    if drafts:
        out.append("")
        out.append("## Open questions in drafts (for information)")
        out.append("")
        for r, q in drafts:
            out.append(f"- `{r['file']}:{q['line']}` {q['text']}")
    out.append("")
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rfc_lint.py",
        description="Lint a folder of RFCs: status header, unanswered review threads, open questions, decision date "
        "and time in review.",
        epilog="Exit codes: 0 no findings, 1 at least one finding, 2 bad input.",
    )
    p.add_argument("folder", help="folder of RFC Markdown files")
    p.add_argument("--as-of", default=None, help="date to measure time in review against, YYYY-MM-DD (default: today)")
    p.add_argument("--max-review-days", type=int, default=14, help="days in review before flagging (default 14)")
    p.add_argument(
        "--sections",
        default=",".join(DEFAULT_SECTIONS),
        help="comma-separated section headings required in review and accepted RFCs "
        "(default: the fixed list in the skill; pass an empty string to skip)",
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
        if args.max_review_days < 0:
            raise InputError("--max-review-days must be 0 or more")
        sections = tuple(s.strip() for s in args.sections.split(",") if s.strip())
        rfcs = collect(Path(args.folder), as_of, args.max_review_days, sections)
    except InputError as exc:
        print(f"rfc_lint.py: {exc}", file=sys.stderr)
        return 2
    if args.json:
        text = (
            json.dumps(
                {"as_of": as_of.isoformat(), "max_review_days": args.max_review_days, "rfcs": rfcs},
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    else:
        text = render(rfcs, as_of, args.max_review_days)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 1 if any(r["findings"] for r in rfcs) else 0


if __name__ == "__main__":
    sys.exit(main())
