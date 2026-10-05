#!/usr/bin/env python3
"""Private per-person 1:1 ledger from a local folder of notes. Reads files only; no network.

Accepted note formats (Markdown or text, *.md or *.txt, read recursively, hidden files skipped):
  A. One file per person, e.g. alex-example.md. The person is the first "# " heading, else the file name.
     Each 1:1 is a "## YYYY-MM-DD" section. An optional "cadence: N" line before the first section sets the
     expected days between 1:1s for that person.
  B. Dated files, e.g. 2026-09-12.md or 2026-09-12-one-on-ones.md (the name starts with the date). Each
     "## <name>" section is a 1:1 with that person on that date.
Inside a section:
  - topic: <text>                       a topic raised
  - [ ] me: <text> (due YYYY-MM-DD)     an open commitment of yours; "them:" for theirs; the due part is optional
  - [x] me: <text>                      a commitment done; the latest mention of the same text wins
Anything else is free text and is not read.

Per person: 1:1 dates, days since the last one against the expected cadence, earlier gaps longer than the
cadence, open commitments on each side (overdue ones marked), and topics raised on more than one date.

Privacy: the script computes no ratings, rankings, sentiment or comparisons between people; people are listed by
name only. It refuses to read notes, or write output, under a folder that looks shared: a ".git" entry in the
folder or any folder above it, or a path component containing "shared" (any case). --allow-shared overrides this.

Exit codes: 0 nothing due, 1 a 1:1 past its cadence or an overdue commitment, 2 bad input, 3 refused because the
path looks shared or inside a repository.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
HEADING2_RE = re.compile(r"^##\s+(.+?)\s*$")
HEADING1_RE = re.compile(r"^#\s+(.+?)\s*$")
CADENCE_RE = re.compile(r"^cadence:\s*(\d+)\s*(?:days?)?\s*$", re.IGNORECASE)
TOPIC_RE = re.compile(r"^[-*]\s+topic:\s*(.+?)\s*$", re.IGNORECASE)
BOX_RE = re.compile(r"^[-*]\s+\[(.?)\]\s*(.*)$")
SIDE_RE = re.compile(r"^(me|them):\s*(.+)$", re.IGNORECASE)
DUE_RE = re.compile(r"\(due\s+(\d{4}-\d{2}-\d{2})\)\s*$", re.IGNORECASE)
SIDES = {"me": "mine", "them": "theirs", "": "side not stated"}


class InputError(Exception):
    """Bad input: exit code 2."""


class Refused(Exception):
    """Path looks shared or sits inside a repository: exit code 3."""


def shared_reason(path: Path) -> str | None:
    """Why a path looks shared, or None. Heuristic documented in SKILL.md."""
    resolved = path.resolve()
    for folder in (resolved, *resolved.parents):
        if (folder / ".git").exists():
            return f"{folder / '.git'} exists, so the path is inside a repository"
    for part in resolved.parts:
        if "shared" in part.lower():
            return f'the path contains "{part}"'
    return None


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


def parse_date(text: str) -> date | None:
    m = DATE_RE.match(text)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def note_files(folder: Path) -> list[Path]:
    files = [
        p
        for p in folder.rglob("*")
        if p.is_file()
        and p.suffix.lower() in (".md", ".txt")
        and not any(part.startswith(".") for part in p.relative_to(folder).parts)
    ]
    return sorted(files, key=lambda p: p.relative_to(folder).as_posix())


def read_sections(folder: Path, warnings: list[str]) -> tuple[dict[str, dict], dict[str, int]]:
    """Return {person: {"display": name, "sessions": {date: [(line_no, file, text)]}}} and per-person cadence."""
    people: dict[str, dict] = {}
    cadence: dict[str, int] = {}
    for path in note_files(folder):
        rel = path.relative_to(folder).as_posix()
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"{rel}: cannot read ({exc}); skipped")
            continue
        file_date = parse_date(path.stem)
        if DATE_RE.match(path.stem) and file_date is None:
            warnings.append(f"{rel}: file name starts with an invalid date; skipped")
            continue
        person_name = None
        if file_date is None:
            person_name = path.stem.replace("-", " ").replace("_", " ").strip()
            for line in lines:
                h1 = HEADING1_RE.match(line)
                if h1:
                    person_name = h1.group(1)
                    break
        current: list | None = None
        for number, line in enumerate(lines, start=1):
            stripped = line.strip()
            h2 = HEADING2_RE.match(stripped)
            if h2:
                title = h2.group(1)
                if file_date is not None:
                    name, when = title, file_date
                else:
                    when = parse_date(title) if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", title) else None
                    if when is None:
                        warnings.append(
                            f"{rel}:{number}: heading {title!r} is not a date (YYYY-MM-DD); section skipped"
                        )
                        current = None
                        continue
                    name = person_name
                key = norm(name)
                if not key:
                    warnings.append(f"{rel}:{number}: empty name in heading; section skipped")
                    current = None
                    continue
                entry = people.setdefault(key, {"display": name, "sessions": {}})
                current = entry["sessions"].setdefault(when, [])
                continue
            if file_date is None and current is None:
                cm = CADENCE_RE.match(stripped)
                if cm and person_name:
                    cadence[norm(person_name)] = int(cm.group(1))
                    continue
            if current is not None and stripped:
                current.append((number, rel, stripped))
    return people, cadence


def build_ledger(person: dict, cadence_days: int, as_of: date, warnings: list[str]) -> dict:
    sessions = dict(sorted(person["sessions"].items()))
    commitments: dict[tuple[str, str], dict] = {}
    topics: dict[str, dict] = {}
    for when, lines in sessions.items():
        if when > as_of:
            warnings.append(f"{person['display']}: a 1:1 is dated {when.isoformat()}, after --as-of")
        for number, rel, text in lines:
            tm = TOPIC_RE.match(text)
            if tm:
                row = topics.setdefault(norm(tm.group(1)), {"topic": tm.group(1), "dates": []})
                if when.isoformat() not in row["dates"]:
                    row["dates"].append(when.isoformat())
                continue
            bm = BOX_RE.match(text)
            if not bm:
                continue
            mark, body = bm.group(1), bm.group(2).strip()
            if mark not in (" ", "", "x", "X"):
                warnings.append(f"{rel}:{number}: checkbox [{mark}] not understood (use [ ] or [x]); line skipped")
                continue
            sm = SIDE_RE.match(body)
            side, body = (sm.group(1).lower(), sm.group(2).strip()) if sm else ("", body)
            due = None
            dm = DUE_RE.search(body)
            if dm:
                due = parse_date(dm.group(1))
                if due is None:
                    warnings.append(f"{rel}:{number}: due date {dm.group(1)!r} is not a valid date; ignored")
                body = body[: dm.start()].strip()
            if not body:
                warnings.append(f"{rel}:{number}: empty commitment; line skipped")
                continue
            row = commitments.setdefault(
                (side, norm(body)), {"text": body, "side": SIDES[side], "raised": when.isoformat(), "due": None}
            )
            row["done"] = mark in ("x", "X")
            row["last_seen"] = when.isoformat()
            row["source"] = f"{rel}:{number}"
            if due is not None:
                row["due"] = due.isoformat()

    dates = list(sessions)
    last = dates[-1] if dates else None
    days_since = (as_of - last).days if last else None
    gaps = [
        {"from": a.isoformat(), "to": b.isoformat(), "days": (b - a).days}
        for a, b in zip(dates, dates[1:], strict=False)
        if (b - a).days > cadence_days
    ]
    open_items = []
    for row in commitments.values():
        if row["done"]:
            continue
        item = {k: row[k] for k in ("text", "side", "raised", "due", "source")}
        item["overdue_days"] = (as_of - date.fromisoformat(row["due"])).days if row["due"] else None
        if item["overdue_days"] is not None and item["overdue_days"] <= 0:
            item["overdue_days"] = None
        open_items.append(item)
    open_items.sort(key=lambda r: (r["side"], r["raised"], r["text"].lower()))
    repeated = sorted(
        (t for t in topics.values() if len(t["dates"]) > 1), key=lambda t: (t["dates"][0], t["topic"].lower())
    )
    last_topics = [t["topic"] for t in topics.values() if last and last.isoformat() in t["dates"]]
    return {
        "person": person["display"],
        "cadence_days": cadence_days,
        "sessions": [d.isoformat() for d in dates],
        "last": last.isoformat() if last else None,
        "days_since_last": days_since,
        "past_cadence": days_since is not None and days_since > cadence_days,
        "gaps_over_cadence": gaps,
        "open_commitments": open_items,
        "closed_commitments": sum(1 for r in commitments.values() if r["done"]),
        "repeated_topics": repeated,
        "last_topics": last_topics,
    }


def analyse(args) -> dict:
    folder: Path = args.notes
    if not folder.is_dir():
        raise InputError(f"notes folder not found: {folder}")
    if not args.allow_shared:
        for label, path in (("notes folder", folder), ("output", args.output)):
            if path is None:
                continue
            reason = shared_reason(path if label == "notes folder" else path.parent)
            if reason:
                raise Refused(
                    f"refusing to use the {label} {path}: {reason}. 1:1 notes stay in a private local folder; "
                    "pass --allow-shared only if you are sure no one else can read it"
                )
    if args.cadence_days < 1:
        raise InputError("--cadence-days must be at least 1")
    try:
        as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    except ValueError as exc:
        raise InputError(f"--as-of must be YYYY-MM-DD, got {args.as_of!r}") from exc
    warnings: list[str] = []
    people, cadence = read_sections(folder, warnings)
    for item in args.cadence or []:
        name, _, days = item.rpartition("=")
        if not name or not days.isdigit() or int(days) < 1:
            raise InputError(f"--cadence must be NAME=DAYS, got {item!r}")
        cadence[norm(name)] = int(days)
    keys = sorted(people, key=lambda k: (people[k]["display"].lower(), k))
    if args.person:
        keys = [k for k in keys if k == norm(args.person)]
        if not keys:
            raise InputError(f"no notes found for {args.person!r}")
    ledgers = [build_ledger(people[k], cadence.get(k, args.cadence_days), as_of, warnings) for k in keys]
    return {
        "as_of": as_of.isoformat(),
        "default_cadence_days": args.cadence_days,
        "people": ledgers,
        "warnings": warnings,
        "flagged": any(p["past_cadence"] or any(c["overdue_days"] for c in p["open_commitments"]) for p in ledgers),
    }


def commitment_line(c: dict) -> str:
    parts = [f"raised {c['raised']}"]
    if c["due"]:
        parts.append(f"due {c['due']}")
    if c["overdue_days"]:
        parts.append(f"overdue by {c['overdue_days']} days")
    return f"- [ ] {c['text']} ({', '.join(parts)}; {c['source']})"


def render_markdown(rep: dict) -> str:
    out = [
        "# 1:1 ledger (private)",
        "",
        f"As of {rep['as_of']}. Expected cadence {rep['default_cadence_days']} days unless set per person. "
        "Keep this file local. It holds no ratings, rankings, sentiment or comparisons between people.",
        "",
    ]
    if not rep["people"]:
        out.append("No 1:1 notes found.")
    due = [p for p in rep["people"] if p["past_cadence"]]
    if due:
        out += ["## Due now (sorted by name)", ""]
        for p in due:
            out.append(
                f"- {p['person']}: last 1:1 {p['last']}, {p['days_since_last']} days ago (cadence {p['cadence_days']})"
            )
        out.append("")
    for p in rep["people"]:
        out += [f"## {p['person']}", ""]
        if p["sessions"]:
            status = "past cadence" if p["past_cadence"] else "within cadence"
            out.append(f"- 1:1s on record: {len(p['sessions'])} (first {p['sessions'][0]}, last {p['last']})")
            out.append(
                f"- Days since last 1:1: {p['days_since_last']} (expected every {p['cadence_days']} days): {status}"
            )
        gaps = ", ".join(f"{g['from']} to {g['to']} ({g['days']} days)" for g in p["gaps_over_cadence"])
        out.append(f"- Earlier gaps longer than cadence: {gaps or 'none'}")
        out.append(f"- Commitments closed on record: {p['closed_commitments']}")
        for side in ("mine", "theirs", "side not stated"):
            rows = [c for c in p["open_commitments"] if c["side"] == side]
            if side == "side not stated" and not rows:
                continue
            out += ["", f"### Open commitments: {side}", ""]
            out += [commitment_line(c) for c in rows] or ["None."]
        out += ["", "### Topics raised more than once", ""]
        out += [f"- {t['topic']} ({', '.join(t['dates'])})" for t in p["repeated_topics"]] or ["None."]
        out += ["", "### Topics at the last 1:1", ""]
        out += [f"- {t}" for t in p["last_topics"]] or ["None recorded."]
        out.append("")
    if rep["warnings"]:
        out += ["## Warnings", ""] + [f"- {w}" for w in rep["warnings"]] + [""]
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
        prog="one_on_one_ledger.py",
        description="Private per-person 1:1 ledger from a local notes folder: cadence gaps, open commitments on "
        "each side and topics raised more than once. No ratings, rankings or comparisons (offline).",
        epilog="Exit codes: 0 nothing due, 1 a 1:1 past cadence or an overdue commitment, 2 bad input, "
        "3 refused (path looks shared or is inside a repository).",
    )
    parser.add_argument("notes", type=Path, help="folder of 1:1 notes (formats in SKILL.md)")
    parser.add_argument("--as-of", help="the date to measure from, YYYY-MM-DD (default today)")
    parser.add_argument("--cadence-days", type=int, default=14, help="expected days between 1:1s (default 14)")
    parser.add_argument(
        "--cadence", action="append", metavar="NAME=DAYS", help="cadence for one person; repeat for more"
    )
    parser.add_argument("--person", help="render only this person's ledger (a prep sheet)")
    parser.add_argument("--allow-shared", action="store_true", help="run even if the path looks shared or in a repo")
    parser.add_argument("--json", action="store_true", help="JSON instead of Markdown")
    parser.add_argument("--output", type=Path, help="write the ledger to this file instead of stdout")
    args = parser.parse_args(argv)
    try:
        rep = analyse(args)
        if args.allow_shared:
            reason = shared_reason(args.notes)
            if reason:
                print(
                    f"one_on_one_ledger.py: warning: {reason}; running because --allow-shared was given",
                    file=sys.stderr,
                )
        write_output(json.dumps(rep, indent=2) + "\n" if args.json else render_markdown(rep), args.output)
    except Refused as exc:
        print(f"one_on_one_ledger.py: {exc}", file=sys.stderr)
        return 3
    except InputError as exc:
        print(f"one_on_one_ledger.py: {exc}", file=sys.stderr)
        return 2
    return 1 if rep["flagged"] else 0


if __name__ == "__main__":
    sys.exit(main())
