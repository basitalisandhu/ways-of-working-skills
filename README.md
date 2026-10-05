# Claude Code skills for ways of working: change records, action ledgers, exception registers, RFCs and decision logs from files you already have

**A Claude Code plugin whose skills turn files a team already keeps (a Terraform plan, meeting notes, an exceptions spreadsheet, a folder of RFCs or decisions) into change records, follow-up ledgers and review agendas, with a tested standard-library script behind each one.**

ways-of-working-skills is a Claude Code plugin for the records that keep a team honest about its own process: what a change will do before the board approves it, which actions from last month's meetings are still open, which security exceptions have expired, which design proposals are stuck in review, and which operational decisions are due for a second look. These records usually live in files nobody re-reads. Each skill here reads those files with a small Python script, applies stated rules, cites the file and line behind every finding, and leaves decisions to people.

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

## Skills

| Skill | Triggers on | Script | Reads | What it produces |
|---|---|---|---|---|
| `change-request-writer` | "write the change request for this plan", "draft the CAB record", "what does this apply destroy?" | `change_request.py` | `terraform show -json` plan, or `gh pr view --json` export | add, change, destroy and replace counts by resource type; IAM, security group, KMS, S3 policy and network changes flagged as needs reviewer; a risk tier by a stated rule; a Markdown record with summary, scope, risk, rollback, verification, approvers and window placeholders |
| `meeting-actions-ledger` | "what is still open from our meetings?", "which actions are overdue?", "open actions for the next sync" | `meeting_ledger.py` | a folder of Markdown or text notes with `- [ ] owner: action (due YYYY-MM-DD)` or `Action:` lines | `ledger.md` and `ledger.json`: one row per action across meetings, carry-overs merged, overdue, ownerless and carried-three-times items flagged, each cited to file and line |
| `risk-exception-register` | "prepare the exception review", "what has expired in the register?", "which exceptions keep being renewed?" | `exception_register.py` | a CSV or Markdown table (id, system, control, reason, approver, granted, expires, compensating controls) | expired and expiring-in-30-days rows, missing approver or compensating control, approver equal to requester, repeated renewals, data problems, as a review agenda with blank decision lines |
| `rfc-lifecycle` | "draft an RFC for ...", "which RFCs are stuck in review?", "which review questions are unanswered?" | `rfc_lint.py` | a folder of RFC Markdown files with a status header | an RFC outline and security review checklist; a lint for status, unanswered `Q:` threads, open questions, missing decision date, missing sections and more than 14 days in review |
| `decision-log` | "write down that we decided ...", "which decisions are due for review?", "rebuild the decision index" | `decision_log.py` | a folder of one-file-per-decision Markdown records | an index table (`--index INDEX.md`), overdue reviews, broken, one-way or cyclic supersede links, missing fields |

Every script reads local files only, prints Markdown by default and JSON with `--json`, answers `--help`, and writes only to the path given with `--out` (or `--index`). Exit codes are the same across the pack: 0 nothing flagged, 1 something needs a person, 2 bad input. Where a date matters it is judged against `--as-of`, so a run can be repeated later with the same result.

## What this is not

- **Not a decision maker.** No skill approves a change, accepts a risk, renews an exception or picks a design. They lay out the facts and leave the decision lines blank.
- **Not a rating of people.** Owners, approvers and deciders appear by name where the files name them, and nowhere are they counted, ranked or scored. The skills tell Claude to decline such requests.
- **Not an integration.** No skill connects to ServiceNow, Jira, Vanta, a calendar, chat or a GRC platform. Export to a file first, or paste the Markdown where it is needed.
- **Not a replacement for review.** The RFC lint checks shape and dates; the review itself is still a conversation between people.

## How it fits with the other packs

- `change-request-writer` comes after `terraform-review` (claude-dev-skills) has reviewed the code, and before `github-change-control-evidence` (compliance-evidence-skills) evidences the change for an audit.
- `rfc-lifecycle` covers the proposal and its review; `adr-writer` (claude-dev-skills) writes the architecture decision record once an RFC is accepted; `adr-miner` (repo-engineering-skills) recovers old decisions from git history; `decision-log` is for everyday operational decisions that need neither.
- `risk-exception-register` tracks accepted exceptions with an expiry; `access-review-pack` (m365-governance-skills) is for access recertification and `security-hub-triage` (aws-security-skills) for a live findings backlog.

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

From a checkout, `python3 scripts/cli.py` is the same dispatcher.

## Security posture

- **Local, offline scripts.** The skill scripts read the file or folder you name. They import no network or subprocess module (the repository validator checks this) and write only to the `--out` or `--index` path you give.
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
No. Names appear where the files name them, sorted alphabetically, and are never counted or scored.

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
