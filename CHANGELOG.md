# Changelog

All notable changes to this project are documented here. The format follows Keep a Changelog, and the project uses semantic versioning.

## [Unreleased]

## [0.4.0] - 2026-10-18

### Changed

- One exit-code convention for the whole pack, now held by `tests/test_exit_codes.py` for every skill: 0 nothing flagged, 1 something needs a person, 2 bad input (3 only for a path `one-on-one-ledger` refuses). `next-actions-sweep` used to exit 0 whenever it wrote its list; it now exits 1 when the list has a reply, decide or do item or a waiting-on-others entry, and 0 when only FYI items remain. New `--exit-zero` keeps the old behaviour for a scheduled run that treats 1 as a failure. If you call `next_actions.py` from a script, check its exit code handling.
- Descriptions of `decision-log`, `risk-exception-register` and `risk-register-refresh` checked against the labelled trigger prompts in the hub's research notes: decision-log now catches "record that we decided ..." and both risk skills say they are not for user access reviews.
- README quick start runs as written from a clone and shows real output.

### Fixed

- An `--out`, `--output` or `--index` path that cannot be written made `change-request-writer`, `decision-log`, `meeting-actions-ledger`, `rfc-lifecycle`, `risk-exception-register`, `shift-handover`, `vendor-scorecard`, `working-agreement-check` and `next-actions-sweep` stop with a traceback and exit 1, which the convention reserves for "needs a person". They now print the reason and exit 2.
- `working-agreement-check`: a `--protection` or `--rulesets` export that is not UTF-8 exits 2 with a message instead of a traceback.
- `vendor-scorecard`: a column with scores but no vendor name exits 2 instead of being dropped silently; a semicolon or tab separated file gets a hint; "no usable criterion rows" now names the first problem and its line.

## [0.3.0] - 2026-10-16

### Added

- `next-actions-sweep`: `next_actions.py` reads saved exports from several accounts (`--source NAME=PATH[:FORMAT]`, repeatable: Microsoft Graph messages JSON, Microsoft Graph Teams chatMessage JSON, Slack export JSON, or a CSV), keeps the latest message per thread, drops threads the user (`--me`, repeatable) already answered, merges duplicates across accounts (same sender and subject within an hour, or the same conversationId), labels each item reply, decide, do or FYI by stated phrase rules, reads deadlines only from explicit forms, ranks by documented weights with deterministic ties, and lists the user's unanswered questions older than `--waiting-days` under "Waiting on others"; excerpts are at most 120 characters, `--redact` tokenises names and addresses other than `--vip`, `--lenient` skips bad records with a count; Markdown or `--format json`; exit 0 when the list is written, 2 on bad input. 23 tests, synthetic examples for two invented accounts plus Teams, Slack and CSV with a golden output.
- `scripts/cli.py` subcommand `next-actions`, with the matching wheel force-include line, container build check and wheel check.
- `risk-exception-register`: `--buckets 30,60,90` splits "Expiring soon" into one sub-list per bucket of days ahead, for quarterly reviews; the window becomes the largest bucket. Without the option the output is unchanged.

### Changed

- Clarify manual script invocation instructions when skills are copied directly into `.claude/skills/`.

## [0.2.1] - 2026-10-05

### Changed

- All 12 skill descriptions rewritten to under 600 characters, goal before mechanism, each with one quoted trigger phrase, a "Use when" sentence and a "Not for" boundary.
- `scripts/validate_plugin.py` fails on a description that is not one double-quoted line, is over 600 characters, or lacks "Use " or "Not for", and on a SKILL.md without `## Limits`; tests cover each rule.
- Tests and the validator read and write text as UTF-8 explicitly; `.gitattributes` keeps LF line endings on every platform.
- CI runs the test suite on Windows as well as Linux and macOS.
- `focus-plan`: the unknown `--tz` error says to install `tzdata` on Windows, where Python ships no IANA zone data; the Limits section says the same. The calendar test fixture is written as bytes so its CRLF line endings survive on Windows.
- The root and plugin READMEs list the searches this pack answers: "meeting overload", "action items", "weekly update".

## [0.2.0] - 2026-10-05

### Added

- `vendor-scorecard`: `vendor_scorecard.py` reads a CSV of criteria, weights and 1 to 5 scores per vendor, computes weighted scores and the ranking with exact fractions, moves each weight by `--step` (default 1) up and down to report whether the leader and the full order survive, flags ties for first, leader changes and data problems, and prints a Markdown scorecard with a blank decision line.
- `working-agreement-check`: `working_agreement_check.py` reads a team's working agreement in Markdown, maps recognised rules (review counts, code owner review, protected branch, required checks, force pushes, deletion, linear history, signed commits, admin enforcement, stale reviews, conversation resolution, CODEOWNERS coverage) to saved branch protection, rulesets, CODEOWNERS and workflow list exports, and reports each as met, not met or not checkable from these exports, listing rules it does not recognise; `--strict` also exits 1 on not checkable.
- `shift-handover`: `shift_handover.py` reads incident and alert exports (JSON or CSV) and optional notes, groups open incidents by age, flags no update within `--stale-hours`, no owner, noisy alerts, and silences that end within `--horizon-hours` or already ended, and writes a forward-looking handover note.
- `scripts/cli.py` subcommands `vendor-scorecard`, `working-agreement` and `shift-handover`, with the matching wheel force-include lines, container build check and wheel check.

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

[Unreleased]: https://github.com/basitalisandhu/ways-of-working-skills/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/basitalisandhu/ways-of-working-skills/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/basitalisandhu/ways-of-working-skills/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/basitalisandhu/ways-of-working-skills/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/basitalisandhu/ways-of-working-skills/releases/tag/v0.1.0
