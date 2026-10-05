# Claude Code skills for ways of working: the records a team keeps, from files you already have

**Nine skills: change requests, action ledgers, exception and risk registers, RFC lint, a decision log, weekly status notes, a private 1:1 ledger and focus time from a calendar export. Offline, standard-library scripts, tested.**

ways-of-working-skills is a Claude Code plugin for the records that keep a team honest about its own process: what a change will do before the board approves it, which actions from last month's meetings are still open, which security exceptions have expired and which risks need a fresh look, which design proposals are stuck in review, which operational decisions are due for a second look, what a lead shipped this week, what each side promised in 1:1s, and where the focus time went. These records usually live in files nobody re-reads. Each skill here reads those files with a small Python script, applies stated rules, cites the file and line behind every finding, and leaves decisions to people.

```text
/plugin marketplace add basitalisandhu/ways-of-working-skills
/plugin install ways-of-working@ways-of-working-skills
```

Quickstart: open Claude Code in a folder of meeting notes and ask "which actions from our syncs are overdue?". Or run a script directly from a clone of this repository:

```bash
python3 scripts/cli.py meeting-ledger path/to/notes --as-of 2026-10-05
python3 scripts/cli.py change-request plan.json --out change-record.md
```

Questions, bugs and ideas: open an issue on this repository. Security reports: see [SECURITY.md](SECURITY.md).

This pack is part of [claude-skills](https://github.com/basitalisandhu/claude-skills), which holds every skill I maintain as one marketplace: `/plugin marketplace add basitalisandhu/claude-skills`.

## When to use this

- A Terraform change is going to the change advisory board and you need the record: what is added, changed, destroyed and replaced, and which IAM, security group, KMS, S3 policy or network changes need a reviewer. `change-request-writer`
- Actions keep reappearing in meeting notes week after week, and nobody knows which are overdue or have no owner. `meeting-actions-ledger`
- The security exceptions register is due for review, or an audit is coming, and you need to know what has expired, what has no approver and what keeps being renewed. `risk-exception-register`
- A design proposal needs writing with a security review, or you want to know which RFCs are stuck in review with unanswered questions. `rfc-lifecycle`
- The team keeps re-deciding things it already decided, and wants a light record with review dates. `decision-log`
- The weekly status note is due and you want shipped, in review, blocked and carried-over items from git and GitHub exports, not from memory. `weekly-status-rollup`
- A 1:1 is coming up and you want the open commitments on both sides and the days since the last one, kept private. `one-on-one-ledger`
- The week feels full of meetings and you want the real load, the back-to-back runs and the longest free blocks from a calendar export. `focus-plan`
- The quarterly risk review is due and the register has to be compared with this quarter's findings and last quarter's snapshot. `risk-register-refresh`

## Skills

| Skill | Triggers on | Script | Reads | What it produces |
|---|---|---|---|---|
| `change-request-writer` | "write the change request for this plan", "draft the CAB record", "what does this apply destroy?" | `change_request.py` | `terraform show -json` plan, or `gh pr view --json` export | add, change, destroy and replace counts by resource type; IAM, security group, KMS, S3 policy and network changes flagged as needs reviewer; a risk tier by a stated rule; a Markdown record with summary, scope, risk, rollback, verification, approvers and window placeholders |
| `meeting-actions-ledger` | "what is still open from our meetings?", "which actions are overdue?", "open actions for the next sync" | `meeting_ledger.py` | a folder of Markdown or text notes with `- [ ] owner: action (due YYYY-MM-DD)` or `Action:` lines | `ledger.md` and `ledger.json`: one row per action across meetings, carry-overs merged, overdue, ownerless and carried-three-times items flagged, each cited to file and line |
| `risk-exception-register` | "prepare the exception review", "what has expired in the register?", "which exceptions keep being renewed?" | `exception_register.py` | a CSV or Markdown table (id, system, control, reason, approver, granted, expires, compensating controls) | expired and expiring-in-30-days rows, missing approver or compensating control, approver equal to requester, repeated renewals, data problems, as a review agenda with blank decision lines |
| `rfc-lifecycle` | "draft an RFC for ...", "which RFCs are stuck in review?", "which review questions are unanswered?" | `rfc_lint.py` | a folder of RFC Markdown files with a status header | an RFC outline and security review checklist; a lint for status, unanswered `Q:` threads, open questions, missing decision date, missing sections and more than 14 days in review |
| `decision-log` | "write down that we decided ...", "which decisions are due for review?", "rebuild the decision index" | `decision_log.py` | a folder of one-file-per-decision Markdown records | an index table (`--index INDEX.md`), overdue reviews, broken, one-way or cyclic supersede links, missing fields |
| `focus-plan` | "where can I find focus time this week?", "how meeting-heavy is my week?", "which recurring meetings eat my time?" | `focus_plan.py` | a calendar export (.ics) from Outlook, Google Calendar or Apple Calendar | meetings and hours in meetings per day, back-to-back runs (runs of three or more flagged), the longest free blocks inside working hours, recurring series with occurrences and time, rules not expanded listed instead of guessed, and a numbered focus plan |
| `one-on-one-ledger` | "prep my 1:1 with Sam", "who am I overdue a 1:1 with?", "what did I promise in my 1:1s?" | `one_on_one_ledger.py` | a private local folder of notes: one file per person with `## YYYY-MM-DD` sections, or dated files with `## <name>` sections | one ledger per person: days since the last 1:1 against the cadence, earlier gaps, open commitments on each side with overdue days, topics raised more than once; no ratings, rankings or comparisons; refuses paths inside a repository or containing "shared" |
| `weekly-status-rollup` | "write my weekly status", "what did I ship this week?", "what keeps carrying over?", "standup update" | `weekly_status_rollup.py` | `git log` text per repository, `gh pr list --json` and `gh issue list --json` exports, last week's note | a status note with shipped, in review, in progress, blocked and carried-over items (weeks carried counted from last week's note), Decisions needed and Risks left to fill; `--daily` for a standup-sized note |
| `risk-register-refresh` | "prepare the quarterly risk review", "which findings are not in the register?", "which risks are overdue for review?" | `risk_register_refresh.py` | a register CSV, last quarter's snapshot, and findings as Security Hub ASFF JSON, JSON or CSV | new findings with no register entry, risks with no supporting finding, likelihood or impact changes as written, overdue or missing review dates, risks without an owner, and a refresh agenda; no scoring |

Every script reads local files only, prints Markdown by default and JSON with `--json`, answers `--help`, and writes only to the path given with `--out`, `--output` or `--index`. Exit codes are the same across the pack: 0 nothing flagged, 1 something needs a person, 2 bad input; `one-on-one-ledger` adds 3 when it refuses a path that looks shared or sits inside a repository. Where a date matters it is judged against `--as-of` (`focus-plan` takes `--start`), so a run can be repeated later with the same result.

**focus-plan.** A week rarely runs out of hours; it runs out of unbroken ones. `focus-plan` reads a calendar the user exported to an .ics file and reports, for each day of the chosen week, how many meetings there are, how much time they cover with overlaps counted once, where meetings run back to back, and the longest free blocks inside the working hours the user sets. It lists recurring series with their occurrences and time in the window, and writes a short focus plan that points at real gaps. Daily and weekly recurrence rules are expanded with exclusions and moved occurrences applied; monthly, yearly and other rules are listed as not expanded rather than guessed. It never writes to a calendar and never says which meetings to decline.

**one-on-one-ledger.** `one-on-one-ledger` turns the notes a lead already keeps into a private ledger per person: when the last 1:1 was against the expected cadence, earlier gaps, what each side still owes (the lead's own overdue promises first), and the topics that keep coming back. It accepts one file per person with dated sections, or dated files with a section per person, using a small checkbox syntax. The privacy rules are built into the script as well as the skill: no ratings, rankings, sentiment or comparisons between people, output stays local, and the script refuses to read or write under a path that sits inside a git repository or contains "shared" unless `--allow-shared` is passed.

**weekly-status-rollup.** `weekly-status-rollup` does the bookkeeping part of a lead's weekly note from saved `git log` text and `gh pr list` and `gh issue list` exports across one or more repositories: shipped, in review, in progress, blocked, and carried over, with the number of weeks each item has carried counted from last week's note. Commits that reference a shipped PR are folded into it. "Decisions needed" and "Risks" stay as sections for the author to fill; entries from last week are copied as questions, never invented. `--daily` gives a standup-sized version. It is one lead's narrative note; team sprint metrics belong to `iteration-report` and the review queue to `pr-queue-digest`.

**risk-register-refresh.** `risk-register-refresh` prepares the quarterly review of a risk register kept as CSV. It compares the register with this quarter's findings (Security Hub ASFF JSON, generic JSON or CSV saved to disk) and with last quarter's snapshot, and reports findings that no risk covers, risks whose linked findings have gone, likelihood or impact values that changed, overdue or unreadable review dates, and risks without an owner, then renders the agenda for the meeting. Likelihood and impact are shown exactly as the register states them; the script never scores, rates or ranks risk, and it does not connect to any GRC tool.

## What this is not

- **Not a decision maker.** No skill approves a change, accepts a risk, renews an exception or picks a design. They lay out the facts and leave the decision lines blank.
- **Not a rating of people.** Owners, approvers and deciders appear by name where the files name them, and nowhere are they counted, ranked or scored. The skills tell Claude to decline such requests.
- **Not an integration.** No skill connects to ServiceNow, Jira, Vanta, a live calendar, chat or a GRC platform. Export to a file first, or paste the Markdown where it is needed.
- **Not a replacement for review.** The RFC lint checks shape and dates; the review itself is still a conversation between people.

## How it fits with the other packs

- `change-request-writer` comes after `terraform-review` (claude-dev-skills) has reviewed the code, and before `github-change-control-evidence` (compliance-evidence-skills) evidences the change for an audit.
- `rfc-lifecycle` covers the proposal and its review; `adr-writer` (claude-dev-skills) writes the architecture decision record once an RFC is accepted; `adr-miner` (repo-engineering-skills) recovers old decisions from git history; `decision-log` is for everyday operational decisions that need neither.
- `weekly-status-rollup` is one lead's note; `iteration-report` (github-manager-skills) gives team sprint metrics and `pr-queue-digest` the review queue.
- `risk-register-refresh` compares quarters of a risk register; `risk-exception-register` tracks accepted exceptions with an expiry; `access-review-pack` (m365-governance-skills) is for access recertification and `security-hub-triage` (aws-security-skills) for a live findings backlog.

## Install

The plugin installs as shown at the top. The scripts are also available without Claude Code:

- **Container image** (GitHub Packages, linux/amd64 and linux/arm64), entrypoint `ways-of-working <subcommand> [args]`; mount the input folder at `/work`. The image is published when a version tag is pushed, signed with cosign (keyless), with a build provenance attestation and an SPDX SBOM attached to the GitHub Release:

  ```bash
  docker run --rm -v "$PWD:/work" ghcr.io/basitalisandhu/ways-of-working-skills:0.1.0 meeting-ledger /work/notes --as-of 2026-10-05
  ```

- **Python package** `ways-of-working-skills`, which installs the same `ways-of-working` command. PyPI publishing is set up in `release.yml` but switched off until the trusted publisher is configured, so until then install from a clone: `pip install .`

| Subcommand | Script (skill) |
|---|---|
| `change-request` | `change_request.py` (change-request-writer) |
| `meeting-ledger` | `meeting_ledger.py` (meeting-actions-ledger) |
| `exception-register` | `exception_register.py` (risk-exception-register) |
| `rfc-lint` | `rfc_lint.py` (rfc-lifecycle) |
| `decision-log` | `decision_log.py` (decision-log) |
| `weekly-status` | `weekly_status_rollup.py` (weekly-status-rollup) |
| `one-on-one` | `one_on_one_ledger.py` (one-on-one-ledger) |
| `focus-plan` | `focus_plan.py` (focus-plan) |
| `risk-refresh` | `risk_register_refresh.py` (risk-register-refresh) |

From a checkout, `python3 scripts/cli.py` is the same dispatcher.

## Security posture

- **Local, offline scripts.** The skill scripts read the file or folder you name. They import no network or subprocess module (the repository validator checks this) and write only to the `--out`, `--output` or `--index` path you give.
- **Your files stay yours.** Plans, notes and registers can hold internal names and system details. Keep them out of version control; `.gitignore` already ignores common names such as `plan.json` and `pr.json`.
- **Untrusted content.** Notes, plans and registers are data, never instructions: each `SKILL.md` says so.
- **Synthetic tests.** The tests build their inputs at run time with made-up names; no real plan, note or register is committed.

## What is inside

```text
.claude-plugin/marketplace.json                       marketplace manifest
plugins/ways-of-working/
├── .claude-plugin/plugin.json                        plugin manifest
├── README.md                                         the plugin's skill table
└── skills/<name>/
    ├── SKILL.md                                      when to use it, inputs, steps, script, output, limits, related skills
    └── scripts/<script>.py                           standard library only, --help, --json, documented exit codes
scripts/cli.py                                        the ways-of-working dispatcher (container and package entrypoint)
scripts/validate_plugin.py                            structure, frontmatter, scripts, READMEs, house style
tests/                                                pytest suite, offline; inputs are built in each test
```

## Development

```bash
python3 -m pytest -q
ruff check .
python3 scripts/validate_plugin.py
claude plugin validate --strict . && claude plugin validate --strict plugins/ways-of-working
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the ground rules and [docs/good-first-issues.md](docs/good-first-issues.md) for a place to start.

## Frequently asked questions

**Are there Claude Code skills for change requests and CAB records from a Terraform plan?**
Yes: `change-request-writer` reads the JSON from `terraform show -json plan.out`, counts what is added, changed, destroyed and replaced, flags IAM, security group, KMS, S3 policy and network resources for a reviewer, and drafts the record. It never runs `terraform`.

**What meeting note format does the action ledger need?**
Checkbox lines such as `- [ ] owner: action (due 2026-10-12)`, or lines starting `Action:`. The full table of accepted forms is in the skill. Prose is not read as an action.

**Does the exception register connect to Vanta or another GRC tool?**
No. Export the register to CSV, or keep it as a Markdown table, and the script reads that file.

**What is the difference between rfc-lifecycle, decision-log and adr-writer?**
An RFC is a proposal under review; `rfc-lifecycle` writes and lints it. An ADR records an architecture decision with its options; use `adr-writer` once the RFC is accepted. A decision log entry is a short record of an everyday operational decision with a review date; use `decision-log`.

**Will it rank who is slow to close actions or approve exceptions?**
No. Names appear where the files name them, sorted alphabetically, and are never counted or scored. `one-on-one-ledger` keeps one ledger per person with no ratings or comparisons, and refuses shared or repository paths unless told otherwise.

**Does focus-plan change my calendar?**
No. It reads a saved .ics export and never writes to a calendar or declines meetings. Recurrence rules it does not expand are listed rather than guessed.

**Can I run it on a schedule?**
Yes, without Claude Code: run a subcommand with `--as-of` and act on the exit code, which is 1 when something needs a person.

## Related repositories

| Repository | What it is |
|---|---|
| [claude-skills](https://github.com/basitalisandhu/claude-skills) | Every pack in one marketplace, including this one |
| [github-manager-skills](https://github.com/basitalisandhu/github-manager-skills) | Engineering manager reports from exported GitHub data: PR queue digest, iteration report, incident postmortem timeline |
| [repo-engineering-skills](https://github.com/basitalisandhu/repo-engineering-skills) | Repository audits and documentation, including `adr-miner` |
| [compliance-evidence-skills](https://github.com/basitalisandhu/compliance-evidence-skills) | Compliance evidence from saved exports, including change control evidence from GitHub |
| [aws-security-skills](https://github.com/basitalisandhu/aws-security-skills) | AWS security skills such as SCP guardrails and Security Hub triage |

More from the author: [github.com/basitalisandhu](https://github.com/basitalisandhu).

## Licence

MIT. See [LICENSE](LICENSE).
