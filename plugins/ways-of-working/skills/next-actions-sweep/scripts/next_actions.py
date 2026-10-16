#!/usr/bin/env python3
"""next_actions.py: one ranked "do next" list from saved mail and chat exports across several accounts.

Inputs (repeat --source NAME=PATH, or NAME=PATH:FORMAT to name the format instead of detecting it):
  * graph-mail: Microsoft Graph messages JSON (an object with a "value" list, or a bare list) as saved from
    GET /me/messages. Required per message: receivedDateTime, from.emailAddress (address or name). Read when
    present: id, subject, toRecipients, ccRecipients, isRead, importance, flag.flagStatus, bodyPreview,
    conversationId, webLink.
  * graph-teams: Microsoft Graph chatMessage JSON ("value" list) as saved from GET /chats/{id}/messages or a channel.
    Required: createdDateTime, from.user (displayName or id) or from.application. Read: id, body.content (HTML tags
    stripped), mentions[].mentioned.user, chatId, channelIdentity, replyToId, webUrl, messageType.
  * slack, slack-dm: a Slack export file (a list of messages). Required: ts, and user, user_name or bot_id. Read:
    text, thread_ts, permalink, subtype. slack-dm marks every message as sent to you directly.
  * csv: columns source, from, to, cc, subject, text, time, thread, link (to and cc separated by ";"). Required:
    from, time. A non-empty source cell replaces NAME for that row.
Detection: .csv or text that is not JSON is csv; JSON records with receivedDateTime or toRecipients are graph-mail,
with createdDateTime and body are graph-teams, with ts are slack. --me (repeatable) lists the user's addresses,
display names and chat ids across every account; --vip lists senders to weight.

Rules (stable, in this order; full table in SKILL.md):
  * window: messages older than --since-hours (default 72) before --as-of, or later than --as-of, are not read.
  * threads: messages are grouped by conversationId, Teams chatId or channel thread, Slack thread_ts (or ts) per
    source, or the CSV thread cell; only the latest message of a thread is ranked.
  * answered: when the latest message is from the user the thread is dropped, unless it contains "?" or a request
    phrase and is at least --waiting-days (default 2) old: then it goes to "Waiting on others".
  * duplicates: two ranked items with the same sender and the same subject (after Re:/Fw:) within one hour, or the
    same conversationId, are merged and every source is listed.
  * addressed: the user is in To, is mentioned (Teams mention, Slack <@id>, or "@name" in the text), or the message
    is a Teams chat message, a slack-dm message, or a CSV row with the user in "to".
  * fyi: automated sender (local part noreply, no-reply, donotreply, do-not-reply, notification(s), mailer-daemon,
    postmaster, bounce(s); a Teams application; a Slack bot), the user only in Cc, not addressed, or none of below.
  * decide: addressed and an approval phrase: approve, approval, sign off, sign-off, go ahead, your call, decision,
    which option.
  * do: addressed and an explicit deadline.
  * reply: addressed and "?" or a request phrase: can you, could you, please, would you, let me know.
  * deadline: only explicit forms, never guessed: an ISO date (YYYY-MM-DD) on or after the message date; "by",
    "before", "due" or "until" followed by a weekday, "today", "tonight" or "tomorrow"; "EOD" or "COB" (the
    message's own day). Weekdays resolve to the next such day on or after the message date. URLs are ignored.
  * score: to you +30, mention +20, VIP sender +25, deadline within 2 days of --as-of (or past) +25, later deadline
    +10, high importance +10, flagged +15, unread +5, decide +20, do +15, reply +10, plus 1 per full 12 hours of age
    up to 6. Ties: older message first, then source and id, so the output is deterministic.
Privacy: excerpts are at most 120 characters; --redact replaces e-mail addresses and names (other than --vip and
the user) with stable tokens such as P1. It reads files only: it never sends, drafts, moves or marks anything.

Output: Markdown (ranked table: rank, label, from, subject or excerpt, deadline, why, sources, link; then waiting on
others) on standard output or --out FILE; --format json (or --json) prints the computed data.

Exit codes: 0 the list was written (also when it has items), 2 bad input (file missing or unreadable, unknown
format, malformed JSON, missing required fields reported with source and index, bad option). --lenient skips bad
records and reports how many were skipped instead of exiting 2.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import io
import json
import re
import sys
from pathlib import Path

FORMATS = ("graph-mail", "graph-teams", "slack", "slack-dm", "csv")
LABEL_ORDER = ("decide", "do", "reply", "fyi")
EXCERPT_MAX = 120
WEIGHTS = {
    "to-you": 30,
    "mention": 20,
    "vip": 25,
    "deadline-soon": 25,
    "deadline-later": 10,
    "high-importance": 10,
    "flagged": 15,
    "unread": 5,
    "decide": 20,
    "do": 15,
    "reply": 10,
    "fyi": 0,
}
AGE_STEP_HOURS, AGE_MAX = 12, 6
SOON_DAYS = 2
AUTOMATED_RE = re.compile(
    r"^(?:no-?reply|do-?not-?reply|notifications?|mailer-daemon|postmaster|bounces?)(?:[+._-].*)?@", re.IGNORECASE
)
REQUEST_RE = re.compile(r"\b(can you|could you|please|would you|let me know)\b", re.IGNORECASE)
APPROVAL_RE = re.compile(
    r"\b(approve|approval|sign[ -]off|go ahead|your call|decision|which option)\b", re.IGNORECASE
)
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
DEADLINE_WORD_RE = re.compile(
    r"\b(?:by|before|due|until)\s+(?:(?:this|next)\s+)?(" + "|".join(WEEKDAYS) + r"|today|tonight|tomorrow)\b",
    re.IGNORECASE,
)
EOD_RE = re.compile(r"\b(?:EOD|COB)\b")
ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
SUBJECT_PREFIX_RE = re.compile(r"^\s*(?:(?:re|fw|fwd|aw|sv)\s*:\s*)+", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")


class InputError(Exception):
    """Bad input: the message is printed and the script exits 2."""


class RecordError(Exception):
    """One bad record: exit 2, or skipped and counted with --lenient."""


# ---------------------------------------------------------------- helpers


def parse_time(value: object) -> dt.datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)  # Graph can give 7 fractional digits
    text = re.sub(r"\.(\d{1,5})(?=[+-]|$)", lambda m: "." + m.group(1).ljust(6, "0"), text)
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            parsed = dt.datetime.combine(dt.date.fromisoformat(text), dt.time())
        else:
            parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%MZ")


def clean(text: object) -> str:
    return re.sub(r"\s+", " ", "" if text is None else str(text)).strip()


def excerpt(text: str) -> str:
    text = clean(text)
    return text if len(text) <= EXCERPT_MAX else text[: EXCERPT_MAX - 3].rstrip() + "..."


def strip_html(text: str) -> str:
    return clean(html.unescape(TAG_RE.sub(" ", text or "")))


def norm_subject(subject: str) -> str:
    return clean(SUBJECT_PREFIX_RE.sub("", subject or "")).lower()


def get(obj: object, *path: str) -> object:
    for part in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def person(address: object, name: object) -> dict:
    return {"address": clean(address).lower(), "name": clean(name)}


def recipients(value: object) -> list[dict]:
    out = []
    for r in value if isinstance(value, list) else []:
        p = person(get(r, "emailAddress", "address"), get(r, "emailAddress", "name"))
        if p["address"] or p["name"]:
            out.append(p)
    return out


# ---------------------------------------------------------------- readers


def message(source: str, where: str, **fields) -> dict:
    base = {
        "source": source,
        "where": where,
        "id": "",
        "thread": "",
        "sender": person("", ""),
        "to": [],
        "cc": [],
        "mentions": [],
        "direct": False,
        "automated": False,
        "subject": "",
        "text": "",
        "time": None,
        "unread": None,
        "importance": "",
        "flagged": False,
        "link": "",
        "kind": "",
    }
    base.update(fields)
    return base


def read_graph_mail(records: list, source: str) -> tuple[list[dict], list[str]]:
    out, bad = [], []
    for i, rec in enumerate(records):
        where = f"{source} item {i}"
        try:
            if not isinstance(rec, dict):
                raise RecordError(f"{where}: not an object")
            time = parse_time(rec.get("receivedDateTime"))
            if rec.get("receivedDateTime") in (None, ""):
                raise RecordError(f"{where}: missing receivedDateTime")
            if time is None:
                raise RecordError(f"{where}: receivedDateTime {rec.get('receivedDateTime')!r} is not ISO 8601")
            sender = person(get(rec, "from", "emailAddress", "address"), get(rec, "from", "emailAddress", "name"))
            if not (sender["address"] or sender["name"]):
                raise RecordError(f"{where}: missing from.emailAddress")
            msg_id = clean(rec.get("id")) or f"item-{i}"
            out.append(
                message(
                    source,
                    where,
                    kind="mail",
                    id=msg_id,
                    thread="conv:" + (clean(rec.get("conversationId")) or f"{source}/{msg_id}"),
                    sender=sender,
                    to=recipients(rec.get("toRecipients")),
                    cc=recipients(rec.get("ccRecipients")),
                    automated=bool(AUTOMATED_RE.match(sender["address"])),
                    subject=clean(rec.get("subject")),
                    text=clean(rec.get("bodyPreview")),
                    time=time,
                    unread=(rec.get("isRead") is False),
                    importance=clean(rec.get("importance")).lower(),
                    flagged=clean(get(rec, "flag", "flagStatus")).lower() == "flagged",
                    link=clean(rec.get("webLink")),
                )
            )
        except RecordError as exc:
            bad.append(str(exc))
    return out, bad


def read_graph_teams(records: list, source: str) -> tuple[list[dict], list[str]]:
    out, bad = [], []
    for i, rec in enumerate(records):
        where = f"{source} item {i}"
        try:
            if not isinstance(rec, dict):
                raise RecordError(f"{where}: not an object")
            if clean(rec.get("messageType")) not in ("", "message"):
                continue  # system events (members added, call started) are not messages to act on
            if rec.get("createdDateTime") in (None, ""):
                raise RecordError(f"{where}: missing createdDateTime")
            time = parse_time(rec.get("createdDateTime"))
            if time is None:
                raise RecordError(f"{where}: createdDateTime {rec.get('createdDateTime')!r} is not ISO 8601")
            user, app = get(rec, "from", "user"), get(rec, "from", "application")
            if isinstance(user, dict) and (user.get("displayName") or user.get("id")):
                sender, automated = person(user.get("id"), user.get("displayName")), False
            elif isinstance(app, dict):
                sender, automated = person("", app.get("displayName") or app.get("id") or "application"), True
            else:
                raise RecordError(f"{where}: missing from.user")
            msg_id = clean(rec.get("id")) or f"item-{i}"
            chan = rec.get("channelIdentity") if isinstance(rec.get("channelIdentity"), dict) else None
            if chan:
                root = clean(rec.get("replyToId")) or msg_id
                thread = f"channel:{clean(chan.get('teamId'))}/{clean(chan.get('channelId'))}/{root}"
            else:
                thread = "chat:" + (clean(rec.get("chatId")) or f"{source}/{msg_id}")
            mentions = []
            for m in rec.get("mentions") or []:
                u = get(m, "mentioned", "user")
                if isinstance(u, dict):
                    mentions.append(person(u.get("id"), u.get("displayName")))
            out.append(
                message(
                    source,
                    where,
                    kind="teams",
                    id=msg_id,
                    thread=thread,
                    sender=sender,
                    mentions=mentions,
                    direct=chan is None,
                    automated=automated,
                    text=strip_html(get(rec, "body", "content") or ""),
                    time=time,
                    importance=clean(rec.get("importance")).lower(),
                    link=clean(rec.get("webUrl")),
                )
            )
        except RecordError as exc:
            bad.append(str(exc))
    return out, bad


def read_slack(records: list, source: str, dm: bool) -> tuple[list[dict], list[str]]:
    out, bad = [], []
    for i, rec in enumerate(records):
        where = f"{source} item {i}"
        try:
            if not isinstance(rec, dict):
                raise RecordError(f"{where}: not an object")
            ts = clean(rec.get("ts"))
            if not ts:
                raise RecordError(f"{where}: missing ts")
            try:
                time = dt.datetime.fromtimestamp(float(ts), tz=dt.timezone.utc)
            except (ValueError, OverflowError, OSError) as exc:
                raise RecordError(f"{where}: ts {ts!r} is not a Slack timestamp") from exc
            automated = bool(rec.get("bot_id")) or clean(rec.get("subtype")) == "bot_message"
            uid, uname = clean(rec.get("user")), clean(rec.get("user_name") or get(rec, "user_profile", "real_name"))
            if not (uid or uname or automated):
                raise RecordError(f"{where}: missing user or user_name")
            text = clean(rec.get("text"))
            mentions = [person(m, "") for m in re.findall(r"<@([A-Z0-9]+)(?:\|[^>]*)?>", text)]
            out.append(
                message(
                    source,
                    where,
                    kind="slack",
                    id=ts,
                    thread=f"slack:{source}/{clean(rec.get('thread_ts')) or ts}",
                    sender=person(uid, uname or (rec.get("username") or "bot" if automated else "")),
                    mentions=mentions,
                    direct=dm,
                    automated=automated,
                    text=text,
                    time=time,
                    link=clean(rec.get("permalink")),
                )
            )
        except RecordError as exc:
            bad.append(str(exc))
    return out, bad


def read_csv(text: str, source: str) -> tuple[list[dict], list[str]]:
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return [], []
    header = [clean(c).lower() for c in rows[0]]
    missing = [c for c in ("from", "time") if c not in header]
    if missing:
        raise InputError(f"{source}: CSV header has no {', '.join(missing)} column")
    out, bad = [], []
    for i, cells in enumerate(rows[1:]):
        where = f"{source} row {i + 1}"
        try:
            if len(cells) != len(header):
                raise RecordError(f"{where}: expected {len(header)} cells, found {len(cells)}")
            rec = dict(zip(header, (c.strip() for c in cells), strict=True))
            if not rec.get("from"):
                raise RecordError(f"{where}: missing from")
            if not rec.get("time"):
                raise RecordError(f"{where}: missing time")
            time = parse_time(rec["time"])
            if time is None:
                raise RecordError(f"{where}: time {rec['time']!r} is not ISO 8601")

            def split(value: str) -> list[dict]:
                return [as_person(p) for p in re.split(r"[;]", value or "") if p.strip()]

            sender = as_person(rec["from"])
            src = rec.get("source") or source
            msg_id = f"row-{i + 1}"
            out.append(
                message(
                    src,
                    where,
                    kind="csv",
                    id=msg_id,
                    thread="thread:" + (rec.get("thread") or f"{src}/{msg_id}"),
                    sender=sender,
                    to=split(rec.get("to", "")),
                    cc=split(rec.get("cc", "")),
                    automated=bool(AUTOMATED_RE.match(sender["address"])),
                    subject=clean(rec.get("subject")),
                    text=clean(rec.get("text")),
                    time=time,
                    link=clean(rec.get("link")),
                )
            )
        except RecordError as exc:
            bad.append(str(exc))
    return out, bad


def as_person(value: str) -> dict:
    """'Name <a@b>' or 'a@b' or 'Name'."""
    value = clean(value)
    m = re.match(r"^(.*?)\s*<([^>]+)>$", value)
    if m:
        return person(m.group(2), m.group(1).strip('"'))
    return person(value, "") if "@" in value else person("", value)


def detect(data: object) -> str | None:
    records = data.get("value") if isinstance(data, dict) else data
    if not isinstance(records, list):
        return None
    first = next((r for r in records if isinstance(r, dict)), None)
    if first is None:
        return "empty"
    if "receivedDateTime" in first or "toRecipients" in first:
        return "graph-mail"
    if "createdDateTime" in first and "body" in first:
        return "graph-teams"
    if "ts" in first:
        return "slack"
    return None


def parse_source(spec: str) -> tuple[str, str, str | None]:
    if "=" not in spec:
        raise InputError(f"--source {spec!r}: expected NAME=PATH or NAME=PATH:FORMAT")
    name, rest = spec.split("=", 1)
    name, fmt = name.strip(), None
    if ":" in rest and rest.rsplit(":", 1)[1] in FORMATS:
        rest, fmt = rest.rsplit(":", 1)
    if not name or not rest:
        raise InputError(f"--source {spec!r}: NAME and PATH must not be empty")
    return name, rest, fmt


def load_source(name: str, path_text: str, fmt: str | None) -> tuple[str, list[dict], list[str]]:
    path = Path(path_text)
    if not path.is_file():
        raise InputError(f"{name}: {path_text}: file not found")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(f"{name}: {path_text}: cannot read ({exc})") from exc
    if not text.strip():
        raise InputError(f"{name}: {path_text}: file is empty")
    looks_json = text.lstrip()[:1] in "[{"
    if fmt is None:
        fmt = "csv" if path.suffix.lower() == ".csv" or not looks_json else None
    if fmt == "csv":
        found, bad = read_csv(text, name)
        return fmt, found, bad
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(f"{name}: {path_text}: not valid JSON ({exc.msg} at line {exc.lineno})") from exc
    if fmt is None:
        fmt = detect(data)
        if fmt is None:
            raise InputError(
                f"{name}: {path_text}: unknown format; give it as {name}=PATH:FORMAT ({', '.join(FORMATS)})"
            )
        if fmt == "empty":
            return fmt, [], []
    records = data.get("value") if isinstance(data, dict) else data
    if not isinstance(records, list):
        raise InputError(f"{name}: {path_text}: expected a JSON list or an object with a \"value\" list")
    if fmt == "graph-mail":
        found, bad = read_graph_mail(records, name)
    elif fmt == "graph-teams":
        found, bad = read_graph_teams(records, name)
    else:
        found, bad = read_slack(records, name, fmt == "slack-dm")
    return fmt, found, bad


# ---------------------------------------------------------------- rules


class Me:
    def __init__(self, me: list[str], vip: list[str]):
        self.ids = {clean(m).lower() for m in me if clean(m)}
        self.vip = {clean(v).lower() for v in vip if clean(v)}

    def is_me(self, p: dict) -> bool:
        return bool({p["address"], p["name"].lower()} & self.ids - {""})

    def is_vip(self, p: dict) -> bool:
        return bool({p["address"], p["name"].lower()} & self.vip - {""})

    def named_in(self, text: str) -> bool:
        low = text.lower()
        return any(f"@{i}" in low for i in self.ids if "@" not in i)


def find_deadline(text: str, sent: dt.datetime) -> tuple[dt.date, str] | None:
    """The earliest explicit deadline in the text, resolved against the message date."""
    text = URL_RE.sub(" ", text)
    day = sent.date()
    found: list[tuple[dt.date, str]] = []
    for m in ISO_DATE_RE.finditer(text):
        try:
            d = dt.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if d >= day:
            found.append((d, m.group(1)))
    for m in DEADLINE_WORD_RE.finditer(text):
        word = m.group(1).lower()
        if word in ("today", "tonight"):
            d = day
        elif word == "tomorrow":
            d = day + dt.timedelta(days=1)
        else:
            d = day + dt.timedelta(days=(WEEKDAYS.index(word) - day.weekday()) % 7)
        found.append((d, clean(m.group(0))))
    for m in EOD_RE.finditer(text):
        found.append((day, m.group(0)))
    return min(found) if found else None


def classify(msg: dict, me: Me, as_of: dt.datetime) -> dict:
    body = URL_RE.sub(" ", f"{msg['subject']} {msg['text']}")
    to_me = any(me.is_me(p) for p in msg["to"])
    cc_me = any(me.is_me(p) for p in msg["cc"])
    mentioned = any(me.is_me(p) for p in msg["mentions"]) or me.named_in(body)
    direct = to_me or msg["direct"]
    addressed = direct or mentioned
    deadline = find_deadline(body, msg["time"])
    approval = APPROVAL_RE.search(body)
    request = REQUEST_RE.search(body)
    question = "?" in body
    why: list[str] = []
    if msg["automated"]:
        label, why = "fyi", ["automated sender"]
    elif cc_me and not addressed:
        label, why = "fyi", ["you are only in Cc"]
    elif not addressed:
        label, why = "fyi", ["not addressed to you"]
    elif approval:
        label, why = "decide", [f'asks for a decision ("{approval.group(0).lower()}")']
    elif deadline:
        label, why = "do", [f'deadline ("{deadline[1]}")']
    elif request or question:
        label, why = "reply", [f'request ("{request.group(0).lower()}")' if request else "asks a question"]
    else:
        label, why = "fyi", ["no question, request or deadline"]
    score = WEIGHTS[label]
    if direct and not msg["automated"]:
        score += WEIGHTS["to-you"]
        why.append("sent to you" if to_me else "direct chat")
    if mentioned and not msg["automated"]:
        score += WEIGHTS["mention"]
        why.append("mentions you")
    if me.is_vip(msg["sender"]):
        score += WEIGHTS["vip"]
        why.append("VIP sender")
    if deadline:
        days = (deadline[0] - as_of.date()).days
        if days <= SOON_DAYS:
            score += WEIGHTS["deadline-soon"]
            why.append("deadline passed" if days < 0 else f"due within {SOON_DAYS} days")
        else:
            score += WEIGHTS["deadline-later"]
            why.append(f"due in {days} days")
    if msg["importance"] == "high":
        score += WEIGHTS["high-importance"]
        why.append("high importance")
    if msg["flagged"]:
        score += WEIGHTS["flagged"]
        why.append("flagged")
    if msg["unread"]:
        score += WEIGHTS["unread"]
        why.append("unread")
    age_hours = int((as_of - msg["time"]).total_seconds() // 3600)
    score += min(AGE_MAX, age_hours // AGE_STEP_HOURS)
    return {
        "label": label,
        "score": score,
        "why": why,
        "deadline": deadline[0].isoformat() if deadline else "",
        "age_hours": age_hours,
        "asks": bool(request or question),
    }


def same_person(a: dict, b: dict) -> bool:
    if a["address"] and b["address"]:
        return a["address"] == b["address"]
    return bool(a["name"]) and a["name"].lower() == b["name"].lower()


def sweep(messages: list[dict], me: Me, as_of: dt.datetime, since_hours: float, waiting_days: float) -> dict:
    start = as_of - dt.timedelta(hours=since_hours)
    in_window = [m for m in messages if start <= m["time"] <= as_of]
    threads: dict[str, list[dict]] = {}
    for m in in_window:
        threads.setdefault(m["thread"], []).append(m)
    items, waiting, answered = [], [], 0
    for key in sorted(threads):
        group = sorted(threads[key], key=lambda m: (m["time"], m["source"], m["id"]))
        latest = group[-1]
        sources = sorted({m["source"] for m in group})
        if me.is_me(latest["sender"]):
            asks = bool(REQUEST_RE.search(URL_RE.sub(" ", latest["text"] + " " + latest["subject"]))) or (
                "?" in URL_RE.sub(" ", latest["text"] + " " + latest["subject"])
            )
            if asks and as_of - latest["time"] >= dt.timedelta(days=waiting_days):
                waiting.append({"msg": latest, "sources": sources})
            else:
                answered += 1
            continue
        items.append({"msg": latest, "sources": sources, **classify(latest, me, as_of)})
    # merge duplicates across accounts: same sender, same subject, within an hour
    items.sort(key=lambda it: (it["msg"]["time"], it["msg"]["source"], it["msg"]["id"]))
    merged: list[dict] = []
    duplicates = 0
    for it in items:
        m = it["msg"]
        twin = next(
            (
                o
                for o in merged
                if m["subject"]
                and same_person(o["msg"]["sender"], m["sender"])
                and norm_subject(o["msg"]["subject"]) == norm_subject(m["subject"])
                and abs((o["msg"]["time"] - m["time"]).total_seconds()) <= 3600
            ),
            None,
        )
        if twin is None:
            merged.append(it)
            continue
        duplicates += 1
        keep, drop = (twin, it) if (twin["score"], -LABEL_ORDER.index(twin["label"])) >= (
            it["score"],
            -LABEL_ORDER.index(it["label"]),
        ) else (it, twin)
        keep["sources"] = sorted(set(keep["sources"]) | set(drop["sources"]))
        if not keep["msg"]["link"]:
            keep["msg"] = {**keep["msg"], "link": drop["msg"]["link"]}
        merged[merged.index(twin)] = keep
    merged.sort(key=lambda it: (-it["score"], it["msg"]["time"], it["msg"]["source"], it["msg"]["id"]))
    waiting.sort(key=lambda w: (w["msg"]["time"], w["msg"]["source"], w["msg"]["id"]))
    return {
        "read": len(in_window),
        "outside_window": len(messages) - len(in_window),
        "threads": len(threads),
        "answered": answered,
        "duplicates": duplicates,
        "items": merged,
        "waiting": waiting,
    }


# ---------------------------------------------------------------- output


class Redactor:
    def __init__(self, enabled: bool, me: Me, names: set[str]):
        self.enabled, self.me, self.tokens = enabled, me, {}
        self.names = sorted((n for n in names if n and n.lower() not in me.vip and n.lower() not in me.ids),
                            key=lambda n: (-len(n), n))

    def token(self, key: str) -> str:
        key = key.lower()
        if key not in self.tokens:
            self.tokens[key] = f"P{len(self.tokens) + 1}"
        return self.tokens[key]

    def who(self, p: dict) -> str:
        shown = p["name"] or p["address"] or "unknown"
        if not self.enabled:
            return shown
        if self.me.is_me(p):
            return "me"
        if self.me.is_vip(p):
            return shown
        return self.token(p["address"] or p["name"])

    def text(self, value: str) -> str:
        if not self.enabled:
            return value
        value = EMAIL_RE.sub(lambda m: m.group(0) if m.group(0).lower() in self.me.vip else self.token(m.group(0)),
                             value)
        for n in self.names:
            value = re.sub(r"\b" + re.escape(n) + r"\b", lambda _m, n=n: self.token(n), value, flags=re.IGNORECASE)
        return value


def report(rep: dict, sources: list[dict], args: argparse.Namespace, as_of: dt.datetime, red: Redactor) -> dict:
    items = []
    for rank, it in enumerate(rep["items"][: args.top], start=1):
        m = it["msg"]
        items.append(
            {
                "rank": rank,
                "label": it["label"],
                "score": it["score"],
                "from": red.who(m["sender"]),
                "subject": red.text(excerpt(m["subject"])),
                "excerpt": red.text(excerpt(m["text"])),
                "deadline": it["deadline"],
                "why": it["why"],
                "time": iso(m["time"]),
                "age_hours": it["age_hours"],
                "sources": it["sources"],
                "link": m["link"],
                "kind": m["kind"],
            }
        )
    waiting = []
    for w in rep["waiting"]:
        m = w["msg"]
        to = m["to"] or m["mentions"]
        waiting.append(
            {
                "to": [red.who(p) for p in to],
                "subject": red.text(excerpt(m["subject"])),
                "excerpt": red.text(excerpt(m["text"])),
                "sent": iso(m["time"]),
                "days_waiting": round((as_of - m["time"]).total_seconds() / 86400, 1),
                "sources": w["sources"],
                "link": m["link"],
                "kind": m["kind"],
            }
        )
    labels = {lab: sum(1 for it in rep["items"] if it["label"] == lab) for lab in LABEL_ORDER}
    return {
        "as_of": iso(as_of),
        "since_hours": args.since_hours,
        "waiting_days": args.waiting_days,
        "sources": sources,
        "counts": {
            "messages_read": rep["read"],
            "outside_window": rep["outside_window"],
            "threads": rep["threads"],
            "answered": rep["answered"],
            "duplicates_merged": rep["duplicates"],
            "ranked": len(rep["items"]),
            "shown": len(items),
            "waiting": len(waiting),
            "skipped": sum(s["skipped"] for s in sources),
            **{f"label_{k}": v for k, v in labels.items()},
        },
        "items": items,
        "waiting": waiting,
        "redacted": args.redact,
    }


def cell(value: str) -> str:
    return (value or "").replace("|", "\\|").replace("\n", " ") or "-"


def render(data: dict) -> str:
    c = data["counts"]
    names = ", ".join(f"{s['name']} ({s['format']}, {s['messages']})" for s in data["sources"])
    lines = [
        f"# Next actions: {data['as_of']}",
        "",
        f"{c['messages_read']} message(s) in the last {data['since_hours']:g} hours from {names}; "
        f"{c['threads']} thread(s), {c['answered']} already answered by you, {c['duplicates_merged']} duplicate(s) "
        f"merged across accounts. Labels: {c['label_decide']} decide, {c['label_do']} do, {c['label_reply']} reply, "
        f"{c['label_fyi']} FYI. Labels and scores come from fixed rules; check each before acting.",
    ]
    if c["skipped"]:
        lines.append(f"{c['skipped']} record(s) skipped as malformed (--lenient).")
    if data["redacted"]:
        lines.append("Names and addresses are replaced by tokens (--redact).")
    lines += ["", f"## Do next ({c['shown']} of {c['ranked']})", ""]
    if data["items"]:
        lines += [
            "| Rank | Label | From | Subject or excerpt | Deadline | Why | Sources | Link |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for it in data["items"]:
            what = it["subject"] or it["excerpt"]
            link = f"[open]({it['link']})" if it["link"] else "-"
            lines.append(
                f"| {it['rank']} | {it['label'].upper() if it['label'] == 'fyi' else it['label']} | {cell(it['from'])} "
                f"| {cell(what)} | {cell(it['deadline'])} | {cell('; '.join(it['why']))} (score {it['score']}) "
                f"| {cell(', '.join(it['sources']))} | {link} |"
            )
    else:
        lines.append("Nothing to act on in this window.")
    lines += ["", f"## Waiting on others ({c['waiting']})", ""]
    if data["waiting"]:
        lines += ["| Sent | To | Subject or excerpt | Days waiting | Sources | Link |", "|---|---|---|---|---|---|"]
        for w in data["waiting"]:
            what = w["subject"] or w["excerpt"]
            link = f"[open]({w['link']})" if w["link"] else "-"
            lines.append(
                f"| {w['sent']} | {cell(', '.join(w['to']))} | {cell(what)} | {w['days_waiting']:g} "
                f"| {cell(', '.join(w['sources']))} | {link} |"
            )
    else:
        lines.append(f"No question of yours has gone unanswered for {data['waiting_days']:g} day(s) or more.")
    lines += ["", "Nothing was sent, drafted, moved or marked as read. Reply from your own mail or chat client."]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="next_actions.py",
        description="Rank what to do next from saved mail and chat exports across several accounts: reply, "
        "decide, do or FYI, with the reason, any stated deadline, the link back and a waiting-on-others list. "
        "Reads files only; never sends or changes anything.",
        epilog="Formats: graph-mail, graph-teams, slack, slack-dm, csv (detected unless given as NAME=PATH:FORMAT). "
        "Exit codes: 0 the list was written (also when it has items), 2 bad input.",
    )
    p.add_argument("--source", action="append", required=True, metavar="NAME=PATH[:FORMAT]",
                   help="an export to read, named for the account (repeatable)")
    p.add_argument("--me", action="append", required=True,
                   help="your address, display name or chat id in any account (repeatable)")
    p.add_argument("--vip", action="append", default=[], help="a sender address or name to weight (repeatable)")
    p.add_argument("--as-of", default=None, help="sweep time, ISO 8601; give it for reproducible output (default now)")
    p.add_argument("--since-hours", type=float, default=72, help="read messages from this many hours back (default 72)")
    p.add_argument("--waiting-days", type=float, default=2,
                   help="days your unanswered question waits before it is listed (default 2)")
    p.add_argument("--top", type=int, default=25, help="items shown in the ranked list (default 25)")
    p.add_argument("--redact", action="store_true", help="replace addresses and names other than --vip with tokens")
    p.add_argument("--lenient", action="store_true", help="skip malformed records and report how many")
    p.add_argument("--format", choices=("markdown", "json"), default="markdown", help="output format")
    p.add_argument("--json", action="store_true", help="same as --format json")
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
        if args.since_hours <= 0 or args.waiting_days < 0 or args.top < 1:
            raise InputError("--since-hours must be above 0, --waiting-days 0 or more and --top 1 or more")
        me = Me(args.me, args.vip)
        if not me.ids:
            raise InputError("--me must name at least one address or name")
        messages, sources, seen = [], [], set()
        for spec in args.source:
            name, path, fmt = parse_source(spec)
            if name in seen:
                raise InputError(f"--source name {name!r} is used twice")
            seen.add(name)
            fmt, found, bad = load_source(name, path, fmt)
            if bad and not args.lenient:
                more = f" (and {len(bad) - 1} more)" if len(bad) > 1 else ""
                raise InputError(f"{bad[0]}{more}; fix the export or pass --lenient to skip bad records")
            messages += found
            sources.append({"name": name, "format": fmt, "messages": len(found), "skipped": len(bad)})
    except InputError as exc:
        print(f"next_actions.py: {exc}", file=sys.stderr)
        return 2
    rep = sweep(messages, me, as_of, args.since_hours, args.waiting_days)
    names: set[str] = set()
    for m in messages:
        for p in [m["sender"], *m["to"], *m["cc"], *m["mentions"]]:
            if p["name"] and not me.is_me(p):
                names.add(p["name"])
    data = report(rep, sources, args, as_of, Redactor(args.redact, me, names))
    as_json = args.json or args.format == "json"
    text = json.dumps(data, indent=2, sort_keys=True) + "\n" if as_json else render(data)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
