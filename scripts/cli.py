#!/usr/bin/env python3
"""ways-of-working: one command for the ways-of-working skill scripts.

    ways-of-working <subcommand> [args]       run one skill script with the given arguments
    ways-of-working <subcommand> --help       that script's own help
    ways-of-working --help                    list the subcommands

Each subcommand runs plugins/ways-of-working/skills/<skill>/scripts/<script>.py unchanged, in a child process with
the same Python, stdin, stdout, stderr and exit code. Standard library only. This is the entrypoint of the
container image ghcr.io/basitalisandhu/ways-of-working-skills and of the ways-of-working-skills Python package.
To add a skill, add one entry to COMMANDS.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

__version__ = "0.2.0"

PROG = "ways-of-working"
HERE = Path(__file__).resolve().parent
# In a checkout or the container image the skills sit at <root>/plugins/ways-of-working/skills; in the installed
# Python package they sit next to this file, at ways_of_working_skills/skills.
SKILLS = next(
    (p for p in (HERE.parent / "plugins" / "ways-of-working" / "skills", HERE / "skills") if p.is_dir()),
    HERE.parent / "plugins" / "ways-of-working" / "skills",
)

# subcommand: (skill directory, script, one-line summary)
COMMANDS: dict[str, tuple[str, str, str]] = {
    "change-request": (
        "change-request-writer",
        "change_request.py",
        "CAB-style change record from a Terraform plan JSON or gh pr view export",
    ),
    "meeting-ledger": (
        "meeting-actions-ledger",
        "meeting_ledger.py",
        "One action ledger across a folder of meeting notes, with overdue and carried items",
    ),
    "exception-register": (
        "risk-exception-register",
        "exception_register.py",
        "Lint a security exception register and print a review agenda",
    ),
    "rfc-lint": (
        "rfc-lifecycle",
        "rfc_lint.py",
        "Lint a folder of RFCs for status, unanswered threads, open questions and time in review",
    ),
    "decision-log": (
        "decision-log",
        "decision_log.py",
        "List, lint and index a folder of one-file-per-decision records",
    ),
    "weekly-status": (
        "weekly-status-rollup",
        "weekly_status_rollup.py",
        "One lead's weekly status note from git log and gh exports, with carried-over items",
    ),
    "one-on-one": (
        "one-on-one-ledger",
        "one_on_one_ledger.py",
        "Private per-person 1:1 ledger: cadence gaps, open commitments, repeated topics",
    ),
    "focus-plan": (
        "focus-plan",
        "focus_plan.py",
        "Meeting load, back-to-back runs and free blocks from a calendar export (.ics)",
    ),
    "risk-refresh": (
        "risk-register-refresh",
        "risk_register_refresh.py",
        "Quarterly risk register refresh against findings exports and last quarter's snapshot",
    ),
    "vendor-scorecard": (
        "vendor-scorecard",
        "vendor_scorecard.py",
        "Weighted vendor or tool scorecard from a CSV, with a one-step weight sensitivity check",
    ),
    "working-agreement": (
        "working-agreement-check",
        "working_agreement_check.py",
        "Check a written working agreement against branch protection, rulesets, CODEOWNERS and workflow exports",
    ),
    "shift-handover": (
        "shift-handover",
        "shift_handover.py",
        "On-call or shift handover note from incident and alert exports: open by age, stale, noisy, silenced",
    ),
}


def script_path(name: str) -> Path:
    skill, script, _ = COMMANDS[name]
    return SKILLS / skill / "scripts" / script


def usage() -> str:
    width = max(len(n) for n in COMMANDS)
    lines = [
        f"usage: {PROG} <subcommand> [args]",
        "",
        f"Runs one of the ways-of-working skill scripts over files you saved (no network). "
        f"Use '{PROG} <subcommand> --help' for its options.",
        "",
        "subcommands:",
    ]
    lines += [f"  {n.ljust(width)}  {h} ({script})" for n, (_, script, h) in COMMANDS.items()]
    lines += ["", "options:", "  -h, --help     show this help and exit", "  --version      show the version and exit"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(usage(), file=sys.stderr)
        return 2
    first, rest = args[0], args[1:]
    if first in ("-h", "--help", "help") and not rest:
        print(usage())
        return 0
    if first == "help":
        first, rest = rest[0], ["--help"]
    if first == "--version":
        print(f"{PROG} {__version__}")
        return 0
    if first not in COMMANDS:
        print(f"{PROG}: unknown subcommand {first!r}\n\n{usage()}", file=sys.stderr)
        return 2
    return subprocess.call([sys.executable, str(script_path(first)), *rest])


if __name__ == "__main__":
    sys.exit(main())
