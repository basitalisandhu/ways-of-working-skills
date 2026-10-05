"""Tests for focus_plan.py. Every calendar is synthetic and assembled in the test; nothing touches the network.

The week analysed is Monday 2026-10-05 to Friday 2026-10-09 in UTC unless a test says otherwise.
"""

from __future__ import annotations

from pathlib import Path

from conftest import load_script, run_json, run_main

mod = load_script("focus-plan", "focus_plan.py")
WEEK = ["--start", "2026-10-05"]


def vevent(uid: str, summary: str, start: str, end: str | None = None, *extra: str) -> str:
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"SUMMARY:{summary}", f"DTSTART:{start}"]
    if end:
        lines.append(f"DTEND:{end}")
    lines += list(extra)
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def calendar(tmp_path: Path, *events: str, name: str = "cal.ics") -> str:
    body = "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Example//Test//EN", *events, "END:VCALENDAR"])
    path = tmp_path / name
    path.write_bytes((body + "\r\n").encode("utf-8"))  # bytes, so Windows does not turn \r\n into \r\r\n
    return str(path)


def report(path: str, *extra: str) -> tuple[int, dict]:
    return run_json(mod, [path, *WEEK, "--json", *extra])


def day(rep: dict, iso: str) -> dict:
    return next(d for d in rep["days"] if d["date"] == iso)


def test_empty_calendar_has_full_free_days_and_exits_zero(tmp_path):
    rc, rep = report(calendar(tmp_path))
    assert rc == 0
    assert rep["meetings"] == 0
    assert [d["longest_free"] for d in rep["days"]] == [{"from": "09:00", "to": "17:00", "minutes": 480}] * 5
    assert rep["not_expanded"] == [] and rep["recurring_series"] == []


def test_not_an_icalendar_file_exits_2(tmp_path):
    path = tmp_path / "notes.ics"
    path.write_text("just some text\n", encoding="utf-8")
    rc, _, err = run_main(mod, [str(path), *WEEK])
    assert rc == 2
    assert "not an iCalendar file" in err
    rc, _, err = run_main(mod, [str(tmp_path / "missing.ics"), *WEEK])
    assert rc == 2 and "file not found" in err


def test_malformed_lines_are_reported_with_line_numbers_and_skipped(tmp_path):
    path = calendar(
        tmp_path,
        vevent("e1", "Review", "20261005T100000Z", "20261005T110000Z", "THIS LINE HAS NO COLON"),
        vevent("e2", "Broken", "not-a-date", "20261005T120000Z"),
    )
    rc, rep = report(path)
    assert rep["meetings"] == 1
    assert any("no ':' in property line" in w and w.startswith("line 9:") for w in rep["warnings"])
    assert any("unreadable DTSTART 'not-a-date'" in w for w in rep["warnings"])


def test_back_to_back_runs_flag_three_or_more(tmp_path):
    path = calendar(
        tmp_path,
        vevent("a", "One", "20261005T090000Z", "20261005T093000Z"),
        vevent("b", "Two", "20261005T093000Z", "20261005T100000Z"),
        vevent("c", "Three", "20261005T100500Z", "20261005T110000Z"),
        vevent("d", "Four", "20261006T090000Z", "20261006T093000Z"),
        vevent("e", "Five", "20261006T093000Z", "20261006T100000Z"),
    )
    rc, rep = report(path)
    assert rc == 1
    monday, tuesday = day(rep, "2026-10-05"), day(rep, "2026-10-06")
    assert monday["back_to_back"] == [
        {
            "from": "09:00",
            "to": "11:00",
            "meetings": 3,
            "minutes": 120,
            "titles": ["One", "Two", "Three"],
            "flagged": True,
        }
    ]
    assert tuesday["back_to_back"][0]["meetings"] == 2 and tuesday["back_to_back"][0]["flagged"] is False
    rc, rep = report(path, "--gap", "0")
    assert day(rep, "2026-10-05")["back_to_back"][0]["meetings"] == 2


def test_day_without_a_focus_block_is_flagged(tmp_path):
    path = calendar(
        tmp_path,
        vevent("a", "Morning", "20261007T090000Z", "20261007T103000Z"),
        vevent("b", "Midday", "20261007T113000Z", "20261007T130000Z"),
        vevent("c", "Afternoon", "20261007T140000Z", "20261007T163000Z"),
    )
    rc, rep = report(path)
    wednesday = day(rep, "2026-10-07")
    assert rc == 1
    assert wednesday["no_focus_block"] is True
    assert wednesday["free_blocks"] == [
        {"from": "10:30", "to": "11:30", "minutes": 60},
        {"from": "13:00", "to": "14:00", "minutes": 60},
        {"from": "16:30", "to": "17:00", "minutes": 30},
    ]
    assert any(p.startswith("Wed 2026-10-07 has no free block of 90 minutes") for p in rep["plan"])
    rc, rep = report(path, "--focus-min", "60")
    assert day(rep, "2026-10-07")["no_focus_block"] is False


def test_weekly_rule_with_exdate_override_and_count(tmp_path):
    path = calendar(
        tmp_path,
        vevent(
            "sync",
            "Ops sync",
            "20260928T140000Z",
            "20260928T150000Z",
            "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR;COUNT=7",
            "EXDATE:20261009T140000Z",
        ),
        vevent("sync", "Ops sync moved", "20261007T160000Z", "20261007T163000Z", "RECURRENCE-ID:20261007T140000Z"),
        vevent("daily", "Standup", "20261005T083000Z", "20261005T084500Z", "RRULE:FREQ=DAILY;UNTIL=20261007T235959Z"),
    )
    rc, rep = report(path)
    starts = {d["date"]: d["meetings"] for d in rep["days"]}
    # Ops sync: Sep 28, 30, Oct 2, 5, 7 (moved), 9 (excluded) = COUNT 7 reached at Oct 9, so Oct 12 never comes.
    assert starts == {"2026-10-05": 2, "2026-10-06": 1, "2026-10-07": 2, "2026-10-08": 0, "2026-10-09": 0}
    series = {s["summary"]: s for s in rep["recurring_series"]}
    assert series["Ops sync"]["occurrences"] == 1
    assert series["Standup"]["occurrences"] == 3 and series["Standup"]["minutes"] == 45


def test_unsupported_rule_is_listed_not_guessed(tmp_path):
    path = calendar(
        tmp_path,
        vevent("m", "Steering group", "20261006T130000Z", "20261006T140000Z", "RRULE:FREQ=MONTHLY;BYDAY=1TU"),
        vevent("w", "Fortnightly", "20261001T100000Z", "20261001T110000Z", "RRULE:FREQ=WEEKLY;BYSETPOS=1"),
    )
    rc, rep = report(path)
    assert rc == 1
    assert [n["summary"] for n in rep["not_expanded"]] == ["Steering group", "Fortnightly"]
    assert "FREQ=MONTHLY is not supported" in rep["not_expanded"][0]["reasons"]
    assert "BYSETPOS not supported" in rep["not_expanded"][1]["reasons"]
    assert rep["meetings"] == 1  # only the first steering occurrence falls in the week; nothing is invented
    assert rep["recurring_series"] == []


def test_cancelled_free_and_all_day_events_are_not_counted(tmp_path):
    path = calendar(
        tmp_path,
        vevent("x", "Cancelled", "20261005T100000Z", "20261005T110000Z", "STATUS:CANCELLED"),
        vevent("y", "Hold", "20261005T120000Z", "20261005T130000Z", "TRANSP:TRANSPARENT"),
        "\r\n".join(["BEGIN:VEVENT", "UID:z", "SUMMARY:Public holiday", "DTSTART;VALUE=DATE:20261009", "END:VEVENT"]),
    )
    rc, rep = report(path)
    assert rc == 0 and rep["meetings"] == 0
    assert [s["reason"] for s in rep["skipped"]] == ["cancelled", "shown as free"]
    assert rep["all_day"] == [{"line": 18, "summary": "Public holiday", "date": "2026-10-09"}]


def test_time_zones_folded_lines_and_unknown_tzid(tmp_path):
    path = calendar(
        tmp_path,
        "BEGIN:VEVENT\r\nUID:f\r\nSUMMARY:Quarterly planning with a long\r\n  title\r\nDTSTART:20261005T010000Z\r\n"
        "DURATION:PT1H30M\r\nEND:VEVENT",
        "BEGIN:VEVENT\r\nUID:g\r\nSUMMARY:Local\r\nDTSTART;TZID=Example Standard Time:20261006T100000\r\n"
        "DTEND;TZID=Example Standard Time:20261006T110000\r\nEND:VEVENT",
    )
    rc, rep = report(path, "--tz", "Australia/Perth")
    monday = day(rep, "2026-10-05")
    assert monday["meetings"] == 1 and monday["minutes_in_meetings"] == 90
    assert monday["free_blocks"][0] == {"from": "10:30", "to": "17:00", "minutes": 390}
    assert any('TZID "Example Standard Time" is not a known IANA zone' in w for w in rep["warnings"])
    rc, _, err = run_main(mod, [path, *WEEK, "--tz", "Not/AZone"])
    assert rc == 2 and "--tz" in err


def test_output_file_and_deterministic_markdown(tmp_path):
    path = calendar(tmp_path, vevent("a", "Review", "20261005T100000Z", "20261005T110000Z"))
    out = tmp_path / "plan.md"
    rc, stdout, _ = run_main(mod, [path, *WEEK, "--output", str(out)])
    assert rc == 0 and stdout == ""
    assert out.read_text(encoding="utf-8") == run_main(mod, [path, *WEEK])[1]
    rc, _, err = run_main(mod, [path, *WEEK, "--output", str(tmp_path / "nope" / "plan.md")])
    assert rc == 2 and "output folder does not exist" in err


GOLDEN = """\
# Focus plan: 2026-10-05 to 2026-10-06

Calendar: golden.ics. Time zone: UTC. Working hours 09:00-17:00. Focus block: at least 90 minutes. Back to \
back: gaps of 5 minutes or less, runs of 3 or more flagged.
5 events read, 6 meetings in the window.

## Load per day

| Day | Meetings | In meetings | Longest free block | Back-to-back runs | Flag |
|---|---|---|---|---|---|
| Mon 2026-10-05 | 4 | 3h00 | 11:00-14:00 (3h00) | 1 (1 flagged) | long run |
| Tue 2026-10-06 | 2 | 1h15 | 09:15-13:00 (3h45) | 0 (0 flagged) | - |

## Free blocks inside working hours

- Mon 2026-10-05: 11:00-14:00 (3h00), 15:00-17:00 (2h00)
- Tue 2026-10-06: 09:15-13:00 (3h45), 14:00-17:00 (3h00)

## Back-to-back runs

| Day | From | To | Meetings | Flagged | Titles |
|---|---|---|---|---|---|
| Mon 2026-10-05 | 09:00 | 11:00 | 3 | yes | Standup; Design review; Planning |

## Recurring series in this window

| Series | Rule | Occurrences | Time |
|---|---|---|---|
| Standup | `FREQ=DAILY;COUNT=2` | 2 | 0h30 |

## Not expanded

- line 34: Steering `FREQ=YEARLY`: FREQ=YEARLY is not supported. Only its first occurrence is counted, when it \
falls in the window.

## All-day and skipped events

None.

## Focus plan

1. Block Mon 2026-10-05 11:00-14:00 (3h00) for focus work; it is the longest free time inside working hours \
that day.
2. Block Tue 2026-10-06 09:15-13:00 (3h45) for focus work; it is the longest free time inside working hours \
that day.
3. Mon 2026-10-05 09:00-11:00 holds 3 meetings back to back (gaps of 5 minutes or less). A short buffer between \
two of them would break the run; which one is your call.
4. 1 recurring series holds 0h30 of the 4h15 scheduled in this window (durations summed). They are listed \
below; which to keep, shorten or drop is your decision.
5. 1 recurring rule was not expanded, so later occurrences are missing from these numbers. Check those days in \
the calendar itself.
"""


def test_golden_markdown(tmp_path):
    path = calendar(
        tmp_path,
        vevent("s", "Standup", "20261005T090000Z", "20261005T091500Z", "RRULE:FREQ=DAILY;COUNT=2"),
        vevent("d", "Design review", "20261005T091500Z", None, "DURATION:PT45M"),
        vevent("p", "Planning", "20261005T100000Z", "20261005T110000Z"),
        vevent("o", "One to one", "20261005T140000Z", "20261005T150000Z"),
        vevent("y", "Steering", "20261006T130000Z", "20261006T140000Z", "RRULE:FREQ=YEARLY"),
        name="golden.ics",
    )
    rc, out, _ = run_main(mod, [path, "--start", "2026-10-05", "--days", "2"])
    assert rc == 1
    assert out == GOLDEN


def test_help():
    rc, out, _ = run_main(mod, ["--help"])
    assert rc == 0
    for flag in ("--start", "--days", "--tz", "--work-start", "--work-end", "--gap", "--focus-min", "--json"):
        assert flag in out
