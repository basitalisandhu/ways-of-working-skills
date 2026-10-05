#!/usr/bin/env python3
"""meeting_ledger.py: one action ledger across a folder of meeting notes.

Input: a folder of meeting notes, *.md and *.txt at any depth (README.md, ledger.md and files starting with "_" are
skipped).
Each file is one meeting. Its date comes from a YYYY-MM-DD at the start of the file name, else from a line
"Date: YYYY-MM-DD"; a file with neither is "undated" and sorts after dated files by name.

Accepted action lines (leading spaces allowed; "-", "*" or "+" bullets):
  - [ ] owner: action text (due 2026-10-12)     open checkbox
  - [x] owner: action text                       done checkbox ([X] also counts)
  Action: owner: action text (due 2026-10-12)    "Action:" or "Actions:" prefix, any case, bullet optional
  Action: action text                            no owner stated
  - [ ] @owner action text                       owner as an @handle instead of "owner:"
The owner is the text before the first ": " when it is one to three words and at most 40 characters, or a leading
@handle. The due date is "(due YYYY-MM-DD)", "due YYYY-MM-DD" or "by YYYY-MM-DD" anywhere in the line. "(done)" in an
"Action:" line marks it done.

Rules:
  * one item per normalised text (lower case, owner and due date removed, punctuation and spaces collapsed); the
    same text in several meetings is one item carried over, and its latest appearance decides status, owner and due;
  * overdue: open and due before --as-of; ownerless: open and no owner in any appearance; carried: open and seen open
    in --carry or more meetings (default 3);
  * a line that starts like an action but cannot be read (empty text, impossible date) is listed as "not understood"
    with its file and line, and the run continues.

Output: Markdown on standard output, or with --out DIR the two files DIR/ledger.md and DIR/ledger.json (nothing else
is written). --json prints the JSON to standard output. Owners are listed by name only, never ranked or scored.
The output is deterministic for the same files and --as-of.

Exit codes: 0 nothing flagged, 1 at least one overdue, ownerless or carried item or a line not understood, 2 bad
input (folder missing, no notes files, bad --as-of).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

DATE_RE = r"\d{4}-\d{2}-\d{2}"
CHECKBOX_RE = re.compile(r"^\s*[-*+]\s+\[(?P<mark>[ xX])\]\s*(?P<text>.*)$")
ACTION_RE = re.compile(r"^\s*(?:[-*+]\s+)?(?:\*\*)?actions?(?:\*\*)?\s*:(?:\*\*)?\s*(?P<text>.*)$", re.IGNORECASE)
DUE_RE = re.compile(r"\(?\b(?:due|by)\s*:?\s*(?P<date>\d{4}-\d{1,2}-\d{1,2})\)?", re.IGNORECASE)
OWNER_COLON_RE = re.compile(r"^(?P<owner>[^:()\[\]]{1,40}?):\s+(?P<rest>.+)$")
OWNER_AT_RE = re.compile(r"^@(?P<owner>[A-Za-z0-9][\w.-]*)\s+(?P<rest>.+)$")
FILE_DATE_RE = re.compile(rf"^(?P<date>{DATE_RE})")
DATE_LINE_RE = re.compile(rf"^\s*(?:\*\*)?date(?:\*\*)?\s*:(?:\*\*)?\s*(?P<date>{DATE_RE})", re.IGNORECASE)
DONE_RE = re.compile(r"\(done\)", re.IGNORECASE)


class InputError(Exception):
    """Bad input: the message is printed and the script exits 2."""


def parse_date(text: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def normalise(text: str) -> str:
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def meeting_date(path: Path, lines: list[str]) -> dt.date | None:
    m = FILE_DATE_RE.match(path.name)
    if m and (d := parse_date(m.group("date"))):
        return d
    for line in lines[:30]:
        m = DATE_LINE_RE.match(line)
        if m and (d := parse_date(m.group("date"))):
            return d
    return None


def parse_action(raw: str) -> tuple[dict | None, str | None]:
    """Return (action, None), (None, reason) for a line that looks like an action but is not readable, or
    (None, None) for a line that is not an action at all."""
    m = CHECKBOX_RE.match(raw)
    if m:
        done, text = m.group("mark") in "xX", m.group("text").strip()
    else:
        m = ACTION_RE.match(raw)
        if not m:
            return None, None
        text = m.group("text").strip()
        done = bool(DONE_RE.search(text))
        text = DONE_RE.sub("", text).strip()
    due = None
    dm = DUE_RE.search(text)
    if dm:
        due = parse_date(dm.group("date"))
        if due is None:
            return None, f"due date {dm.group('date')!r} is not a valid YYYY-MM-DD date"
        text = (text[: dm.start()] + text[dm.end() :]).strip()
    owner = None
    om = OWNER_AT_RE.match(text) or OWNER_COLON_RE.match(text)
    if om and len(om.group("owner").split()) <= 3:
        owner, text = om.group("owner").strip(), om.group("rest").strip()
    text = text.strip(" -.;,")
    if not normalise(text):
        return None, "action has no text"
    return {"text": text, "owner": owner, "due": due, "done": done}, None


def collect(folder: Path) -> tuple[list[dict], list[dict], list[dict]]:
    if not folder.is_dir():
        raise InputError(f"{folder}: not a folder")
    files = sorted(
        p
        for p in folder.rglob("*")
        if p.is_file()
        and p.suffix.lower() in (".md", ".txt")
        and p.name.lower() not in ("readme.md", "ledger.md")
        and not p.name.startswith("_")
    )
    if not files:
        raise InputError(f"{folder}: no meeting notes (*.md or *.txt) found")
    meetings, appearances, bad = [], [], []
    for path in files:
        rel = path.relative_to(folder).as_posix()
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        date = meeting_date(path, lines)
        meetings.append({"file": rel, "date": date})
        for n, raw in enumerate(lines, start=1):
            action, problem = parse_action(raw)
            if problem:
                bad.append({"file": rel, "line": n, "text": raw.strip(), "problem": problem})
            elif action:
                appearances.append(action | {"file": rel, "line": n, "date": date})
    order = {
        m["file"]: i
        for i, m in enumerate(sorted(meetings, key=lambda m: (m["date"] is None, m["date"] or dt.date.min, m["file"])))
    }
    for a in appearances:
        a["order"] = order[a["file"]]
    appearances.sort(key=lambda a: (a["order"], a["line"]))
    meetings.sort(key=lambda m: order[m["file"]])
    return meetings, appearances, bad


def build(appearances: list[dict], as_of: dt.date, carry: int) -> list[dict]:
    items: dict[str, dict] = {}
    for a in appearances:
        key = normalise(a["text"])
        item = items.setdefault(key, {"text": a["text"], "owner": None, "due": None, "seen": [], "open_in": set()})
        item["seen"].append(a)
        item["text"] = a["text"]
        if a["owner"]:
            item["owner"] = a["owner"]
        if a["due"]:
            item["due"] = a["due"]
        if not a["done"]:
            item["open_in"].add(a["file"])
    ledger = []
    for i, item in enumerate(items.values(), start=1):
        last = item["seen"][-1]
        status = "done" if last["done"] else "open"
        flags = []
        if status == "open":
            if item["due"] and item["due"] < as_of:
                flags.append("overdue")
            if not item["owner"]:
                flags.append("ownerless")
            if len(item["open_in"]) >= carry:
                flags.append("carried")
        ledger.append(
            {
                "id": f"A{i:03d}",
                "text": item["text"],
                "owner": item["owner"],
                "due": item["due"].isoformat() if item["due"] else None,
                "status": status,
                "first_seen": item["seen"][0]["file"],
                "last_seen": last["file"],
                "meetings_open": len(item["open_in"]),
                "sources": [f"{s['file']}:{s['line']}" for s in item["seen"]],
                "flags": flags,
            }
        )
    return ledger


def render(ledger: list[dict], meetings: list[dict], bad: list[dict], as_of: dt.date, carry: int) -> str:
    out = ["# Action ledger", ""]
    open_items = [x for x in ledger if x["status"] == "open"]
    out.append(
        f"As of {as_of.isoformat()}. {len(meetings)} meeting file(s), {len(ledger)} action(s): "
        f"{len(open_items)} open, {len(ledger) - len(open_items)} done."
    )
    out.append("")
    for flag, heading in (
        ("overdue", "Overdue"),
        ("ownerless", "No owner stated"),
        ("carried", f"Carried over in {carry} or more meetings"),
    ):
        rows = [x for x in ledger if flag in x["flags"]]
        out.append(f"## {heading} ({len(rows)})")
        out.append("")
        if not rows:
            out.append("None.")
        for x in rows:
            extra = f", due {x['due']}" if x["due"] else ""
            extra += f", open in {x['meetings_open']} meetings" if flag == "carried" else ""
            out.append(f"- {x['id']} {x['text']} ({x['owner'] or 'no owner stated'}{extra}; {x['sources'][-1]})")
        out.append("")
    out.append("## Open by owner")
    out.append("")
    owners = sorted({x["owner"] or "" for x in open_items}, key=lambda o: (o == "", o.lower()))
    if not owners:
        out.append("No open actions.")
    for owner in owners:
        out.append(f"### {owner or 'No owner stated'}")
        out.append("")
        for x in open_items:
            if (x["owner"] or "") == owner:
                out.append(f"- [ ] {x['id']} {x['text']}" + (f" (due {x['due']})" if x["due"] else ""))
        out.append("")
    out.append("## All actions")
    out.append("")
    if ledger:
        out.append("| ID | Action | Owner | Due | Status | Meetings open | Flags | Sources |")
        out.append("|---|---|---|---|---|---|---|---|")
        for x in ledger:
            out.append(
                f"| {x['id']} | {x['text'].replace('|', '/')} | {x['owner'] or ''} | {x['due'] or ''} | "
                f"{x['status']} | {x['meetings_open']} | {', '.join(x['flags'])} | {'; '.join(x['sources'])} |"
            )
    else:
        out.append("No action lines found. See the accepted formats in the skill.")
    out.append("")
    out.append(f"## Lines not understood ({len(bad)})")
    out.append("")
    if not bad:
        out.append("None.")
    for b in bad:
        out.append(f"- {b['file']}:{b['line']}: {b['problem']}: `{b['text']}`")
    out.append("")
    undated = [m["file"] for m in meetings if m["date"] is None]
    if undated:
        out.append("Undated files (sorted last, by name): " + ", ".join(undated))
        out.append("")
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="meeting_ledger.py",
        description="Build one action ledger across a folder of meeting notes and flag overdue, ownerless and "
        "repeatedly carried items.",
        epilog="Exit codes: 0 nothing flagged, 1 something flagged or a line not understood, 2 bad input.",
    )
    p.add_argument("folder", help="folder of meeting notes (*.md, *.txt)")
    p.add_argument("--as-of", default=None, help="date to judge overdue items against, YYYY-MM-DD (default: today)")
    p.add_argument("--carry", type=int, default=3, help="flag open items seen in this many meetings (default 3)")
    p.add_argument("--out", default=None, help="write ledger.md and ledger.json into this folder (created if missing)")
    p.add_argument("--json", action="store_true", help="print JSON to standard output instead of Markdown")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        as_of = dt.date.today() if args.as_of is None else parse_date(args.as_of)
        if as_of is None:
            raise InputError(f"--as-of {args.as_of!r} is not a YYYY-MM-DD date")
        if args.carry < 2:
            raise InputError("--carry must be 2 or more")
        meetings, appearances, bad = collect(Path(args.folder))
    except InputError as exc:
        print(f"meeting_ledger.py: {exc}", file=sys.stderr)
        return 2
    ledger = build(appearances, as_of, args.carry)
    data = {
        "as_of": as_of.isoformat(),
        "carry_threshold": args.carry,
        "meetings": [{"file": m["file"], "date": m["date"].isoformat() if m["date"] else None} for m in meetings],
        "actions": ledger,
        "not_understood": bad,
    }
    js = json.dumps(data, indent=2, sort_keys=True) + "\n"
    md = render(ledger, meetings, bad, as_of, args.carry)
    for b in bad:
        print(f"meeting_ledger.py: {b['file']}:{b['line']}: {b['problem']}", file=sys.stderr)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "ledger.md").write_text(md, encoding="utf-8")
        (out / "ledger.json").write_text(js, encoding="utf-8")
        print(f"wrote {out / 'ledger.md'} and {out / 'ledger.json'}")
    else:
        sys.stdout.write(js if args.json else md)
    flagged = any(x["flags"] for x in ledger) or bool(bad)
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
