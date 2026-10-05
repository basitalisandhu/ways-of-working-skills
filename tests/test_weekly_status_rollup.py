"""Tests for weekly_status_rollup.py. Exports are synthetic, written under tmp_path; repositories are examples.

Week ending 2026-10-02 (window 2026-09-26 to 2026-10-02) unless a test says otherwise.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import load_script, run_json, run_main

mod = load_script("weekly-status-rollup", "weekly_status_rollup.py")
ASOF = ["--as-of", "2026-10-02"]
BASE = "https://github.com/example-org/shop"


def pr(number, title, state="OPEN", draft=False, merged=None, labels=(), updated="2026-09-28T10:00:00Z", login="dev-a"):
    return {
        "number": number,
        "title": title,
        "url": f"{BASE}/pull/{number}",
        "state": state,
        "isDraft": draft,
        "mergedAt": merged,
        "closedAt": merged,
        "updatedAt": updated,
        "labels": [{"name": name} for name in labels],
        "author": {"login": login},
    }


def issue(number, title, state="OPEN", reason=None, closed=None, updated="2026-09-10T10:00:00Z"):
    return {
        "number": number,
        "title": title,
        "url": f"{BASE}/issues/{number}",
        "state": state,
        "stateReason": reason,
        "closedAt": closed,
        "updatedAt": updated,
        "labels": [],
        "assignees": [],
    }


PRS = [
    pr(12, "Add retry to checkout", state="MERGED", merged="2026-09-30T10:00:00Z"),
    pr(15, "Rate limit the export API"),
    pr(16, "Rotate signing keys", labels=["Blocked"]),
    pr(17, "Spike on a new queue", draft=True),
    pr(18, "Review page copy", login="dev-b"),
    pr(9, "Old merged work", state="MERGED", merged="2026-09-20T10:00:00Z"),
]
ISSUES = [
    issue(40, "Checkout fails on retry", state="CLOSED", reason="COMPLETED", closed="2026-09-30T11:00:00Z"),
    issue(41, "Dark mode", state="CLOSED", reason="NOT_PLANNED", closed="2026-09-29T11:00:00Z"),
    issue(42, "Write the on-call guide"),
    issue(43, "Tidy the backlog labels", updated="2026-10-01T09:00:00Z"),
]
GITLOG = (
    "a1b2c3d\t2026-09-30\tExample Dev\tAdd retry to checkout (#12)\n"
    "e4f5a6b\t2026-09-29\tExample Dev\tFix typo in docs\n"
    "only two\tfields\n"
    "9f8e7d6\t2026-09-20\tExample Dev\tOutside the window\n"
)
LAST_WEEK = f"""\
# Weekly status: week ending 2026-09-25

## In review

- [shop#15]({BASE}/pull/15) Rate limit the export API (in review, carried 1 week)
- shop#12 Add retry to checkout
- shop#99 Something that left the exports
- Write the on-call guide

## Decisions needed

- Pick a queue vendor

## Risks

_To fill in. The exports hold no decisions or risks, so none are generated._
"""


def exports(tmp_path: Path, prs=PRS, issues=ISSUES, gitlog=GITLOG, last=LAST_WEEK) -> list[str]:
    (tmp_path / "shop.log").write_text(gitlog, encoding="utf-8")
    (tmp_path / "prs.json").write_text(json.dumps(prs), encoding="utf-8")
    (tmp_path / "issues.json").write_text(json.dumps(issues), encoding="utf-8")
    argv = ["--git-log", str(tmp_path / "shop.log"), "--prs", str(tmp_path / "prs.json")]
    argv += ["--issues", str(tmp_path / "issues.json")]
    if last is not None:
        (tmp_path / "last.md").write_text(last, encoding="utf-8")
        argv += ["--last-week", str(tmp_path / "last.md")]
    return argv


def refs(items: list[dict]) -> list[str]:
    return [i["ref"] for i in items]


def test_empty_exports_give_an_empty_note_and_exit_zero(tmp_path):
    argv = exports(tmp_path, prs=[], issues=[], gitlog="", last=None)
    rc, out, _ = run_main(mod, [*argv, *ASOF])
    assert rc == 0
    assert "## Shipped\n\nNone." in out and "## Blocked\n\nNone." in out
    rc, _, err = run_main(mod, ASOF)
    assert rc == 2 and "give at least one of" in err


def test_malformed_inputs(tmp_path):
    argv = exports(tmp_path, last=None)
    _, rep = run_json(mod, [*argv, *ASOF, "--json"])
    assert rep["warnings"] == [
        "shop.log:3: expected 4 tab-separated fields (hash, YYYY-MM-DD date, author, subject); line skipped"
    ]
    (tmp_path / "prs.json").write_text("[{not json", encoding="utf-8")
    rc, _, err = run_main(mod, [*argv, *ASOF])
    assert rc == 2 and "prs.json: invalid JSON" in err


def test_grouping_and_commit_folding(tmp_path):
    rc, rep = run_json(mod, [*exports(tmp_path, last=None), *ASOF, "--json"])
    g = rep["groups"]
    assert refs(g["shipped"]) == ["shop#12", "shop#40"]
    assert refs(g["in_review"]) == ["shop#15", "shop#18"]
    assert refs(g["in_progress"]) == ["shop#17", "shop#43"]
    assert refs(g["blocked"]) == ["shop#16"]
    assert refs(rep["not_planned"]) == ["shop#41"]
    assert [c["hash"] for c in rep["direct_commits"]] == ["e4f5a6b"]  # a1b2c3d is folded into shop#12
    assert rc == 1  # a blocked item


def test_carry_over_counts_weeks_and_flags(tmp_path):
    rc, rep = run_json(mod, [*exports(tmp_path), *ASOF, "--json"])
    carried = {i["ref"]: (i["carried_weeks"], i["flagged_carry"]) for i in rep["groups"]["carried"]}
    assert carried == {"shop#15": (2, True), "shop#42": (1, False)}
    assert rep["not_found"] == ["shop#99"]
    shipped = next(i for i in rep["groups"]["shipped"] if i["ref"] == "shop#12")
    assert shipped["in_last_week"] is True
    rc, rep = run_json(mod, [*exports(tmp_path), *ASOF, "--json", "--carry-flag", "3"])
    assert not any(i["flagged_carry"] for i in rep["groups"]["carried"])


def test_decisions_and_risks_are_left_to_fill_never_invented(tmp_path):
    rc, out, _ = run_main(mod, [*exports(tmp_path), *ASOF])
    decisions = out.split("## Decisions needed", 1)[1].split("## Risks", 1)[0]
    risks = out.split("## Risks", 1)[1].split("## Warnings", 1)[0]
    assert "_To fill in." in decisions and "- Pick a queue vendor" in decisions
    assert risks.strip() == "_To fill in. The exports hold no decisions or risks, so none are generated._"


def test_daily_mode_and_login_filter(tmp_path):
    argv = exports(tmp_path, last=None)
    rc, out, _ = run_main(mod, [*argv, "--as-of", "2026-09-30", "--daily"])
    assert out.startswith("# Daily update: 2026-09-30\n")
    assert "## Done" in out and "## Today" in out and "Carried over" not in out
    assert "shop#12" in out.split("## In review", 1)[0]
    _, rep = run_json(mod, [*argv, *ASOF, "--json", "--login", "dev-b"])
    assert refs(rep["groups"]["in_review"]) == ["shop#18"] and rep["groups"]["blocked"] == []


GOLDEN = """\
# Weekly status: week ending 2026-10-02

Window 2026-09-26 to 2026-10-02 (7 days). Sources: git log shop (3 commits); prs.json (6 PRs); issues.json (4 \
issues); last week: last.md.

## Shipped

- [shop#12](https://github.com/example-org/shop/pull/12) Add retry to checkout (merged 2026-09-30, was in last \
week's note)
- [shop#40](https://github.com/example-org/shop/issues/40) Checkout fails on retry (issue, closed 2026-09-30)

### Commits not linked to a shipped PR

- shop: e4f5a6b Fix typo in docs (2026-09-29)

## In review

- [shop#18](https://github.com/example-org/shop/pull/18) Review page copy (in review)

## In progress

- [shop#17](https://github.com/example-org/shop/pull/17) Spike on a new queue (draft)
- [shop#43](https://github.com/example-org/shop/issues/43) Tidy the backlog labels (issue)

## Blocked

- [shop#16](https://github.com/example-org/shop/pull/16) Rotate signing keys (label Blocked)

## Carried over from last week (flagged at 2 weeks)

- [shop#15](https://github.com/example-org/shop/pull/15) Rate limit the export API (in review, carried 2 weeks, \
flagged)
- [shop#42](https://github.com/example-org/shop/issues/42) Write the on-call guide (issue, carried 1 week)

## Closed as not planned

- [shop#41](https://github.com/example-org/shop/issues/41) Dark mode (issue, closed 2026-09-29)

## In last week's note but not in this week's exports

- shop#99 (check by hand)

## Decisions needed

_To fill in. The exports hold no decisions or risks, so none are generated._

From last week's note (still open?):

- Pick a queue vendor

## Risks

_To fill in. The exports hold no decisions or risks, so none are generated._

## Warnings

- shop.log:3: expected 4 tab-separated fields (hash, YYYY-MM-DD date, author, subject); line skipped
"""


def test_golden_markdown(tmp_path):
    argv = exports(tmp_path)
    rc, out, _ = run_main(mod, [*argv, *ASOF])
    assert rc == 1
    assert out.replace(str(tmp_path), "TMP") == GOLDEN


def test_help():
    rc, out, _ = run_main(mod, ["--help"])
    assert rc == 0
    for flag in ("--git-log", "--prs", "--issues", "--last-week", "--daily", "--carry-flag", "--json", "--output"):
        assert flag in out
