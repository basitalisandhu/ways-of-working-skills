#!/usr/bin/env python3
"""Meeting load and focus-time analysis from a saved calendar export (.ics). Reads one file; no network.

Input: an iCalendar file exported from Outlook, Google Calendar or Apple Calendar. VEVENT blocks are read for
SUMMARY, UID, DTSTART, DTEND or DURATION, RRULE, EXDATE, RECURRENCE-ID, STATUS and TRANSP.

Per day of the window (--start, --days) in the analysis time zone (--tz):
  meetings           timed events that start that day (cancelled and "show as free" events are left out)
  in meetings        time covered by meetings, overlaps counted once
  back-to-back runs  meetings joined by gaps of --gap minutes or less; runs of --run-flag or more are flagged
  free blocks        gaps inside working hours (--work-start, --work-end), longest first; a day with no free
                     block of --focus-min minutes is flagged
Then recurring series with their occurrence count and time in the window, rules that were not expanded, all-day
and skipped events, and a plain-language focus plan.

Recurrence: FREQ=DAILY and FREQ=WEEKLY with INTERVAL, COUNT, UNTIL, WKST and plain BYDAY codes (MO, TU, ...) are
expanded, with EXDATE and RECURRENCE-ID overrides applied. Any other rule is listed as "not expanded", never
guessed; only its first occurrence (DTSTART) is counted, when it falls in the window.

Exit codes: 0 nothing flagged, 1 at least one day without a focus block, a flagged back-to-back run or a rule not
expanded, 2 bad input (missing or unreadable file, not an iCalendar file, bad option value).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone(timedelta(0))
DAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
SUPPORTED_FREQ = {"DAILY", "WEEKLY"}
SUPPORTED_PARTS = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "WKST"}
DURATION_RE = re.compile(r"^P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")
MAX_STEPS = 20000


class InputError(Exception):
    """Bad input: reported on stderr with exit code 2."""


# ---------------------------------------------------------------------------------------------------------------
# iCalendar parsing


def unfold(text: str) -> list[tuple[int, str]]:
    """Join folded continuation lines (RFC 5545 3.1); keep the line number of the first physical line."""
    lines: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if raw[:1] in (" ", "\t") and lines:
            first, value = lines[-1]
            lines[-1] = (first, value + raw[1:])
        else:
            lines.append((number, raw))
    return lines


def split_property(line: str) -> tuple[str, dict[str, str], str] | None:
    """NAME;PARAM=V;PARAM="V":VALUE -> (NAME, params, VALUE), or None when there is no unquoted colon."""
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            head, value = line[:i], line[i + 1 :]
            break
    else:
        return None
    parts = head.split(";")
    params: dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            key, val = part.split("=", 1)
            params[key.upper()] = val.strip('"')
    return parts[0].upper(), params, value


def parse_events(text: str, warnings: list[str]) -> list[dict]:
    if "BEGIN:VCALENDAR" not in text.upper():
        raise InputError("not an iCalendar file (no BEGIN:VCALENDAR line)")
    events: list[dict] = []
    current: dict | None = None
    nested = 0
    for number, line in unfold(text):
        if not line.strip():
            continue
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            if current is not None:
                warnings.append(f"line {current['line']}: BEGIN:VEVENT without END:VEVENT; event skipped")
            current = {"line": number, "props": {}}
            nested = 0
            continue
        if current is None:
            continue
        if upper.startswith("BEGIN:"):
            nested += 1
            continue
        if upper.startswith("END:") and nested:
            nested -= 1
            continue
        if upper == "END:VEVENT":
            events.append(current)
            current = None
            continue
        if nested:
            continue  # VALARM and other sub-components
        prop = split_property(line)
        if prop is None:
            warnings.append(f"line {number}: no ':' in property line; ignored")
            continue
        name, params, value = prop
        current["props"].setdefault(name, []).append((params, value, number))
    if current is not None:
        warnings.append(f"line {current['line']}: BEGIN:VEVENT without END:VEVENT; event skipped")
    return events


def first(event: dict, name: str) -> tuple[dict[str, str], str, int] | None:
    values = event["props"].get(name)
    return values[0] if values else None


def resolve_zone(params: dict[str, str], analysis_tz: tzinfo, warnings: list[str], number: int) -> tzinfo:
    tzid = params.get("TZID")
    if not tzid:
        return analysis_tz
    try:
        return ZoneInfo(tzid)
    except (ZoneInfoNotFoundError, ValueError):
        warnings.append(f'line {number}: TZID "{tzid}" is not a known IANA zone; times read in the analysis zone')
        return analysis_tz


def parse_value(value: str, params: dict[str, str], analysis_tz: tzinfo, warnings: list[str], number: int):
    """Return a date (all-day) or an aware datetime. Raises ValueError on a bad value."""
    value = value.strip()
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value, "%Y%m%d").date()
    if value.endswith("Z"):
        return datetime.strptime(value[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
    return naive.replace(tzinfo=resolve_zone(params, analysis_tz, warnings, number))


def parse_duration(value: str) -> timedelta:
    m = DURATION_RE.fullmatch(value.strip().lstrip("+"))
    if not m or not any(m.groups()):
        raise ValueError(f"bad DURATION {value!r}")
    w, d, h, mi, s = (int(g or 0) for g in m.groups())
    return timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def parse_rrule(value: str) -> tuple[dict[str, str], list[str]]:
    """Return the rule parts and the reasons it cannot be expanded (empty when supported)."""
    parts: dict[str, str] = {}
    for chunk in value.strip().split(";"):
        if "=" in chunk:
            key, val = chunk.split("=", 1)
            parts[key.upper()] = val.upper()
    reasons: list[str] = []
    if parts.get("FREQ") not in SUPPORTED_FREQ:
        reasons.append(f"FREQ={parts.get('FREQ', '(missing)')} is not supported")
    extra = sorted(set(parts) - SUPPORTED_PARTS)
    if extra:
        reasons.append(f"{', '.join(extra)} not supported")
    for code in filter(None, parts.get("BYDAY", "").split(",")):
        if code not in DAY_CODES:
            reasons.append(f"BYDAY value {code} not supported")
    for key in ("INTERVAL", "COUNT"):
        if key in parts and not parts[key].isdigit():
            reasons.append(f"{key}={parts[key]} is not a number")
    if parts.get("WKST", "MO") not in DAY_CODES:
        reasons.append(f"WKST={parts['WKST']} not recognised")
    return parts, reasons


def instant(value) -> datetime | date:
    return value.astimezone(UTC) if isinstance(value, datetime) else value


def occurrence_starts(start: datetime, parts: dict[str, str], until, last_day: date):
    """Yield wall-clock starts of a DAILY or WEEKLY rule, in the zone of DTSTART, up to last_day or COUNT/UNTIL."""
    zone = start.tzinfo
    interval = max(int(parts.get("INTERVAL", "1")), 1)
    count = int(parts["COUNT"]) if "COUNT" in parts else None
    byday = [DAY_CODES.index(c) for c in filter(None, parts.get("BYDAY", "").split(","))]
    wall = start.replace(tzinfo=None)
    produced = 0

    def within_until(candidate: datetime) -> bool:
        if until is None:
            return True
        if isinstance(until, datetime):
            return candidate.astimezone(UTC) <= until.astimezone(UTC)
        return candidate.date() <= until

    if parts["FREQ"] == "DAILY":
        for step in range(MAX_STEPS):
            day = wall.date() + timedelta(days=step * interval)
            if day > last_day:
                return
            if byday and day.weekday() not in byday:
                continue
            candidate = datetime.combine(day, wall.time()).replace(tzinfo=zone)
            if not within_until(candidate):
                return
            yield candidate
            produced += 1
            if count is not None and produced >= count:
                return
        return
    wkst = DAY_CODES.index(parts.get("WKST", "MO"))
    days = sorted(byday or [wall.weekday()], key=lambda d: (d - wkst) % 7)
    week0 = wall.date() - timedelta(days=(wall.weekday() - wkst) % 7)
    for step in range(MAX_STEPS):
        week = week0 + timedelta(days=7 * step * interval)
        if week > last_day:
            return
        for weekday in days:
            day = week + timedelta(days=(weekday - wkst) % 7)
            if day < wall.date():
                continue
            candidate = datetime.combine(day, wall.time()).replace(tzinfo=zone)
            if not within_until(candidate):
                return
            yield candidate
            produced += 1
            if count is not None and produced >= count:
                return


# ---------------------------------------------------------------------------------------------------------------
# Analysis


def fmt_minutes(minutes: int) -> str:
    return f"{minutes // 60}h{minutes % 60:02d}"


def day_label(day: date) -> str:
    return f"{DAY_NAMES[day.weekday()]} {day.isoformat()}"


def hhmm(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def parse_clock(value: str, flag: str) -> time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise InputError(f"{flag} must be HH:MM, got {value!r}") from exc


def build_occurrences(events, analysis_tz, window_start, window_end, warnings, report):
    overridden: dict[str, set] = {}
    for event in events:
        rid = first(event, "RECURRENCE-ID")
        uid = first(event, "UID")
        if rid and uid:
            try:
                value = parse_value(rid[1], rid[0], analysis_tz, warnings, rid[2])
            except ValueError:
                warnings.append(f"line {rid[2]}: unreadable RECURRENCE-ID {rid[1]!r}; ignored")
                continue
            overridden.setdefault(uid[1], set()).add(instant(value))

    occurrences: list[dict] = []
    last_day = (window_end - timedelta(seconds=1)).date()
    for event in events:
        summary = (first(event, "SUMMARY") or ({}, "(no title)", 0))[1].strip() or "(no title)"
        uid = (first(event, "UID") or ({}, f"line-{event['line']}", 0))[1]
        status = (first(event, "STATUS") or ({}, "", 0))[1].upper()
        transp = (first(event, "TRANSP") or ({}, "", 0))[1].upper()
        if status == "CANCELLED":
            report["skipped"].append({"line": event["line"], "summary": summary, "reason": "cancelled"})
            continue
        if transp == "TRANSPARENT":
            report["skipped"].append({"line": event["line"], "summary": summary, "reason": "shown as free"})
            continue
        dtstart = first(event, "DTSTART")
        if dtstart is None:
            warnings.append(f"line {event['line']}: event {summary!r} has no DTSTART; skipped")
            continue
        try:
            start = parse_value(dtstart[1], dtstart[0], analysis_tz, warnings, dtstart[2])
        except ValueError:
            warnings.append(f"line {dtstart[2]}: unreadable DTSTART {dtstart[1]!r} in {summary!r}; event skipped")
            continue
        if not isinstance(start, datetime):
            report["all_day"].append({"line": event["line"], "summary": summary, "date": start.isoformat()})
            continue
        dtend, duration = first(event, "DTEND"), first(event, "DURATION")
        try:
            if dtend is not None:
                end = parse_value(dtend[1], dtend[0], analysis_tz, warnings, dtend[2])
                if not isinstance(end, datetime):
                    raise ValueError("date-only DTEND on a timed event")
                length = end - start
            elif duration is not None:
                length = parse_duration(duration[1])
            else:
                raise ValueError("no DTEND or DURATION")
        except ValueError as exc:
            warnings.append(f"line {event['line']}: {summary!r}: {exc}; event skipped")
            continue
        if length < timedelta(0):
            warnings.append(f"line {event['line']}: {summary!r} ends before it starts; event skipped")
            continue

        rrule = first(event, "RRULE")
        starts: list[datetime] = [start]
        series = None
        if rrule is not None and first(event, "RECURRENCE-ID") is None:
            parts, reasons = parse_rrule(rrule[1])
            until = None
            if not reasons and "UNTIL" in parts:
                try:
                    until = parse_value(parts["UNTIL"], {}, start.tzinfo, warnings, rrule[2])
                except ValueError:
                    reasons.append(f"UNTIL={parts['UNTIL']} unreadable")
            if reasons:
                report["not_expanded"].append(
                    {"line": rrule[2], "summary": summary, "rule": rrule[1].strip(), "reasons": reasons}
                )
            else:
                series = uid
                starts = list(occurrence_starts(start, parts, until, last_day))
                excluded = set()
                for params, value, number in event["props"].get("EXDATE", []):
                    for item in value.split(","):
                        try:
                            excluded.add(instant(parse_value(item, params, analysis_tz, warnings, number)))
                        except ValueError:
                            warnings.append(f"line {number}: unreadable EXDATE {item!r}; ignored")
                skip = excluded | overridden.get(uid, set())
                starts = [s for s in starts if instant(s) not in skip and s.date() not in excluded]
        for occ in starts:
            local = occ.astimezone(analysis_tz)
            if window_start <= local < window_end:
                occurrences.append(
                    {
                        "summary": summary,
                        "uid": uid,
                        "series": series,
                        "rule": rrule[1].strip() if series else None,
                        "start": local,
                        "end": (occ + length).astimezone(analysis_tz),
                        "line": event["line"],
                    }
                )
    occurrences.sort(key=lambda o: (o["start"], o["end"], o["summary"], o["uid"]))
    return occurrences


def merge(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def minutes(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60)


def analyse(args) -> dict:
    try:
        analysis_tz: tzinfo = UTC if args.tz.upper() == "UTC" else ZoneInfo(args.tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InputError(f"--tz {args.tz!r} is not a known IANA time zone") from exc
    work_start = parse_clock(args.work_start, "--work-start")
    work_end = parse_clock(args.work_end, "--work-end")
    if work_end <= work_start:
        raise InputError("--work-end must be after --work-start")
    if args.days < 1 or args.gap < 0 or args.run_flag < 2 or args.focus_min < 1 or args.top < 1:
        raise InputError("--days, --focus-min and --top must be at least 1, --run-flag at least 2, --gap 0 or more")
    if args.start:
        try:
            first_day = date.fromisoformat(args.start)
        except ValueError as exc:
            raise InputError(f"--start must be YYYY-MM-DD, got {args.start!r}") from exc
    else:
        today = datetime.now(analysis_tz).date()
        first_day = today - timedelta(days=today.weekday())
    path: Path = args.ics
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise InputError(f"file not found: {path}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(f"cannot read {path}: {exc}") from exc

    warnings: list[str] = []
    events = parse_events(text, warnings)
    window_start = datetime.combine(first_day, time(0)).replace(tzinfo=analysis_tz)
    window_end = datetime.combine(first_day + timedelta(days=args.days), time(0)).replace(tzinfo=analysis_tz)
    report: dict = {
        "calendar": path.name,
        "time_zone": args.tz,
        "window": {"start": first_day.isoformat(), "days": args.days},
        "settings": {
            "work_start": work_start.strftime("%H:%M"),
            "work_end": work_end.strftime("%H:%M"),
            "gap_minutes": args.gap,
            "run_flag": args.run_flag,
            "focus_minutes": args.focus_min,
        },
        "events_read": len(events),
        "skipped": [],
        "all_day": [],
        "not_expanded": [],
    }
    occs = build_occurrences(events, analysis_tz, window_start, window_end, warnings, report)
    report["all_day"] = [
        a for a in report["all_day"] if first_day.isoformat() <= a["date"] < window_end.date().isoformat()
    ]

    days = []
    for offset in range(args.days):
        day = first_day + timedelta(days=offset)
        day_start = datetime.combine(day, time(0)).replace(tzinfo=analysis_tz)
        day_end = day_start + timedelta(days=1)
        todays = [o for o in occs if o["start"].date() == day]
        clipped = [(max(o["start"], day_start), min(o["end"], day_end)) for o in todays]
        busy = merge(clipped)
        in_meetings = sum(minutes(e - s) for s, e in busy)

        runs = []
        gap = timedelta(minutes=args.gap)
        cluster: list[dict] = []
        cluster_end = None
        for occ in todays:
            if cluster and occ["start"] <= cluster_end + gap:
                cluster.append(occ)
                cluster_end = max(cluster_end, occ["end"])
                continue
            if len(cluster) >= 2:
                runs.append((cluster, cluster_end))
            cluster, cluster_end = [occ], occ["end"]
        if len(cluster) >= 2:
            runs.append((cluster, cluster_end))
        run_rows = [
            {
                "from": hhmm(c[0]["start"]),
                "to": hhmm(end),
                "meetings": len(c),
                "minutes": minutes(end - c[0]["start"]),
                "titles": [o["summary"] for o in c],
                "flagged": len(c) >= args.run_flag,
            }
            for c, end in runs
        ]

        ws = datetime.combine(day, work_start).replace(tzinfo=analysis_tz)
        we = datetime.combine(day, work_end).replace(tzinfo=analysis_tz)
        free = []
        cursor = ws
        for s, e in busy:
            if e <= ws or s >= we:
                continue
            if s > cursor:
                free.append((cursor, min(s, we)))
            cursor = max(cursor, e)
        if cursor < we:
            free.append((cursor, we))
        free_rows = sorted(
            ({"from": hhmm(s), "to": hhmm(e), "minutes": minutes(e - s)} for s, e in free if e > s),
            key=lambda b: (-b["minutes"], b["from"]),
        )
        longest = free_rows[0] if free_rows else None
        days.append(
            {
                "date": day.isoformat(),
                "label": day_label(day),
                "meetings": len(todays),
                "minutes_in_meetings": in_meetings,
                "back_to_back": run_rows,
                "free_blocks": free_rows[: args.top],
                "longest_free": longest,
                "no_focus_block": not longest or longest["minutes"] < args.focus_min,
            }
        )
    report["days"] = days

    series: dict[str, dict] = {}
    for occ in occs:
        if occ["series"]:
            row = series.setdefault(
                occ["series"], {"summary": occ["summary"], "rule": occ["rule"], "occurrences": 0, "minutes": 0}
            )
            row["occurrences"] += 1
            row["minutes"] += minutes(occ["end"] - occ["start"])
    report["recurring_series"] = sorted(series.values(), key=lambda r: (-r["minutes"], r["summary"], r["rule"]))
    report["scheduled_minutes"] = sum(minutes(o["end"] - o["start"]) for o in occs)
    report["meetings"] = len(occs)
    report["warnings"] = warnings
    report["plan"] = focus_plan(report)
    report["flagged"] = bool(
        report["not_expanded"] or any(d["no_focus_block"] or any(r["flagged"] for r in d["back_to_back"]) for d in days)
    )
    return report


def focus_plan(report: dict) -> list[str]:
    s = report["settings"]
    plan: list[str] = []
    for day in report["days"]:
        best = day["longest_free"]
        if not day["no_focus_block"]:
            plan.append(
                f"Block {day['label']} {best['from']}-{best['to']} ({fmt_minutes(best['minutes'])}) for focus work; "
                "it is the longest free time inside working hours that day."
            )
        else:
            longest = f"{best['from']}-{best['to']}, {fmt_minutes(best['minutes'])}" if best else "none"
            plan.append(
                f"{day['label']} has no free block of {s['focus_minutes']} minutes inside working hours "
                f"(longest: {longest}). Plan this as a lighter focus day, or look for a meeting that could move."
            )
    for day in report["days"]:
        for run in day["back_to_back"]:
            if run["flagged"]:
                plan.append(
                    f"{day['label']} {run['from']}-{run['to']} holds {run['meetings']} meetings back to back "
                    f"(gaps of {s['gap_minutes']} minutes or less). A short buffer between two of them would break "
                    "the run; which one is your call."
                )
    series = report["recurring_series"]
    if series:
        recurring = sum(r["minutes"] for r in series)
        verb = "holds" if len(series) == 1 else "hold"
        plan.append(
            f"{len(series)} recurring series {verb} {fmt_minutes(recurring)} of the "
            f"{fmt_minutes(report['scheduled_minutes'])} scheduled in this window (durations summed). They are "
            "listed below; which to keep, shorten or drop is your decision."
        )
    if report["not_expanded"]:
        count = len(report["not_expanded"])
        plan.append(
            f"{count} recurring {'rule was' if count == 1 else 'rules were'} not expanded, so later occurrences are "
            "missing from these numbers. Check those days in the calendar itself."
        )
    return plan


# ---------------------------------------------------------------------------------------------------------------
# Rendering


def cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(rep: dict) -> str:
    s = rep["settings"]
    last = date.fromisoformat(rep["window"]["start"]) + timedelta(days=rep["window"]["days"] - 1)
    out = [
        f"# Focus plan: {rep['window']['start']} to {last.isoformat()}",
        "",
        f"Calendar: {rep['calendar']}. Time zone: {rep['time_zone']}. Working hours {s['work_start']}-"
        f"{s['work_end']}. Focus block: at least {s['focus_minutes']} minutes. Back to back: gaps of "
        f"{s['gap_minutes']} minutes or less, runs of {s['run_flag']} or more flagged.",
        f"{rep['events_read']} events read, {rep['meetings']} meetings in the window.",
        "",
        "## Load per day",
        "",
        "| Day | Meetings | In meetings | Longest free block | Back-to-back runs | Flag |",
        "|---|---|---|---|---|---|",
    ]
    for d in rep["days"]:
        best = d["longest_free"]
        best_text = f"{best['from']}-{best['to']} ({fmt_minutes(best['minutes'])})" if best else "none"
        flagged_runs = sum(1 for r in d["back_to_back"] if r["flagged"])
        flags = []
        if d["no_focus_block"]:
            flags.append("no focus block")
        if flagged_runs:
            flags.append("long run")
        out.append(
            f"| {d['label']} | {d['meetings']} | {fmt_minutes(d['minutes_in_meetings'])} | {best_text} | "
            f"{len(d['back_to_back'])} ({flagged_runs} flagged) | {', '.join(flags) or '-'} |"
        )
    out += ["", "## Free blocks inside working hours", ""]
    for d in rep["days"]:
        blocks = ", ".join(f"{b['from']}-{b['to']} ({fmt_minutes(b['minutes'])})" for b in d["free_blocks"])
        out.append(f"- {d['label']}: {blocks or 'none'}")
    out += ["", "## Back-to-back runs", ""]
    runs = [(d, r) for d in rep["days"] for r in d["back_to_back"]]
    if runs:
        out += ["| Day | From | To | Meetings | Flagged | Titles |", "|---|---|---|---|---|---|"]
        for d, r in runs:
            out.append(
                f"| {d['label']} | {r['from']} | {r['to']} | {r['meetings']} | {'yes' if r['flagged'] else 'no'} | "
                f"{cell('; '.join(r['titles']))} |"
            )
    else:
        out.append("None.")
    out += ["", "## Recurring series in this window", ""]
    if rep["recurring_series"]:
        out += ["| Series | Rule | Occurrences | Time |", "|---|---|---|---|"]
        for r in rep["recurring_series"]:
            out.append(f"| {cell(r['summary'])} | `{r['rule']}` | {r['occurrences']} | {fmt_minutes(r['minutes'])} |")
    else:
        out.append("None.")
    out += ["", "## Not expanded", ""]
    if rep["not_expanded"]:
        for n in rep["not_expanded"]:
            out.append(
                f"- line {n['line']}: {n['summary']} `{n['rule']}`: {'; '.join(n['reasons'])}. Only its first "
                "occurrence is counted, when it falls in the window."
            )
    else:
        out.append("None.")
    out += ["", "## All-day and skipped events", ""]
    extra = [f"- line {a['line']}: {a['summary']} (all day, {a['date']}; not counted)" for a in rep["all_day"]]
    extra += [f"- line {k['line']}: {k['summary']} ({k['reason']}; not counted)" for k in rep["skipped"]]
    out += extra or ["None."]
    out += ["", "## Focus plan", ""]
    out += [f"{i}. {line}" for i, line in enumerate(rep["plan"], start=1)]
    if rep["warnings"]:
        out += ["", "## Warnings", ""] + [f"- {w}" for w in rep["warnings"]]
    return "\n".join(out) + "\n"


def to_json(rep: dict) -> str:
    return json.dumps(rep, indent=2, default=str) + "\n"


def write_output(text: str, output: Path | None) -> None:
    if output is None:
        sys.stdout.write(text)
        return
    if not output.parent.is_dir():
        raise InputError(f"output folder does not exist: {output.parent}")
    output.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="focus_plan.py",
        description="Meeting load, back-to-back runs, free blocks and a focus plan from a saved .ics file (offline).",
        epilog="Exit codes: 0 nothing flagged, 1 a day without a focus block, a flagged run or a rule not expanded, "
        "2 bad input.",
    )
    parser.add_argument("ics", type=Path, help="calendar export (.ics)")
    parser.add_argument("--start", help="first day YYYY-MM-DD (default: Monday of the current week)")
    parser.add_argument("--days", type=int, default=5, help="days to analyse (default 5)")
    parser.add_argument(
        "--tz", default="UTC", help="IANA time zone for the analysis, e.g. Australia/Perth (default UTC)"
    )
    parser.add_argument("--work-start", default="09:00", help="start of working hours HH:MM (default 09:00)")
    parser.add_argument("--work-end", default="17:00", help="end of working hours HH:MM (default 17:00)")
    parser.add_argument("--gap", type=int, default=5, help="minutes between meetings that still count as back to back")
    parser.add_argument(
        "--run-flag", type=int, default=3, help="meetings in a back-to-back run that flag it (default 3)"
    )
    parser.add_argument("--focus-min", type=int, default=90, help="minutes a free block needs to count as focus time")
    parser.add_argument("--top", type=int, default=3, help="free blocks listed per day (default 3)")
    parser.add_argument("--json", action="store_true", help="JSON report instead of Markdown")
    parser.add_argument("--output", type=Path, help="write the report to this file instead of stdout")
    args = parser.parse_args(argv)
    try:
        rep = analyse(args)
        write_output(to_json(rep) if args.json else render_markdown(rep), args.output)
    except InputError as exc:
        print(f"focus_plan.py: {exc}", file=sys.stderr)
        return 2
    return 1 if rep["flagged"] else 0


if __name__ == "__main__":
    sys.exit(main())
