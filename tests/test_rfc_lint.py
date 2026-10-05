"""Tests for rfc_lint.py. Each test writes a synthetic RFC folder into tmp_path."""
from __future__ import annotations

import textwrap

from conftest import load_script, run_json, run_main, write_files

mod = load_script("rfc-lifecycle", "rfc_lint.py")
AS_OF = ["--as-of", "2026-10-05"]

SECTIONS = textwrap.dedent("""
    ## Summary
    Text.
    ## Motivation
    Text.
    ## Proposal
    Text.
    ## Alternatives considered
    Do nothing.
    ## Security review
    Answered.
    ## Rollout and rollback
    Steps.
    ## Open questions
    - None
    ## Decision
    Pending.
    """)

REVIEW = """
    ---
    title: Move build runners to a private subnet
    status: review
    authors: platform team
    created: 2026-09-01
    review-started: 2026-09-10
    ---
    # Move build runners to a private subnet
    ## Summary
    Runners move.
    ## Motivation
    Exposure.
    ## Proposal
    Q: What happens to jobs that need the public package mirror?
    Q: Who owns the NAT gateway?
    A: The networking team.
    ## Alternatives considered
    Do nothing.
    ## Security review
    > Q: Are runner credentials rotated?
    > A: Yes, daily.
    ## Rollout and rollback
    Steps.
    ## Open questions
    - Which regions go first?
    - ~~Do we need a second NAT gateway?~~
    - Cost per month (resolved)
    ## Decision
    Pending.
    """


def rfcs(tmp_path, files):
    return str(write_files(tmp_path / "rfcs", files))


def findings(rep: dict) -> list[tuple[str, str, int]]:
    return [(f["file"], f["rule"], f["line"]) for r in rep["rfcs"] for f in r["findings"]]


def test_unanswered_threads_open_questions_and_overdue_review(tmp_path):
    rc, rep = run_json(mod, [rfcs(tmp_path, {"0003-private-runners.md": REVIEW}), *AS_OF])
    assert rc == 1
    assert findings(rep) == [
        ("0003-private-runners.md", "unanswered-thread", 14),
        ("0003-private-runners.md", "open-questions", 25),
        ("0003-private-runners.md", "review-overdue", 1),
    ]
    r = rep["rfcs"][0]
    assert (r["days_in_review"], r["review_basis"]) == (25, "review-started")
    assert [q["text"] for q in r["open_questions"]] == ["Which regions go first?"]


def test_review_limit_and_created_fallback(tmp_path):
    text = REVIEW.replace("    review-started: 2026-09-10\n", "")
    _, rep = run_json(mod, [rfcs(tmp_path, {"r.md": text}), *AS_OF, "--max-review-days", "40"])
    assert rep["rfcs"][0]["review_basis"] == "created (no review-started)"
    assert rep["rfcs"][0]["days_in_review"] == 34
    assert "review-overdue" not in [f[1] for f in findings(rep)]


def test_status_header_and_decision_date_rules(tmp_path):
    files = {
        "a-no-status.md": "# Idea\n\nText.\n",
        "b-bad-status.md": "---\nstatus: maybe\n---\n",
        "c-accepted.md": "---\ntitle: C\nstatus: accepted\n---\n" + SECTIONS,
        "d-superseded.md": "---\ntitle: D\nstatus: superseded\ndecision-date: 2026-09-01\n---\n" + SECTIONS,
        "e-rejected.md": "---\ntitle: E\nstatus: rejected\ndecision-date: 2026-09-31\n---\n",
    }
    _, rep = run_json(mod, [rfcs(tmp_path, files), *AS_OF])
    got = {(f[0], f[1]) for f in findings(rep)}
    assert got == {
        ("a-no-status.md", "missing-status"),
        ("b-bad-status.md", "invalid-status"),
        ("c-accepted.md", "missing-decision-date"),
        ("d-superseded.md", "missing-superseded-by"),
        ("e-rejected.md", "invalid-date"),
        ("e-rejected.md", "missing-decision-date"),
    }


def test_missing_sections_only_for_review_and_accepted(tmp_path):
    files = {
        "draft.md": "---\ntitle: Draft\nstatus: draft\n---\n## Open questions\n- Everything?\n",
        "accepted.md": "---\ntitle: A\nstatus: accepted\ndecision-date: 2026-09-20\n---\n## Summary\nText.\n",
    }
    rc, rep = run_json(mod, [rfcs(tmp_path, files), *AS_OF])
    missing = [f for f in findings(rep) if f[1] == "missing-section"]
    assert {f[0] for f in missing} == {"accepted.md"} and len(missing) == 7
    _, rep = run_json(mod, [rfcs(tmp_path, files), *AS_OF, "--sections", ""])
    assert findings(rep) == []
    draft = next(r for r in rep["rfcs"] if r["file"] == "draft.md")
    assert [q["text"] for q in draft["open_questions"]] == ["Everything?"]


def test_malformed_front_matter_and_skipped_files(tmp_path):
    files = {
        "broken.md": "---\ntitle: Never closed\nstatus: review\n\n# Body\n",
        "_template.md": "---\nstatus: nonsense\n---\n",
        "README.md": "# RFCs\n",
    }
    rc, rep = run_json(mod, [rfcs(tmp_path, files), *AS_OF])
    assert rc == 1
    assert [r["file"] for r in rep["rfcs"]] == ["broken.md"]
    assert findings(rep) == [("broken.md", "malformed-front-matter", 1)]


def test_empty_and_bad_inputs_exit_2(tmp_path):
    (tmp_path / "empty").mkdir()
    rc, _, err = run_main(mod, [str(tmp_path / "empty")])
    assert rc == 2 and "no RFC files" in err
    rc, _, err = run_main(mod, [str(tmp_path / "missing")])
    assert rc == 2 and "not a folder" in err
    rc, _, err = run_main(mod, [rfcs(tmp_path, {"x.md": REVIEW}), "--as-of", "yesterday"])
    assert rc == 2 and "--as-of" in err


def test_clean_rfc_exits_0(tmp_path):
    text = "---\ntitle: Clean\nstatus: accepted\ndecision-date: 2026-09-20\n---\n" + SECTIONS
    rc, out, _ = run_main(mod, [rfcs(tmp_path, {"clean.md": text}), *AS_OF])
    assert rc == 0 and "## Findings\n\nNone." in out


GOLDEN = """\
# RFC lifecycle lint

As of 2026-10-05. 1 RFC(s), 3 finding(s). Review limit 14 days.

| RFC | Title | Status | Days in review | Open questions | Unanswered threads | Decision date |
|---|---|---|---|---|---|---|
| `0003-private-runners.md` | Move build runners to a private subnet | review | 25 | 1 | 1 |  |

## Findings

- `0003-private-runners.md:14` unanswered-thread: Q: What happens to jobs that need the public package mirror?
- `0003-private-runners.md:25` open-questions: Which regions go first?
- `0003-private-runners.md:1` review-overdue: in review 25 days since 2026-09-10 (review-started); limit 14
"""


def test_golden_report(tmp_path):
    rc, out, _ = run_main(mod, [rfcs(tmp_path, {"0003-private-runners.md": REVIEW}), *AS_OF])
    assert rc == 1
    assert out == GOLDEN
