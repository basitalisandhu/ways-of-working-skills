#!/usr/bin/env python3
"""shift_handover.py: a forward-looking on-call or shift handover note from incident and alert exports.

Inputs:
  * incidents: a JSON file (a list of objects, or an object with an "incidents", "items" or "data" list) or a CSV.
    Field names are matched ignoring case, spaces, hyphens and underscores.
      Required: id, title, status, opened
      Optional: last_update (also updated, updated_at, last_updated), owner (also assignee)
    Status resolved, closed, done, cancelled or canceled means closed; anything else is open.
  * --alerts FILE (optional): JSON or CSV with name, count, first_seen, last_seen and, optionally, silenced_until
    (also silence_expires, muted_until) for an alert that is silenced.
  * --notes FILE (optional): free text from the outgoing shift, copied into the note as a quoted block.
Times are ISO 8601 (2026-10-05T08:30:00Z, an offset, or a date alone for midnight); a time without an offset is read
as UTC.

Rules (each finding cites the record id or alert name and its line or position in the file):
  * open incidents are grouped by age since opened: under 4 hours, 4 to 24 hours, 1 to 7 days, over 7 days;
  * no-update: an open incident whose last update (or, without one, its opened time) is more than --stale-hours
    (default 4) before --as-of;
  * no-owner: an open incident with no owner;
  * noisy-alert: an alert whose count is --noisy-count (default 10) or more;
  * silence-expires: a silence that ends within --horizon-hours (default 12, the next shift) after --as-of;
  * silence-expired: a silence that ended before --as-of;
  * data problems: missing-field (a required field empty), invalid-time, invalid-count, duplicate-id, malformed-row
    (wrong number of CSV cells). A record with a data problem is still listed where it can be.
Nothing is decided and nobody is rated: the note says what is open, what to watch and what was silenced, and leaves
the incoming shift's acknowledgement blank.

Output: a Markdown handover note on standard output (or --out FILE); --json prints the computed data instead. The
output is deterministic for the same files and --as-of.

Exit codes: 0 nothing flagged, 1 something flagged (no-update, no-owner, noisy alerts, silences, data problems),
2 bad input (incidents file missing, empty or unreadable, required columns missing, bad --as-of or option).
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

CLOSED = {"resolved", "closed", "done", "cancelled", "canceled"}
ALIASES = {
    "last_update": ("last_update", "updated", "updated_at", "last_updated"),
    "owner": ("owner", "assignee"),
    "silenced_until": ("silenced_until", "silence_expires", "muted_until"),
    "first_seen": ("first_seen",),
    "last_seen": ("last_seen",),
}
INCIDENT_REQUIRED = ("id", "title", "status", "opened")
ALERT_REQUIRED = ("name", "count")
AGE_BUCKETS = (
    ("under-4h", "Opened under 4 hours ago", 4),
    ("4h-24h", "Opened 4 to 24 hours ago", 24),
    ("1d-7d", "Opened 1 to 7 days ago", 24 * 7),
    ("over-7d", "Opened over 7 days ago", None),
)
DATA_RULES = ("missing-field", "invalid-time", "invalid-count", "duplicate-id", "malformed-row")


class InputError(Exception):
    """Bad input: the message is printed and the script exits 2."""


def key(name: str) -> str:
    return re.sub(r"[\s_-]+", "_", str(name).strip().lower())


def parse_time(value: str) -> dt.datetime | None:
    value = value.strip()
    if not value:
        return None
    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            parsed = dt.datetime.combine(dt.date.fromisoformat(value), dt.time())
        else:
            parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%MZ")


def hours(delta: dt.timedelta) -> float:
    return round(delta.total_seconds() / 3600, 1)


def read_records(path: Path, label: str, list_keys: tuple[str, ...]) -> tuple[list, list[dict], list[str]]:
    """Return ([(where, {normalised key: text})], [malformed-row problems], CSV header or [])."""
    if not path.is_file():
        raise InputError(f"{label} {path}: file not found")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if not text.strip():
        raise InputError(f"{label} {path}: file is empty")
    if path.suffix.lower() == ".json" or text.lstrip()[:1] in "[{":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InputError(f"{label} {path}: not valid JSON ({exc.msg} at line {exc.lineno})") from exc
        if isinstance(data, dict):
            data = next((data[k] for k in list_keys if isinstance(data.get(k), list)), None)
        if not isinstance(data, list):
            raise InputError(f"{label} {path}: expected a JSON list of records")
        records = []
        for i, item in enumerate(data, start=1):
            if isinstance(item, dict):
                records.append((f"item {i}", {key(k): "" if v is None else str(v).strip() for k, v in item.items()}))
        return records, [], []
    reader = csv.reader(io.StringIO(text))
    rows = [(reader.line_num, r) for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise InputError(f"{label} {path}: no rows")
    header = [key(c) for c in rows[0][1]]
    records, problems = [], []
    for n, cells in rows[1:]:
        if len(cells) != len(header):
            detail = f"expected {len(header)} cells, found {len(cells)}"
            problems.append({"rule": "malformed-row", "ref": "", "where": f"line {n}", "detail": detail})
            continue
        records.append((f"line {n}", {h: c.strip() for h, c in zip(header, cells, strict=True)}))
    return records, problems, header


def field(rec: dict, name: str) -> str:
    for alias in ALIASES.get(name, (name,)):
        if rec.get(alias):
            return rec[alias]
    return ""


def check_columns(records: list[tuple[str, dict]], required: tuple[str, ...], label: str, header: list[str]) -> None:
    present = set(header)
    for _, rec in records:
        present |= set(rec)
    missing = [c for c in required if c not in present]
    if (records or header) and missing:
        raise InputError(f"{label}: missing required field(s): " + ", ".join(missing))


def analyse(
    incidents: list[tuple[str, dict]],
    inc_problems: list[dict],
    alerts: list[tuple[str, dict]] | None,
    alert_problems: list[dict],
    notes: str | None,
    as_of: dt.datetime,
    stale_hours: float,
    noisy_count: int,
    horizon_hours: float,
) -> dict:
    findings: list[dict] = list(inc_problems) + list(alert_problems)

    def add(rule: str, ref: str, where: str, detail: str) -> None:
        findings.append({"rule": rule, "ref": ref, "where": where, "detail": detail})

    open_items, closed_n, seen = [], 0, set()
    for where, rec in incidents:
        ident = rec.get("id", "")
        for name in INCIDENT_REQUIRED:
            if not rec.get(name):
                add("missing-field", ident, where, f"{name} is empty")
        if ident:
            if ident in seen:
                add("duplicate-id", ident, where, "id already listed above")
            seen.add(ident)
        if rec.get("status", "").lower() in CLOSED:
            closed_n += 1
            continue
        opened = parse_time(rec.get("opened", ""))
        if rec.get("opened") and opened is None:
            add("invalid-time", ident, where, f"opened {rec['opened']!r} is not an ISO 8601 time")
        raw_update = field(rec, "last_update")
        updated = parse_time(raw_update) if raw_update else None
        if raw_update and updated is None:
            add("invalid-time", ident, where, f"last update {raw_update!r} is not an ISO 8601 time")
        reference = updated or opened
        item = {
            "id": ident,
            "title": rec.get("title", ""),
            "status": rec.get("status", ""),
            "owner": field(rec, "owner"),
            "opened": iso(opened) if opened else rec.get("opened", ""),
            "last_update": iso(updated) if updated else raw_update,
            "age_hours": hours(as_of - opened) if opened else None,
            "hours_since_update": hours(as_of - reference) if reference else None,
            "where": where,
            "flags": [],
        }
        bucket = None
        if opened:
            age = item["age_hours"]
            bucket = next(b for b, _, limit in AGE_BUCKETS if limit is None or age < limit)
        item["age_group"] = bucket or "unknown"
        if reference and item["hours_since_update"] > stale_hours:
            item["flags"].append("no-update")
            add("no-update", ident, where, f"no update for {item['hours_since_update']:g} hours")
        if not item["owner"]:
            item["flags"].append("no-owner")
            add("no-owner", ident, where, "no owner")
        open_items.append(item)
    open_items.sort(key=lambda i: (-(i["age_hours"] or 0), i["id"]))

    alert_rows, silences = [], []
    for where, rec in alerts or []:
        name = rec.get("name", "")
        if not name:
            add("missing-field", "", where, "name is empty")
        raw_count = rec.get("count", "")
        count = int(raw_count) if re.fullmatch(r"\d+", raw_count) else None
        if count is None:
            add("invalid-count", name, where, f"count {raw_count!r} is not a whole number")
        times = {}
        for f in ("first_seen", "last_seen", "silenced_until"):
            raw = field(rec, f)
            parsed = parse_time(raw) if raw else None
            if raw and parsed is None:
                add("invalid-time", name, where, f"{f} {raw!r} is not an ISO 8601 time")
            times[f] = iso(parsed) if parsed else raw
            if f == "silenced_until" and parsed:
                left = hours(parsed - as_of)
                state = "expired" if left < 0 else ("expires this shift" if left <= horizon_hours else "active")
                silences.append({"name": name, "until": iso(parsed), "hours_left": left, "state": state})
                if state == "expired":
                    add("silence-expired", name, where, f"silence ended {iso(parsed)} ({-left:g} hours ago)")
                elif state == "expires this shift":
                    add("silence-expires", name, where, f"silence ends {iso(parsed)} (in {left:g} hours)")
        row = {"name": name, "count": count, "first_seen": times["first_seen"], "last_seen": times["last_seen"]}
        row["noisy"] = count is not None and count >= noisy_count
        if row["noisy"]:
            add("noisy-alert", name, where, f"fired {count} times")
        alert_rows.append(row)
    alert_rows.sort(key=lambda a: (-(a["count"] or 0), a["name"]))
    silences.sort(key=lambda s: (s["until"], s["name"]))
    order = {
        r: i
        for i, r in enumerate(
            ("no-update", "no-owner", "noisy-alert", "silence-expired", "silence-expires") + DATA_RULES
        )
    }
    findings.sort(key=lambda f: (order[f["rule"]], f["ref"], f["where"]))
    return {
        "as_of": iso(as_of),
        "stale_hours": stale_hours,
        "noisy_count": noisy_count,
        "horizon_hours": horizon_hours,
        "incidents_read": len(incidents),
        "closed": closed_n,
        "open": open_items,
        "alerts": alert_rows if alerts is not None else None,
        "silences": silences,
        "notes": notes,
        "findings": findings,
    }


def cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render(rep: dict) -> str:
    out = [f"# Shift handover: {rep['as_of']}", ""]
    out.append(
        f"{len(rep['open'])} open incident(s), {rep['closed']} closed in the export. Stale means no update for more "
        f"than {rep['stale_hours']:g} hours; noisy means {rep['noisy_count']} or more firings; the next shift is "
        f"{rep['horizon_hours']:g} hours."
    )
    out += ["", "## Open incidents", ""]
    if not rep["open"]:
        out += ["No open incidents in the export.", ""]
    for bucket, title, _ in (*reversed(AGE_BUCKETS), ("unknown", "Opened time unknown", None)):
        items = [i for i in rep["open"] if i["age_group"] == bucket]
        if not items:
            continue
        out += [f"### {title} ({len(items)})", ""]
        for i in items:
            owner = i["owner"] or "no owner"
            since = f", last update {i['hours_since_update']:g}h ago" if i["hours_since_update"] is not None else ""
            flags = f" **[{', '.join(i['flags'])}]**" if i["flags"] else ""
            out.append(f"- {i['id']}: {i['title']} ({i['status']}, {owner}, opened {i['opened']}{since}){flags}")
        out.append("")
    watch = [f for f in rep["findings"] if f["rule"] in ("no-update", "no-owner", "noisy-alert", "silence-expires")]
    out += ["## What to watch", ""]
    if watch:
        out += [f"- [{f['rule']}] {f['ref']}: {f['detail']}" for f in watch]
    else:
        out.append("Nothing flagged.")
    out.append("")
    if rep["alerts"] is not None:
        noisy = [a for a in rep["alerts"] if a["noisy"]]
        out += [f"## Noisy alerts ({len(noisy)})", ""]
        if noisy:
            out += ["| Alert | Count | First seen | Last seen |", "|---|---:|---|---|"]
            out += [
                f"| {cell(a['name'])} | {a['count']} | {cell(a['first_seen'])} | {cell(a['last_seen'])} |"
                for a in noisy
            ]
        else:
            out.append(f"No alert fired {rep['noisy_count']} or more times.")
        out.append("")
        out += [f"## Silenced ({len(rep['silences'])})", ""]
        if rep["silences"]:
            out += ["| Alert | Silenced until | State |", "|---|---|---|"]
            out += [f"| {cell(s['name'])} | {s['until']} | {s['state']} |" for s in rep["silences"]]
        else:
            out.append("No silences in the alert export.")
        out.append("")
    data = [f for f in rep["findings"] if f["rule"] in DATA_RULES or f["rule"] == "silence-expired"]
    if data:
        out += ["## Check before relying on this note", ""]
        out += [f"- [{f['rule']}] {f['ref'] or '(no id)'} ({f['where']}): {f['detail']}" for f in data]
        out.append("")
    if rep["notes"]:
        out += ["## Notes from the outgoing shift", ""]
        out += [f"> {line}" if line.strip() else ">" for line in rep["notes"].strip().splitlines()]
        out.append("")
    out += ["## Handover", "", "- Outgoing: ____", "- Incoming, acknowledged at: ____", ""]
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shift_handover.py",
        description="Write an on-call or shift handover note from saved incident and alert exports: open "
        "incidents by age, items with no recent update, noisy alerts and silences that expire.",
        epilog="Exit codes: 0 nothing flagged, 1 something flagged for the incoming shift, 2 bad input.",
    )
    p.add_argument("incidents", help="incidents export: JSON list or CSV (id, title, status, opened, ...)")
    p.add_argument("--alerts", help="alert export: JSON or CSV (name, count, first_seen, last_seen, silenced_until)")
    p.add_argument("--notes", help="free-text notes from the outgoing shift")
    p.add_argument("--as-of", default=None, help="handover time, ISO 8601 (default: now, UTC)")
    p.add_argument("--stale-hours", type=float, default=4, help="hours without an update before flagging (default 4)")
    p.add_argument("--noisy-count", type=int, default=10, help="firings that make an alert noisy (default 10)")
    p.add_argument("--horizon-hours", type=float, default=12, help="length of the next shift (default 12)")
    p.add_argument("--json", action="store_true", help="print the computed data as JSON instead of Markdown")
    p.add_argument("--out", default=None, help="write the output to this file instead of standard output")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.as_of is None:
            as_of = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
        else:
            as_of = parse_time(args.as_of)
            if as_of is None:
                raise InputError(f"--as-of {args.as_of!r} is not an ISO 8601 time")
        if args.stale_hours < 0 or args.noisy_count < 1 or args.horizon_hours < 0:
            raise InputError("--stale-hours and --horizon-hours must be 0 or more, --noisy-count 1 or more")
        incidents, inc_problems, header = read_records(
            Path(args.incidents), "incidents", ("incidents", "items", "data")
        )
        check_columns(incidents, INCIDENT_REQUIRED, "incidents", header)
        alerts, alert_problems = None, []
        if args.alerts:
            alerts, alert_problems, header = read_records(Path(args.alerts), "--alerts", ("alerts", "items", "data"))
            check_columns(alerts, ALERT_REQUIRED, "--alerts", header)
        notes = None
        if args.notes:
            notes_path = Path(args.notes)
            if not notes_path.is_file():
                raise InputError(f"--notes {args.notes}: file not found")
            notes = notes_path.read_text(encoding="utf-8-sig", errors="replace")
        rep = analyse(
            incidents,
            inc_problems,
            alerts,
            alert_problems,
            notes,
            as_of,
            args.stale_hours,
            args.noisy_count,
            args.horizon_hours,
        )
    except InputError as exc:
        print(f"shift_handover.py: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(rep, indent=2, sort_keys=True) + "\n" if args.json else render(rep)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 1 if rep["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
