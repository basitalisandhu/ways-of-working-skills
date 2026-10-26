"""Seeded fuzz tests for every script that reads files from outside: each run mutates one input file of a working
example (random bytes, truncation, flipped bytes, inserted delimiters, JSON values swapped for other types, shuffled,
dropped or repeated lines, an empty file) and requires the pack's exit-code convention to hold. Bad input must exit 2
with a message, never a traceback. The generator is random.Random seeded by the skill name, so a failure repeats
exactly; raise FUZZ_RUNS locally to search harder."""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

import pytest
from conftest import ROOT, load_script, run_main
from test_exit_codes import WRITERS, documented_case

FUZZ_RUNS = int(os.environ.get("FUZZ_RUNS", "200"))
EXAMPLES = ROOT / "plugins" / "ways-of-working" / "skills" / "next-actions-sweep" / "examples"
SPECIALS = ['"', "'", ",", "|", "\n", "\r", "\t", "\x00", "{", "}", "[", "]", ":", "#", "-", "﻿", "é", "‮",
            "9999-99-99", "2026-02-30", "T25:99", "NaN", "1e999", "-1", "BEGIN:VEVENT", "END:VEVENT", "RRULE:FREQ=",
            "DTSTART:", "## ", "- [ ] ", "@", "=", ";", "\\", " "]
ODD_VALUES = [None, 0, -1, 1.5, "", "x", [], {}, True, "2026-13-45", [[]], {"a": None}, 10**30]


def swap_json_values(rng: random.Random, node):
    if rng.random() < 0.15:
        return rng.choice(ODD_VALUES)
    if isinstance(node, dict):
        return {k: swap_json_values(rng, v) for k, v in node.items() if rng.random() > 0.1}
    if isinstance(node, list):
        return [swap_json_values(rng, v) for v in node if rng.random() > 0.1]
    return node


def mutate(rng: random.Random, data: bytes) -> bytes:
    op = rng.randrange(9)
    lines = data.split(b"\n")
    if op == 0:
        return bytes(rng.randrange(256) for _ in range(rng.randrange(200)))
    if op == 1 and data:
        return data[: rng.randrange(len(data))]
    if op == 2 and data:
        buf = bytearray(data)
        for _ in range(rng.randrange(1, 6)):
            buf[rng.randrange(len(buf))] = rng.randrange(256)
        return bytes(buf)
    if op == 3:
        text = data.decode("utf-8", "replace")
        for _ in range(rng.randrange(1, 8)):
            i = rng.randrange(len(text) + 1)
            text = text[:i] + rng.choice(SPECIALS) + text[i:]
        return text.encode("utf-8")
    if op == 4:
        try:
            return json.dumps(swap_json_values(rng, json.loads(data))).encode("utf-8")
        except ValueError:
            return data
    if op == 5:
        rng.shuffle(lines)
        return b"\n".join(lines)
    if op == 6:
        return b"\n".join(line for line in lines if rng.random() > 0.3)
    if op == 7:
        return b""
    i = rng.randrange(len(lines))
    lines[i:i] = [lines[i]] * rng.randrange(1, 4)
    return b"\n".join(lines)


def weekly_case(d: Path):
    (d / "git.log").write_text("a1b2c3d\t2026-09-30\tExample Dev\tAdd retry to checkout (#12)\n"
                               "b2c3d4e\t2026-09-29\tExample Dev\tFix the export timeout\n", encoding="utf-8")
    prs = [{"number": 15, "title": "Rate limit the export API", "url": "https://github.com/OWNER/shop/pull/15",
            "state": "OPEN", "isDraft": False, "updatedAt": "2026-09-28T10:00:00Z", "labels": [{"name": "blocked"}],
            "author": {"login": "example-dev"}},
           {"number": 12, "title": "Add retry to checkout", "url": "https://github.com/OWNER/shop/pull/12",
            "state": "MERGED", "mergedAt": "2026-09-30T10:00:00Z", "updatedAt": "2026-09-30T10:00:00Z", "labels": []}]
    issues = [{"number": 20, "title": "Login page times out", "url": "https://github.com/OWNER/shop/issues/20",
               "state": "OPEN", "updatedAt": "2026-09-28T10:00:00Z", "labels": [], "assignees": [{"login": "x"}]}]
    (d / "prs.json").write_text(json.dumps(prs), encoding="utf-8")
    (d / "issues.json").write_text(json.dumps(issues), encoding="utf-8")
    (d / "last.md").write_text("- [shop#15](https://github.com/OWNER/shop/pull/15) Rate limit the export API "
                               "(in review, carried 1 week)\n- Login page times out\n", encoding="utf-8")
    argv = ["--git-log", f"shop={d / 'git.log'}", "--prs", str(d / "prs.json"), "--issues", str(d / "issues.json"),
            "--last-week", str(d / "last.md"), "--as-of", "2026-10-01"]
    return "weekly_status_rollup.py", argv, [d / "git.log", d / "prs.json", d / "issues.json", d / "last.md"]


def next_actions_case(d: Path):
    names = ["work-mail.json", "home-mail.json", "teams.json", "slack.json", "other.csv"]
    for name in names:
        (d / name).write_bytes((EXAMPLES / name).read_bytes())
    argv = ["--source", f"work={d / 'work-mail.json'}", "--source", f"home={d / 'home-mail.json'}",
            "--source", f"teams={d / 'teams.json'}", "--source", f"slack={d / 'slack.json'}:slack-dm",
            "--source", f"other={d / 'other.csv'}", "--me", "alex.morgan@example.com", "--me", "U0ME",
            "--as-of", "2026-10-16T09:00:00Z"]
    return "next_actions.py", argv, [d / name for name in names]


def one_on_one_case(d: Path):
    (d / "notes").mkdir()
    note = d / "notes" / "alex-example.md"
    note.write_text("# Alex Example\ncadence: 7\n\n## 2026-09-20\n- [ ] me: send the budget sheet (due 2026-09-25)\n"
                    "- [ ] Alex: draft the plan\n- topic: hiring\n\n## 2026-09-27\n- [x] me: send the budget sheet\n"
                    "- topic: hiring\n", encoding="utf-8")
    return "one_on_one_ledger.py", [str(d / "notes"), "--as-of", "2026-10-05"], [note]


def build_case(skill: str, d: Path):
    if skill == "weekly-status-rollup":
        return weekly_case(d)
    if skill == "next-actions-sweep":
        return next_actions_case(d)
    if skill == "one-on-one-ledger":
        return one_on_one_case(d)
    name, argv, _ = documented_case(skill, d)
    files = []
    for arg in argv:
        path = Path(arg)
        if arg.startswith(str(d)) and path.is_file():
            files.append(path)
        elif arg.startswith(str(d)) and path.is_dir():
            files += sorted(p for p in path.iterdir() if p.is_file())
    return name, argv, files


ALL = [*WRITERS, "weekly-status-rollup", "next-actions-sweep", "one-on-one-ledger"]


def test_every_skill_is_fuzzed():
    skills = (ROOT / "plugins" / "ways-of-working" / "skills").glob("*/SKILL.md")
    assert sorted(ALL) == sorted(p.parent.name for p in skills)


@pytest.mark.parametrize("skill", ALL)
def test_mutated_inputs_keep_the_exit_code_convention(skill, tmp_path):
    name, argv, files = build_case(skill, tmp_path)
    assert files, f"{skill}: no input files to mutate"
    mod = load_script(skill, name)
    rc, _, err = run_main(mod, argv)
    assert rc in (0, 1), f"{skill}: the unmutated example exits {rc}: {err}"
    rng = random.Random(skill)
    seeds = {f: f.read_bytes() for f in files}
    for run in range(FUZZ_RUNS):
        target = rng.choice(files)
        data = mutate(rng, seeds[target])
        target.write_bytes(data)
        try:
            rc, _, err = run_main(mod, argv)
        except Exception as exc:  # noqa: BLE001 - the point is to catch any crash
            pytest.fail(f"{skill} run {run}: {type(exc).__name__}: {exc} on {target.name} = {data[:200]!r}")
        assert rc in (0, 1, 2, 3), f"{skill} run {run}: exit {rc} on {target.name} = {data[:200]!r}"
        assert "Traceback" not in err
        target.write_bytes(seeds[target])


# Regressions for the crashes the fuzzer found on 2026-10-26, one per distinct cause.

@pytest.mark.parametrize("pr", [
    {"number": 15, "title": True, "url": "https://github.com/OWNER/shop/pull/15", "state": "OPEN"},
    {"number": 15, "title": "t", "url": -1, "state": "OPEN"},
    {"number": 15, "title": "t", "url": "https://github.com/OWNER/shop/pull/15", "state": [[]]},
    {"number": 15, "title": "t", "url": "https://github.com/OWNER/shop/pull/15", "state": "OPEN", "labels": -1},
    {"number": 15, "title": "t", "url": "https://github.com/OWNER/shop/pull/15", "state": "OPEN",
     "labels": [{"name": 0}]},
    {"number": 15, "title": "t", "url": "https://github.com/OWNER/shop/pull/15", "state": "OPEN", "author": True},
    {"number": 15, "title": "t", "url": "https://github.com/OWNER/shop/pull/15", "state": "OPEN",
     "updatedAt": 10**30},
    {"number": True, "title": "t"},
])
def test_weekly_status_reads_odd_json_types_without_a_traceback(pr, tmp_path):
    (tmp_path / "prs.json").write_text(json.dumps([pr]), encoding="utf-8")
    (tmp_path / "issues.json").write_text(json.dumps([{"number": 2, "title": "x", "state": "OPEN",
                                                        "assignees": True}]), encoding="utf-8")
    rc, _, err = run_main(load_script("weekly-status-rollup", "weekly_status_rollup.py"),
                          ["--prs", str(tmp_path / "prs.json"), "--issues", str(tmp_path / "issues.json"),
                           "--as-of", "2026-10-01"])
    assert rc in (0, 1, 2) and "Traceback" not in err


def test_change_request_with_a_nested_action_list_exits_2(tmp_path):
    plan = {"format_version": "1.2", "resource_changes": [
        {"address": "aws_s3_bucket.logs", "type": "aws_s3_bucket", "change": {"actions": [["delete"], "create"]}}]}
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    rc, _, err = run_main(load_script("change-request-writer", "change_request.py"), [str(tmp_path / "plan.json")])
    assert rc == 2 and "needs address, type and change.actions" in err


def test_next_actions_reads_a_teams_message_with_odd_body_and_mentions(tmp_path):
    record = {"id": "t1", "messageType": "message", "createdDateTime": "2026-10-16T08:15:00Z",
              "from": {"user": {"id": "u-jamie", "displayName": "Jamie Fox"}},
              "body": {"content": {"nested": 1}}, "mentions": 7}
    (tmp_path / "teams.json").write_text(json.dumps({"value": [record]}), encoding="utf-8")
    rc, _, err = run_main(load_script("next-actions-sweep", "next_actions.py"),
                          ["--source", f"teams={tmp_path / 'teams.json'}", "--me", "u-alex",
                           "--as-of", "2026-10-16T09:00:00Z"])
    assert rc in (0, 1) and "Traceback" not in err


@pytest.mark.parametrize(("skill", "script", "argv"), [
    ("risk-exception-register", "exception_register.py", ["{f}", "--as-of", "2026-10-05"]),
    ("shift-handover", "shift_handover.py", ["{f}", "--as-of", "2026-10-05T08:00:00Z"]),
    ("vendor-scorecard", "vendor_scorecard.py", ["{f}"]),
    ("risk-register-refresh", "risk_register_refresh.py", ["{f}", "--as-of", "2026-10-05"]),
    ("next-actions-sweep", "next_actions.py", ["--source", "x={f}:csv", "--me", "me@example.com",
                                               "--as-of", "2026-10-16T09:00:00Z"]),
])
def test_a_csv_with_a_nul_character_exits_2_on_every_python(skill, script, argv, tmp_path):
    f = tmp_path / "in.csv"
    f.write_bytes(b"id,title\nA-1,te\x00xt\n")
    rc, _, err = run_main(load_script(skill, script), [a.replace("{f}", str(f)) for a in argv])
    assert rc == 2 and "line 2 holds a NUL character" in err
