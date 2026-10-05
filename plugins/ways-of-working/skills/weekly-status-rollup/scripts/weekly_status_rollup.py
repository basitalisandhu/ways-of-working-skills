#!/usr/bin/env python3
"""One lead's weekly status note (or a daily standup note) from saved exports. Reads files only; no network.

Inputs (export commands are in SKILL.md):
  --git-log [NAME=]FILE  git log --date=short --pretty=format:'%h%x09%ad%x09%an%x09%s' saved per repository; the
                         repository name is NAME, else the file name without its extension
  --prs FILE             gh pr list --state all --json number,title,url,state,isDraft,createdAt,mergedAt,closedAt,
                         updatedAt,labels,author
  --issues FILE          gh issue list --state all --json number,title,url,state,stateReason,createdAt,closedAt,
                         updatedAt,labels,assignees
  --last-week FILE       last week's note (this script's output, or any Markdown that cites items as repo#N or by
                         URL, or repeats their titles as bullets)
Every option except --last-week can be repeated.

Groups, each item once, in this order of precedence:
  blocked         open PRs or issues carrying a --blocked-label (default "blocked")
  carried over    open items that last week's note also listed; the count of weeks carries forward from a
                  "carried N weeks" marker; items carried --carry-flag weeks or more are flagged
  in review       open PRs that are not drafts
  in progress     open draft PRs, and open issues updated inside the window
  shipped         PRs merged and issues closed as completed inside the window, plus commits that do not reference
                  a shipped PR
"Decisions needed" and "Risks" are left for the author to fill; text under those headings in last week's note is
copied as "from last week", never invented. --daily gives a standup-sized note for one day.

Exit codes: 0 nothing flagged, 1 a blocked item or an item carried --carry-flag weeks or more, 2 bad input.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

URL_RE = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/(?:pull|issues)/(\d+)", re.IGNORECASE)
SHORT_REF_RE = re.compile(r"(?<![\w/])([A-Za-z0-9][\w.-]*)#(\d+)\b")
PR_REF_RE = re.compile(r"(?:\(#(\d+)\)|Merge pull request #(\d+))")
CARRIED_RE = re.compile(r"carried (\d+) weeks?", re.IGNORECASE)
HEADING_RE = re.compile(r"^#{2,3}\s+(.+?)\s*$")
BULLET_RE = re.compile(r"^[-*]\s+(?:\[[ xX]?\]\s*)?")
TO_FILL = ("Decisions needed", "Risks")


class InputError(Exception):
    """Bad input: exit code 2."""


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


def day_of(stamp: str | None) -> date | None:
    if not stamp:
        return None
    try:
        return date.fromisoformat(stamp[:10])
    except ValueError:
        return None


def load_json_list(path: Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise InputError(f"file not found: {path}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise InputError(f"{path}: invalid JSON ({exc.msg} at line {exc.lineno})") from exc
    if not isinstance(data, list):
        raise InputError(f"{path}: expected a JSON list as written by gh ... --json")
    if data and all(isinstance(page, list) for page in data):  # gh api --slurp pages
        data = [item for page in data for item in page]
    return data


def read_commits(spec: str, warnings: list[str]) -> tuple[str, list[dict]]:
    name, sep, file = spec.partition("=")
    path = Path(file if sep else spec)
    repo = name if sep else path.stem
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError as exc:
        raise InputError(f"file not found: {path}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(f"cannot read {path}: {exc}") from exc
    commits = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        fields = line.split("\t")
        when = day_of(fields[1]) if len(fields) == 4 else None
        if len(fields) != 4 or when is None:
            warnings.append(
                f"{path.name}:{number}: expected 4 tab-separated fields (hash, YYYY-MM-DD date, author, subject); "
                "line skipped"
            )
            continue
        commits.append(
            {"repo": repo, "hash": fields[0], "date": when, "author": fields[2], "subject": fields[3].strip()}
        )
    return repo, commits


def to_item(raw: dict, kind: str, source: str, warnings: list[str], index: int) -> dict | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("number"), int):
        warnings.append(f"{source}: entry {index} has no number; skipped")
        return None
    url = raw.get("url") or ""
    m = URL_RE.search(url)
    repo = m.group(2) if m else "?"
    if not m:
        warnings.append(f"{source}: {kind} #{raw['number']} has no GitHub url, so its repository is unknown")
    labels = [lab.get("name", "") for lab in raw.get("labels") or [] if isinstance(lab, dict)]
    people = [raw.get("author") or {}] if kind == "pr" else raw.get("assignees") or []
    return {
        "kind": kind,
        "repo": repo,
        "number": raw["number"],
        "key": f"{repo.lower()}#{raw['number']}",
        "ref": f"{repo}#{raw['number']}",
        "title": (raw.get("title") or "").strip(),
        "url": url,
        "state": (raw.get("state") or "").upper(),
        "state_reason": (raw.get("stateReason") or "").upper(),
        "draft": bool(raw.get("isDraft")),
        "merged": day_of(raw.get("mergedAt")),
        "closed": day_of(raw.get("closedAt")),
        "updated": day_of(raw.get("updatedAt")),
        "labels": labels,
        "logins": [p.get("login", "") for p in people if isinstance(p, dict)],
    }


def read_last_week(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise InputError(f"file not found: {path}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(f"cannot read {path}: {exc}") from exc
    refs: dict[str, dict] = {}
    bullets: dict[str, int] = {}
    carried_forward: dict[str, list[str]] = {name: [] for name in TO_FILL}
    section = ""
    for line in text.splitlines():
        heading = HEADING_RE.match(line.strip())
        if heading:
            section = heading.group(1).strip()
            continue
        weeks_m = CARRIED_RE.search(line)
        weeks = int(weeks_m.group(1)) if weeks_m else 0
        keys = {f"{m.group(2).lower()}#{m.group(3)}" for m in URL_RE.finditer(line)}
        keys |= {f"{m.group(1).lower()}#{m.group(2)}" for m in SHORT_REF_RE.finditer(line)}
        for key in keys:
            refs[key] = {"weeks": max(weeks, refs.get(key, {}).get("weeks", 0)), "line": line.strip()}
        stripped = line.strip()
        for name in TO_FILL:
            if section.lower().startswith(name.lower()) and stripped.startswith(("-", "*")):
                body = BULLET_RE.sub("", stripped).strip()
                if body and not body.startswith("_") and not body.lower().startswith("from last week"):
                    carried_forward[name].append(body)
        if stripped.startswith(("-", "*")) and not keys:
            body = re.sub(r"\(.*?\)\s*$", "", BULLET_RE.sub("", stripped)).strip()
            if norm(body):
                bullets[norm(body)] = max(weeks, bullets.get(norm(body), 0))
    return {"refs": refs, "bullets": bullets, "to_fill": carried_forward}


def analyse(args) -> dict:
    days = args.days if args.days is not None else (1 if args.daily else 7)
    if days < 1 or args.carry_flag < 1:
        raise InputError("--days and --carry-flag must be at least 1")
    try:
        as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    except ValueError as exc:
        raise InputError(f"--as-of must be YYYY-MM-DD, got {args.as_of!r}") from exc
    start = as_of - timedelta(days=days - 1)

    def in_window(day: date | None) -> bool:
        return day is not None and start <= day <= as_of

    warnings: list[str] = []
    sources: list[str] = []
    commits: list[dict] = []
    for spec in args.git_log or []:
        repo, rows = read_commits(spec, warnings)
        if args.author:
            rows = [c for c in rows if args.author.lower() in c["author"].lower()]
        commits += rows
        sources.append(f"git log {repo} ({len(rows)} commits)")
    items: list[dict] = []
    for kind, files in (("pr", args.prs or []), ("issue", args.issues or [])):
        for file in files:
            path = Path(file)
            rows = [to_item(r, kind, path.name, warnings, i) for i, r in enumerate(load_json_list(path), start=1)]
            rows = [r for r in rows if r]
            if args.login:
                rows = [r for r in rows if args.login.lower() in (x.lower() for x in r["logins"])]
            items += rows
            sources.append(f"{path.name} ({len(rows)} {'PRs' if kind == 'pr' else 'issues'})")
    seen: dict[str, dict] = {}
    for item in items:
        seen.setdefault(item["key"], item)
    items = sorted(seen.values(), key=lambda i: (i["repo"].lower(), i["number"]))

    last = read_last_week(Path(args.last_week)) if args.last_week else None
    if last is not None:
        sources.append(f"last week: {Path(args.last_week).name}")
    blocked_labels = {b.lower() for b in (args.blocked_label or ["blocked"])}

    groups: dict[str, list[dict]] = {k: [] for k in ("blocked", "carried", "in_review", "in_progress", "shipped")}
    not_planned: list[dict] = []
    for item in items:
        is_open = item["state"] == "OPEN"
        weeks = 0
        if last is not None and is_open:
            if item["key"] in last["refs"]:
                weeks = last["refs"][item["key"]]["weeks"] + 1
            elif norm(item["title"]) in last["bullets"]:
                weeks = last["bullets"][norm(item["title"])] + 1
        item["carried_weeks"] = weeks
        item["flagged_carry"] = weeks >= args.carry_flag
        hit = sorted(lab for lab in item["labels"] if lab.lower() in blocked_labels)
        if is_open and hit:
            item["blocked_by"] = hit[0]
            groups["blocked"].append(item)
        elif weeks:
            groups["carried"].append(item)
        elif is_open and item["kind"] == "pr":
            groups["in_progress" if item["draft"] else "in_review"].append(item)
        elif is_open and in_window(item["updated"]):
            groups["in_progress"].append(item)
        elif item["kind"] == "pr" and item["state"] == "MERGED" and in_window(item["merged"]):
            groups["shipped"].append(item)
        elif item["kind"] == "issue" and item["state"] == "CLOSED" and in_window(item["closed"]):
            (not_planned if item["state_reason"] == "NOT_PLANNED" else groups["shipped"]).append(item)
        if last is not None and not is_open and item["key"] in last["refs"]:
            item["in_last_week"] = True

    shipped_keys = {i["key"] for i in groups["shipped"] if i["kind"] == "pr"}
    direct = []
    for c in sorted(commits, key=lambda c: (c["repo"].lower(), c["date"], c["hash"])):
        if not in_window(c["date"]):
            continue
        nums = [a or b for a, b in PR_REF_RE.findall(c["subject"])]
        if any(f"{c['repo'].lower()}#{n}" in shipped_keys for n in nums):
            continue
        direct.append(c)

    missing = []
    if last is not None:
        known = {i["key"] for i in items}
        missing = sorted(k for k in last["refs"] if k not in known)
    flagged = bool(groups["blocked"] or any(i["flagged_carry"] for i in items))
    return {
        "mode": "daily" if args.daily else "weekly",
        "as_of": as_of.isoformat(),
        "window": {"start": start.isoformat(), "end": as_of.isoformat(), "days": days},
        "sources": sources,
        "carry_flag": args.carry_flag,
        "groups": groups,
        "not_planned": not_planned,
        "direct_commits": direct,
        "not_found": missing,
        "from_last_week": last["to_fill"] if last else {name: [] for name in TO_FILL},
        "warnings": warnings,
        "flagged": flagged,
    }


def link(item: dict) -> str:
    return f"[{item['ref']}]({item['url']})" if item["url"] else item["ref"]


def describe(item: dict, rep: dict) -> str:
    notes = []
    if item["kind"] == "issue":
        notes.append("issue")
    if item["state"] == "MERGED" and item["merged"]:
        notes.append(f"merged {item['merged'].isoformat()}")
    elif item["state"] == "CLOSED" and item["closed"]:
        notes.append(f"closed {item['closed'].isoformat()}")
    elif item.get("blocked_by"):
        notes.append(f"label {item['blocked_by']}")
    elif item["kind"] == "pr" and item["state"] == "OPEN":
        notes.append("draft" if item["draft"] else "in review")
    if item.get("carried_weeks"):
        weeks = item["carried_weeks"]
        notes.append(f"carried {weeks} week{'s' if weeks != 1 else ''}")
        if item["flagged_carry"]:
            notes.append("flagged")
    if item.get("in_last_week"):
        notes.append("was in last week's note")
    return f"- {link(item)} {item['title']} ({', '.join(notes)})"


def section(title: str, lines: list[str]) -> list[str]:
    return [f"## {title}", "", *(lines or ["None."]), ""]


def render_markdown(rep: dict) -> str:
    g = rep["groups"]
    w = rep["window"]
    commit_lines = [
        f"- {c['repo']}: {c['hash']} {c['subject']} ({c['date'].isoformat()})" for c in rep["direct_commits"]
    ]
    if rep["mode"] == "daily":
        out = [
            f"# Daily update: {rep['as_of']}",
            "",
            f"Sources: {'; '.join(rep['sources']) or 'none'}.",
            "",
            *section("Done", [describe(i, rep) for i in g["shipped"]] + commit_lines),
            *section("In review", [describe(i, rep) for i in g["in_review"] + g["carried"] if i["state"] == "OPEN"]),
            *section("Blocked", [describe(i, rep) for i in g["blocked"]]),
            "## Today",
            "",
            "_To fill in._",
            "",
        ]
    else:
        out = [
            f"# Weekly status: week ending {rep['as_of']}",
            "",
            f"Window {w['start']} to {w['end']} ({w['days']} days). Sources: {'; '.join(rep['sources']) or 'none'}.",
            "",
            *section("Shipped", [describe(i, rep) for i in g["shipped"]]),
            "### Commits not linked to a shipped PR",
            "",
            *(commit_lines or ["None."]),
            "",
            *section("In review", [describe(i, rep) for i in g["in_review"]]),
            *section("In progress", [describe(i, rep) for i in g["in_progress"]]),
            *section("Blocked", [describe(i, rep) for i in g["blocked"]]),
            *section(
                f"Carried over from last week (flagged at {rep['carry_flag']} weeks)",
                [describe(i, rep) for i in g["carried"]],
            ),
        ]
        if rep["not_planned"]:
            out += section("Closed as not planned", [describe(i, rep) for i in rep["not_planned"]])
        if rep["not_found"]:
            out += section(
                "In last week's note but not in this week's exports",
                [f"- {k} (check by hand)" for k in rep["not_found"]],
            )
        for name in ("Decisions needed", "Risks"):
            out += [f"## {name}", "", "_To fill in. The exports hold no decisions or risks, so none are generated._"]
            if rep["from_last_week"][name]:
                out += ["", "From last week's note (still open?):", ""]
                out += [f"- {line}" for line in rep["from_last_week"][name]]
            out.append("")
    if rep["warnings"]:
        out += ["## Warnings", "", *[f"- {x}" for x in rep["warnings"]], ""]
    return "\n".join(out).rstrip("\n") + "\n"


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
        prog="weekly_status_rollup.py",
        description="One lead's weekly status note from saved git log and gh exports, with carry-over against last "
        "week's note (offline).",
        epilog="Exit codes: 0 nothing flagged, 1 a blocked item or an item carried --carry-flag weeks or more, "
        "2 bad input.",
    )
    parser.add_argument("--git-log", action="append", metavar="[NAME=]FILE", help="saved git log for one repository")
    parser.add_argument("--prs", action="append", metavar="FILE", help="saved gh pr list --json export")
    parser.add_argument("--issues", action="append", metavar="FILE", help="saved gh issue list --json export")
    parser.add_argument("--last-week", metavar="FILE", help="last week's note, for carry-over")
    parser.add_argument("--as-of", help="last day of the window, YYYY-MM-DD (default today)")
    parser.add_argument("--days", type=int, help="window length in days (default 7, or 1 with --daily)")
    parser.add_argument("--daily", action="store_true", help="standup-sized note: done, in review, blocked")
    parser.add_argument("--blocked-label", action="append", help='label that marks blocked work (default "blocked")')
    parser.add_argument("--carry-flag", type=int, default=2, help="weeks carried that flag an item (default 2)")
    parser.add_argument("--author", help="keep only commits whose author name contains this text")
    parser.add_argument("--login", help="keep only PRs authored by, and issues assigned to, this login")
    parser.add_argument("--json", action="store_true", help="JSON instead of Markdown")
    parser.add_argument("--output", type=Path, help="write the note to this file instead of stdout")
    args = parser.parse_args(argv)
    try:
        if not (args.git_log or args.prs or args.issues):
            raise InputError("give at least one of --git-log, --prs or --issues")
        rep = analyse(args)
        write_output(to_json(rep) if args.json else render_markdown(rep), args.output)
    except InputError as exc:
        print(f"weekly_status_rollup.py: {exc}", file=sys.stderr)
        return 2
    return 1 if rep["flagged"] else 0


if __name__ == "__main__":
    sys.exit(main())
