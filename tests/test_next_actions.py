"""Tests for next_actions.py. Mail, Teams, Slack and CSV exports are synthetic files written in each test; the
golden check runs the synthetic exports shipped in the skill's examples/ folder."""

from __future__ import annotations

import json

import pytest
from conftest import SKILLS, load_script, run_main, write_files

mod = load_script("next-actions-sweep", "next_actions.py")
AS_OF = "2026-10-16T09:00:00Z"  # a Friday
ME = ["--me", "alex.morgan@example.com", "--me", "alex@example.org", "--me", "Alex Morgan", "--me", "U0ME"]
EXAMPLES = SKILLS / "next-actions-sweep" / "examples"


def mail(i, subject, body, when, sender=("Sam Lee", "sam.lee@example.com"), to=("alex.morgan@example.com",), cc=(),
         conv=None, **extra):
    rec = {
        "id": f"m{i}",
        "subject": subject,
        "from": {"emailAddress": {"name": sender[0], "address": sender[1]}},
        "toRecipients": [{"emailAddress": {"address": a}} for a in to],
        "ccRecipients": [{"emailAddress": {"address": a}} for a in cc],
        "receivedDateTime": when,
        "isRead": True,
        "bodyPreview": body,
        "conversationId": conv or f"conv-{i}",
        "webLink": f"https://outlook.example.com/mail/m{i}",
    }
    rec.update(extra)
    return rec


def sweep(tmp_path, sources: dict[str, object], *extra: str) -> tuple[int, dict, str]:
    """sources: {name: list or text}; .csv suffix in the name's file when text starts without [ or {."""
    args = []
    for name, data in sources.items():
        text = data if isinstance(data, str) else json.dumps(data)
        fname = f"{name}.csv" if isinstance(data, str) and not text.lstrip().startswith(("[", "{")) else f"{name}.json"
        write_files(tmp_path, {fname: text})
        args += ["--source", f"{name}={tmp_path / fname}"]
    rc, out, err = run_main(mod, [*args, *ME, "--as-of", AS_OF, "--json", *extra])
    return rc, (json.loads(out) if rc in (0, 1) else {}), err


def labels(rep: dict) -> dict[str, str]:
    return {(i["subject"] or i["excerpt"]): i["label"] for i in rep["items"]}


def test_each_label_from_mail(tmp_path):
    msgs = [
        mail(1, "Budget", "Can you approve the budget?", "2026-10-16T08:00:00Z"),
        mail(2, "Notes", "Send the notes by Friday.", "2026-10-15T08:00:00Z"),
        mail(3, "Lunch", "Are you free?", "2026-10-15T09:00:00Z"),
        mail(4, "Move", "Can everyone pack by Friday?", "2026-10-15T10:00:00Z", to=("team@example.com",),
             cc=("alex.morgan@example.com",)),
        mail(5, "Report", "Please read", "2026-10-15T11:00:00Z", sender=("Reports", "no-reply@example.com")),
        mail(6, "Status", "All green today.", "2026-10-15T12:00:00Z"),
        mail(7, "Other", "Can you help?", "2026-10-15T13:00:00Z", to=("someone@example.com",)),
    ]
    rc, rep, _ = sweep(tmp_path, {"work": {"value": msgs}})
    assert rc == 1
    assert labels(rep) == {
        "Budget": "decide",
        "Notes": "do",
        "Lunch": "reply",
        "Move": "fyi",
        "Report": "fyi",
        "Status": "fyi",
        "Other": "fyi",
    }
    why = {i["subject"]: i["why"][0] for i in rep["items"]}
    assert why["Move"] == "you are only in Cc" and why["Report"] == "automated sender"
    assert why["Other"] == "not addressed to you"
    assert next(i for i in rep["items"] if i["subject"] == "Notes")["deadline"] == "2026-10-16"


def test_waiting_on_others_and_answered_threads(tmp_path):
    me = ("Alex Morgan", "alex.morgan@example.com")
    msgs = [
        mail(1, "Vendor", "Paperwork attached.", "2026-10-13T09:00:00Z", conv="c-vendor"),
        mail(2, "RE: Vendor", "Could you confirm the date?", "2026-10-13T11:00:00Z", sender=me,
             to=("taylor@example.com",), conv="c-vendor"),
        mail(3, "Review", "Are you joining?", "2026-10-15T08:00:00Z", conv="c-review"),
        mail(4, "RE: Review", "Yes, see you there.", "2026-10-15T09:00:00Z", sender=me, conv="c-review"),
        mail(5, "Fresh", "Any news?", "2026-10-15T10:00:00Z", sender=me, to=("kai@example.com",)),
    ]
    rc, rep, _ = sweep(tmp_path, {"work": msgs})
    assert rc == 1
    assert rep["items"] == []
    assert [(w["subject"], w["to"], w["days_waiting"]) for w in rep["waiting"]] == [
        ("RE: Vendor", ["taylor@example.com"], 2.9)
    ]
    assert rep["counts"]["answered"] == 2
    _, rep, _ = sweep(tmp_path, {"work": msgs}, "--waiting-days", "0.5")
    assert [w["subject"] for w in rep["waiting"]] == ["RE: Vendor", "Fresh"]


def test_duplicates_across_accounts_are_merged_with_every_source(tmp_path):
    dana = ("Dana Cole", "dana.cole@example.net")
    work = [mail(1, "Talk slot", "Would you like 2pm?", "2026-10-15T12:00:00Z", sender=dana, conv="a")]
    home = [
        mail(2, "Talk slot", "Would you like 2pm?", "2026-10-15T12:30:00Z", sender=dana, to=("alex@example.org",),
             conv="b"),
        mail(3, "Shared thread", "Please check.", "2026-10-15T13:00:00Z", conv="same"),
    ]
    work.append(mail(4, "Shared thread", "Please check.", "2026-10-15T13:00:00Z", conv="same"))
    rc, rep, _ = sweep(tmp_path, {"work": work, "home": home})
    assert rc == 1
    assert [(i["subject"], i["sources"]) for i in rep["items"]] == [
        ("Talk slot", ["home", "work"]),
        ("Shared thread", ["home", "work"]),
    ]
    assert rep["counts"]["duplicates_merged"] == 1
    # more than an hour apart is not a duplicate
    home[0]["receivedDateTime"] = "2026-10-15T13:30:00Z"
    _, rep, _ = sweep(tmp_path, {"work": work, "home": home})
    assert [i["subject"] for i in rep["items"]].count("Talk slot") == 2


@pytest.mark.parametrize(
    ("text", "sent", "expected"),
    [
        ("Due 2026-10-20 please", "2026-10-15T08:00:00Z", "2026-10-20"),
        ("as discussed on 2026-10-01", "2026-10-15T08:00:00Z", ""),
        ("send it by Friday", "2026-10-14T08:00:00Z", "2026-10-16"),
        ("send it by Monday", "2026-10-15T08:00:00Z", "2026-10-19"),
        ("by tomorrow please", "2026-10-15T08:00:00Z", "2026-10-16"),
        ("before EOD", "2026-10-15T08:00:00Z", "2026-10-15"),
        ("see https://x.example.com/2026-12-01?by=tomorrow", "2026-10-15T08:00:00Z", ""),
        ("next week sometime", "2026-10-15T08:00:00Z", ""),
        ("we met on Friday", "2026-10-15T08:00:00Z", ""),
    ],
)
def test_deadlines_are_explicit_forms_only(text, sent, expected):
    found = mod.find_deadline(text, mod.parse_time(sent))
    assert (found[0].isoformat() if found else "") == expected


def test_scoring_weights_order_and_top(tmp_path):
    msgs = [
        mail(1, "Plain", "Can you look?", "2026-10-15T09:00:00Z"),
        mail(2, "Same score older", "Can you look?", "2026-10-15T08:00:00Z"),
        mail(3, "Boss", "Can you look?", "2026-10-16T08:00:00Z", sender=("Priya Shah", "priya@example.com"),
             importance="high", flag={"flagStatus": "flagged"}, isRead=False),
    ]
    rc, rep, _ = sweep(tmp_path, {"work": msgs}, "--vip", "priya@example.com")
    assert rc == 1
    scores = {i["subject"]: i["score"] for i in rep["items"]}
    # reply 10 + to you 30 + age (24h and 25h -> 2), and the VIP adds 25 + 10 + 15 + 5
    assert scores == {"Boss": 95, "Same score older": 42, "Plain": 42}
    assert [i["subject"] for i in rep["items"]] == ["Boss", "Same score older", "Plain"]
    _, rep, _ = sweep(tmp_path, {"work": msgs}, "--vip", "priya@example.com", "--top", "1")
    assert [i["rank"] for i in rep["items"]] == [1] and rep["counts"]["ranked"] == 3


def test_teams_html_mentions_chats_and_system_events(tmp_path):
    teams = {
        "value": [
            {"id": "t1", "messageType": "message", "createdDateTime": "2026-10-16T08:15:00.1234567Z",
             "chatId": "19:chat", "from": {"user": {"id": "u-jamie", "displayName": "Jamie Fox"}},
             "body": {"content": "<p>Which option &amp; when?</p>"}, "webUrl": "https://teams.example.com/t1"},
            {"id": "t2", "createdDateTime": "2026-10-15T14:00:00Z",
             "channelIdentity": {"teamId": "a", "channelId": "b"},
             "from": {"user": {"id": "u-kai", "displayName": "Kai Ito"}},
             "body": {"content": "<at id=\"0\">Alex Morgan</at> can you review?"},
             "mentions": [{"mentioned": {"user": {"id": "u-alex", "displayName": "Alex Morgan"}}}]},
            {"id": "t3", "createdDateTime": "2026-10-15T15:00:00Z",
             "channelIdentity": {"teamId": "a", "channelId": "c"},
             "from": {"application": {"displayName": "Deploy bot"}}, "body": {"content": "Deployed?"}},
            {"id": "t4", "messageType": "systemEventMessage", "createdDateTime": "2026-10-15T15:05:00Z",
             "from": None, "body": {"content": ""}},
        ]
    }
    rc, rep, _ = sweep(tmp_path, {"teams": teams})
    assert rc == 1
    assert rep["sources"][0]["format"] == "graph-teams" and rep["sources"][0]["messages"] == 3
    got = {i["excerpt"]: (i["label"], i["why"]) for i in rep["items"]}
    assert got["Which option & when?"][0] == "decide" and "direct chat" in got["Which option & when?"][1]
    assert got["Alex Morgan can you review?"] == ("reply", ['request ("can you")', "mentions you"])
    assert got["Deployed?"] == ("fyi", ["automated sender"])


def test_slack_mentions_bots_and_dm_format(tmp_path):
    slack = [
        {"user": "U0SAM", "user_name": "sam", "text": "<@U0ME> can you look?", "ts": "1792134000.000100"},
        {"bot_id": "B1", "subtype": "bot_message", "text": "Build passed", "ts": "1792135800.000200"},
        {"user": "U0KAI", "user_name": "kai", "text": "lunch?", "ts": "1792136000.000300"},
    ]
    rc, rep, _ = sweep(tmp_path, {"slack": slack})
    assert rc == 1
    assert {i["excerpt"]: i["label"] for i in rep["items"]} == {
        "<@U0ME> can you look?": "reply",
        "Build passed": "fyi",
        "lunch?": "fyi",
    }
    write_files(tmp_path, {"dm.json": json.dumps(slack[2:])})
    rc, out, _ = run_main(mod, ["--source", f"dm={tmp_path / 'dm.json'}:slack-dm", *ME, "--as-of", AS_OF, "--json"])
    rep = json.loads(out)
    assert rep["sources"][0]["format"] == "slack-dm"
    assert rep["items"][0]["label"] == "reply" and "direct chat" in rep["items"][0]["why"]


def test_csv_source_column_and_excerpt_limit(tmp_path):
    long_text = "Please " + "x" * 300
    csv_text = (
        "source,from,to,cc,subject,text,time,thread,link\n"
        ",Morgan Bell <m.bell@example.org>,alex@example.org,,Form,Please return it by tomorrow.,"
        "2026-10-16T06:00:00Z,t1,https://forms.example.org/1\n"
        f"school,office@example.org,alex@example.org;other@example.org,,,{long_text},2026-10-16T07:00:00Z,,\n"
    )
    rc, rep, _ = sweep(tmp_path, {"other": csv_text})
    assert rc == 1
    form = next(i for i in rep["items"] if i["subject"] == "Form")
    assert (form["label"], form["deadline"], form["sources"], form["from"]) == ("do", "2026-10-17", ["other"],
                                                                                "Morgan Bell")
    long_item = next(i for i in rep["items"] if i["subject"] == "")
    assert long_item["sources"] == ["school"] and len(long_item["excerpt"]) == 120


def test_redact_tokenises_people_but_not_vips_or_me(tmp_path):
    msgs = [
        mail(1, "Hello", "Can you call Dana Cole or dana@example.net?", "2026-10-16T08:00:00Z",
             sender=("Dana Cole", "dana@example.net")),
        mail(2, "Budget", "Please approve.", "2026-10-16T07:00:00Z", sender=("Priya Shah", "priya@example.com")),
    ]
    rc, rep, _ = sweep(tmp_path, {"work": msgs}, "--redact", "--vip", "priya@example.com")
    assert rc == 1 and rep["redacted"] is True
    text = json.dumps(rep["items"])
    assert "Dana" not in text and "dana@example.net" not in text
    assert "Priya Shah" in text
    hello = next(i for i in rep["items"] if i["subject"] == "Hello")
    assert hello["from"] == "P1" and hello["excerpt"] == "Can you call P2 or P1?"


def test_window_excludes_old_and_future_messages(tmp_path):
    msgs = [
        mail(1, "Old", "Can you check?", "2026-10-10T09:00:00Z"),
        mail(2, "Future", "Can you check?", "2026-10-17T09:00:00Z"),
        mail(3, "Now", "Can you check?", "2026-10-16T08:59:00Z"),
    ]
    rc, rep, _ = sweep(tmp_path, {"work": msgs})
    assert rc == 1 and [i["subject"] for i in rep["items"]] == ["Now"]
    assert rep["counts"]["outside_window"] == 2
    _, rep, _ = sweep(tmp_path, {"work": msgs}, "--since-hours", "200")
    assert [i["subject"] for i in rep["items"]] == ["Old", "Now"]


def test_bad_input_exits_2_with_source_and_index(tmp_path):
    good = [mail(1, "A", "Can you?", "2026-10-16T08:00:00Z")]
    bad_record = [*good, {"subject": "no time", "from": {"emailAddress": {"address": "a@example.com"}},
                          "toRecipients": []}]
    cases = {
        "broken.json": ('{"value": [', "not valid JSON"),
        "empty.json": ("", "file is empty"),
        "unknown.json": ('[{"hello": 1}]', "unknown format"),
        "wrongtype.json": (json.dumps({"value": {"receivedDateTime": "x"}}), "unknown format"),
        "missing.json": (json.dumps(bad_record), "work item 1: missing receivedDateTime"),
        "badtime.json": (json.dumps([mail(1, "A", "b", "yesterday")]), "is not ISO 8601"),
    }
    for fname, (text, needle) in cases.items():
        write_files(tmp_path, {fname: text})
        rc, out, err = run_main(mod, ["--source", f"work={tmp_path / fname}", *ME, "--as-of", AS_OF])
        assert rc == 2, fname
        assert needle in err, (fname, err)
        assert out == ""
    rc, _, err = run_main(mod, ["--source", f"work={tmp_path / 'nope.json'}", *ME])
    assert rc == 2 and "file not found" in err
    rc, _, err = run_main(mod, ["--source", "no-equals-sign", *ME])
    assert rc == 2 and "NAME=PATH" in err
    write_files(tmp_path, {"ok.json": json.dumps(good)})
    rc, _, err = run_main(mod, ["--source", f"a={tmp_path / 'ok.json'}", "--source", f"a={tmp_path / 'ok.json'}", *ME])
    assert rc == 2 and "used twice" in err
    rc, _, err = run_main(mod, ["--source", f"a={tmp_path / 'ok.json'}", *ME, "--as-of", "soon"])
    assert rc == 2 and "--as-of" in err
    rc, _, err = run_main(mod, ["--source", f"a={tmp_path / 'ok.json'}"])
    assert rc == 2 and "--me" in err


def test_lenient_skips_bad_records_and_reports_the_count(tmp_path):
    msgs = [mail(1, "A", "Can you?", "2026-10-16T08:00:00Z"), {"id": "x", "receivedDateTime": AS_OF}, "junk"]
    rc, rep, _ = sweep(tmp_path, {"work": msgs}, "--lenient")
    assert rc == 1
    assert rep["counts"]["skipped"] == 2 and [i["subject"] for i in rep["items"]] == ["A"]
    rc, out, _ = run_main(
        mod, ["--source", f"work={tmp_path / 'work.json'}", *ME, "--as-of", AS_OF, "--lenient"]
    )
    assert rc == 1 and "2 record(s) skipped as malformed (--lenient)." in out


def test_empty_list_gives_an_empty_report(tmp_path):
    rc, rep, _ = sweep(tmp_path, {"work": {"value": []}})
    assert rc == 0 and rep["items"] == [] and rep["sources"][0]["format"] == "empty"


def test_golden_markdown_from_the_shipped_examples(tmp_path):
    args = [
        "--source", f"work-mail={EXAMPLES / 'work-mail.json'}",
        "--source", f"home-mail={EXAMPLES / 'home-mail.json'}",
        "--source", f"teams={EXAMPLES / 'teams.json'}",
        "--source", f"slack={EXAMPLES / 'slack.json'}",
        "--source", f"other={EXAMPLES / 'other.csv'}",
        "--me", "alex.morgan@example.com", "--me", "alex@example.org", "--me", "Alex Morgan",
        "--me", "U0ME", "--me", "u-alex", "--vip", "priya.shah@example.com", "--as-of", AS_OF,
    ]  # fmt: skip
    rc, out, err = run_main(mod, args)
    assert rc == 1, err
    assert out == (EXAMPLES / "expected.md").read_text(encoding="utf-8")
    out_file = tmp_path / "sweep.md"
    assert run_main(mod, [*args, "--out", str(out_file)])[0] == 1
    assert out_file.read_text(encoding="utf-8") == out
    rc, out, _ = run_main(mod, [*args, "--format", "json"])
    assert rc == 1 and json.loads(out)["counts"]["label_decide"] == 2


def test_help_exits_0():
    rc, out, _ = run_main(mod, ["--help"])
    assert rc == 0 and out.startswith("usage: next_actions.py") and "Exit codes" in out


def test_exit_code_follows_the_pack_convention(tmp_path):
    """0 nothing needs you, 1 an item needs you, --exit-zero for scheduled runs, 2 on an unwritable --out."""
    fyi = [mail(1, "Status", "All green today.", "2026-10-16T08:00:00Z"),
           mail(2, "Report", "Please read", "2026-10-16T07:00:00Z", sender=("Reports", "no-reply@example.com"))]
    rc, rep, _ = sweep(tmp_path, {"work": fyi})
    assert rc == 0 and rep["items"] and {i["label"] for i in rep["items"]} == {"fyi"}
    ask = [*fyi, mail(3, "Budget", "Can you approve the budget?", "2026-10-16T08:30:00Z")]
    rc, rep, _ = sweep(tmp_path, {"work": ask})
    assert rc == 1 and rep["counts"]["label_decide"] == 1
    rc, rep, _ = sweep(tmp_path, {"work": ask}, "--exit-zero")
    assert rc == 0 and rep["counts"]["label_decide"] == 1
    rc, _, err = run_main(mod, ["--source", f"work={tmp_path / 'work.json'}", *ME, "--as-of", AS_OF,
                                "--out", str(tmp_path / "missing" / "sweep.md")])
    assert rc == 2 and "cannot write" in err
