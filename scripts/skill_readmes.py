#!/usr/bin/env python3
"""Write or check plugins/ways-of-working/skills/<skill>/README.md: what the skill does, one command to try it on the
files in its examples/ folder, and the real output of that command.

    python3 scripts/skill_readmes.py           write every README from a fresh run of its sample command
    python3 scripts/skill_readmes.py --check   exit 1 if any README's sample no longer matches a fresh run

The sample commands run from the skill's own folder with this Python, so the output shows relative paths. Standard
library only. Exit codes: 0 written or all match, 1 a README is out of date (with --check), 2 bad input.
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "plugins" / "ways-of-working" / "skills"

# skill: (sample command run from the skill folder, output language for the fence)
SAMPLES: dict[str, tuple[str, str]] = {
    "change-request-writer": (
        'scripts/change_request.py examples/plan.json --change-id CHG-1042 --window "Sat 02:00 to 04:00 UTC"',
        "markdown",
    ),
    "decision-log": ("scripts/decision_log.py examples/decisions --as-of 2026-10-05", "markdown"),
    "focus-plan": ("scripts/focus_plan.py examples/week.ics --start 2026-10-05 --tz UTC", "markdown"),
    "meeting-actions-ledger": ("scripts/meeting_ledger.py examples/notes --as-of 2026-10-12", "markdown"),
    "next-actions-sweep": (
        "scripts/next_actions.py --source work-mail=examples/work-mail.json --source home-mail=examples/home-mail.json"
        " --source teams=examples/teams.json --source slack=examples/slack.json --source other=examples/other.csv"
        ' --me alex.morgan@example.com --me alex@example.org --me "Alex Morgan" --me U0ME --me u-alex'
        " --vip priya.shah@example.com --as-of 2026-10-16T09:00:00Z",
        "markdown",
    ),
    "one-on-one-ledger": (
        "scripts/one_on_one_ledger.py examples/notes --as-of 2026-10-05 --allow-shared",
        "markdown",
    ),
    "rfc-lifecycle": ("scripts/rfc_lint.py examples/rfcs --as-of 2026-10-05", "markdown"),
    "risk-exception-register": ("scripts/exception_register.py examples/exceptions.csv --as-of 2026-10-05", "markdown"),
    "risk-register-refresh": (
        "scripts/risk_register_refresh.py examples/register.csv --previous examples/previous.csv"
        " --findings examples/findings.json --as-of 2026-10-05",
        "markdown",
    ),
    "shift-handover": (
        "scripts/shift_handover.py examples/incidents.csv --alerts examples/alerts.csv --notes examples/notes.txt"
        " --as-of 2026-10-05T08:00:00Z",
        "markdown",
    ),
    "vendor-scorecard": ("scripts/vendor_scorecard.py examples/scores.csv", "markdown"),
    "weekly-status-rollup": (
        "scripts/weekly_status_rollup.py --git-log shop=examples/shop.log --prs examples/prs.json"
        " --issues examples/issues.json --last-week examples/last-week.md --as-of 2026-10-01",
        "markdown",
    ),
    "working-agreement-check": (
        "scripts/working_agreement_check.py examples/agreement.md --protection examples/protection.json"
        " --codeowners examples/CODEOWNERS --workflows examples/workflows.json",
        "markdown",
    ),
}
NOTES = {
    "one-on-one-ledger": "`--allow-shared` is needed only because the examples live inside this repository; the "
    "script refuses notes in a repository or shared folder unless told otherwise.",
}
START, END = "<!-- sample-output: start -->", "<!-- sample-output: end -->"


def description(skill: str) -> str:
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r'^description: "(.*)"$', text, re.M)
    if not m:
        raise SystemExit(f"skill_readmes.py: {skill}/SKILL.md has no one-line double-quoted description")
    return m.group(1).replace('\\"', '"')


def run(skill: str) -> tuple[int, str]:
    command, _ = SAMPLES[skill]
    proc = subprocess.run([sys.executable, *shlex.split(command)], cwd=SKILLS / skill, capture_output=True,
                          text=True, encoding="utf-8", timeout=120, check=False)
    if proc.returncode not in (0, 1):
        raise SystemExit(f"skill_readmes.py: {skill} sample exited {proc.returncode}: {proc.stderr.strip()}")
    return proc.returncode, proc.stdout


def render(skill: str, rc: int, output: str) -> str:
    desc = description(skill)
    what = desc.split(" Use when")[0]
    not_for = next((s for s in re.split(r"(?<=\.) ", desc) if s.startswith("Not for")), "")
    command, lang = SAMPLES[skill]
    fence = "````" if "```" in output else "```"
    meaning = "nothing flagged" if rc == 0 else "something needs a person"
    out = [
        f"# {skill}",
        "",
        what,
        "",
        f"{not_for}" if not_for else "",
        "",
        "How Claude runs it, step by step: [SKILL.md](SKILL.md). Everything runs on files you saved; no network.",
        "",
        "## Try it",
        "",
        "From this folder, on the synthetic files in [examples/](examples/):",
        "",
        "```bash",
        f"python3 {command}",
        "```",
        "",
    ]
    if skill in NOTES:
        out += [NOTES[skill], ""]
    out += [
        "## Sample output",
        "",
        f"The real output of that command (exit code {rc}, {meaning}):",
        "",
        START,
        f"{fence}{lang}",
        output.rstrip("\n"),
        fence,
        END,
        "",
        "This README is generated by `scripts/skill_readmes.py` and checked against a fresh run by the tests.",
        "",
    ]
    return "\n".join(line for i, line in enumerate(out) if line or out[i - 1] != "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write or check the per-skill README samples.",
                                 epilog="Exit codes: 0 written or all match, 1 out of date (--check), 2 bad input.")
    ap.add_argument("--check", action="store_true", help="compare instead of writing")
    args = ap.parse_args(argv)
    missing = sorted(p.parent.name for p in SKILLS.glob("*/SKILL.md") if p.parent.name not in SAMPLES)
    if missing:
        print(f"skill_readmes.py: no sample command for {', '.join(missing)}", file=sys.stderr)
        return 2
    stale = []
    for skill in sorted(SAMPLES):
        rc, output = run(skill)
        text = render(skill, rc, output)
        path = SKILLS / skill / "README.md"
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                stale.append(skill)
        else:
            path.write_text(text, encoding="utf-8")
    if stale:
        print("skill_readmes.py: out of date, run python3 scripts/skill_readmes.py: " + ", ".join(stale),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
