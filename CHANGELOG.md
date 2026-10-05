# Changelog

All notable changes to this project are documented here. The format follows Keep a Changelog, and the project uses semantic versioning.

## [Unreleased]

## [0.1.0] - 2026-10-05

### Added

- Plugin marketplace `ways-of-working-skills` with one plugin, `ways-of-working`, whose skills each have a tested standard-library script that reads local files and never calls the network.
- `change-request-writer`: `change_request.py` reads a `terraform show -json` plan or a `gh pr view --json` export, counts add, change, destroy and replace by resource type, flags IAM, security group, KMS, S3 policy and network changes as needs reviewer, tiers risk by a stated rule, and prints a Markdown change record with summary, scope, risk, rollback, verification, approvers and maintenance window.
- `meeting-actions-ledger`: `meeting_ledger.py` extracts actions, owners and due dates from a folder of meeting notes, merges carry-overs, flags overdue, ownerless and items carried in three or more meetings, and writes `ledger.md` and `ledger.json`.
- `risk-exception-register`: `exception_register.py` lints a CSV or Markdown register of security exceptions for expired and expiring rows, missing approver or compensating control, approver equal to requester, repeated renewals and data problems, and prints a review agenda.
- `rfc-lifecycle`: an RFC outline and security review checklist, and `rfc_lint.py`, which lints a folder of RFCs for status, unanswered review threads, open questions, missing decision date, missing sections and time in review.
- `decision-log`: one file per operational decision, and `decision_log.py`, which lists decisions, flags overdue reviews, broken, one-way and cyclic supersede links and missing fields, and renders an index.
- `focus-plan`: `focus_plan.py` reads a saved calendar export (.ics) and reports meetings and hours in meetings per day, back-to-back runs (runs of `--run-flag` or more flagged), the longest free blocks inside configurable working hours, recurring series with occurrences and time, and a plain-language focus plan; DAILY and WEEKLY rules are expanded with EXDATE and RECURRENCE-ID applied, and any other rule is listed as not expanded.
- `one-on-one-ledger`: `one_on_one_ledger.py` builds a private per-person 1:1 ledger from a local notes folder (one file per person or dated files with a `## <name>` section) with days since the last 1:1 against a cadence, earlier gaps, open commitments on each side with overdue days and topics raised more than once; no ratings, rankings or comparisons, and it refuses (exit 3) a path inside a git repository or containing "shared" unless `--allow-shared` is passed.
- `weekly-status-rollup`: `weekly_status_rollup.py` writes one lead's weekly status note from saved `git log` text and `gh pr list` and `gh issue list` exports across repositories, grouping shipped, in review, in progress, blocked and carried-over items, counting weeks carried against last week's note, and leaving Decisions needed and Risks to fill; `--daily` gives a standup-sized note.
- `risk-register-refresh`: `risk_register_refresh.py` compares a CSV risk register with a previous snapshot and findings exports (Security Hub ASFF JSON, JSON or CSV) and reports new findings with no register entry, risks with no supporting finding, likelihood or impact changes, overdue or unreadable review dates and risks without an owner, with a refresh agenda; it uses the register's own values and never scores risk.
- `scripts/cli.py`: the `ways-of-working <subcommand>` dispatcher (`change-request`, `meeting-ledger`, `exception-register`, `rfc-lint`, `decision-log`, `weekly-status`, `one-on-one`, `focus-plan`, `risk-refresh`), used by the container image and the Python package.
- `scripts/validate_plugin.py`; a pytest suite whose inputs are built at run time; CI on Python 3.10 to 3.13 on Linux and macOS, a container build check and `claude plugin validate --strict`.
- `Dockerfile` and `publish-github-packages.yml`: on a `v*` tag, the image `ghcr.io/basitalisandhu/ways-of-working-skills` for linux/amd64 and linux/arm64 with an SPDX SBOM, a build provenance attestation and a keyless cosign signature.
- `release.yml`: PyPI trusted publishing of the `ways-of-working-skills` package, off until the repository variable `PYPI_PUBLISH` is `true`.
- Tasks for new contributors in `docs/good-first-issues.md`.

[Unreleased]: https://github.com/basitalisandhu/ways-of-working-skills/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/basitalisandhu/ways-of-working-skills/releases/tag/v0.1.0
