# Good first issues

Small, well-specified pieces of work for a first contribution. Each is self-contained, comes with the test to add, and needs no account, token or network access: tests build their own inputs. Read [CONTRIBUTING.md](../CONTRIBUTING.md) first: standard library only, a test for every rule change, made-up names only, no rating of people, plain language without em-dashes.

To claim one, open an issue with the title below (or comment on the existing one) and say you are working on it. Run `python3 -m pytest -q`, `ruff check .` and `python3 scripts/validate_plugin.py` before opening the pull request.

## 1. change-request-writer: flag replacement of stateful resource types

**Labels:** good first issue, change-request-writer, python

**Context.** Replacing a database or a volume loses its data unless it is restored. The record lists every replace, but does not single out the stateful ones.

**Acceptance criteria.**
- A documented list of stateful types (for example `aws_db_instance`, `aws_rds_cluster`, `aws_ebs_volume`, `aws_efs_file_system`, `aws_dynamodb_table`) whose replace or destroy adds a "stateful" note to the Rollback section.
- A test with a replaced `aws_db_instance` and a replaced `aws_instance`, where only the first gets the note.

## 2. meeting-actions-ledger: read a "Decisions:" section as decision candidates

**Labels:** good first issue, meeting-actions-ledger, python

**Context.** Notes often have a "Decisions" list next to the actions. Those lines are candidates for `decision-log`.

**Acceptance criteria.**
- `--decisions` lists the items under a heading or line named "Decisions" as "candidate decisions (unconfirmed)", each with file and line, in both outputs.
- They are never counted as actions and never change the exit code.
- A test with one decisions section and one action.

## 3. risk-exception-register: expiry buckets for 30, 60 and 90 days

**Labels:** good first issue, risk-exception-register, python

**Context.** Quarterly reviews want to see what expires this month, next month and the month after.

**Acceptance criteria.**
- `--buckets 30,60,90` splits the "Expiring soon" section into one sub-list per bucket; without the option the output is unchanged.
- A test with rows expiring in 10, 45 and 80 days.

## 4. rfc-lifecycle: read reviewers and flag a review with none

**Labels:** good first issue, rfc-lifecycle, python

**Context.** An RFC in review with no named reviewer tends to stay there.

**Acceptance criteria.**
- A `missing-reviewers` finding when the status is review and the `reviewers` front matter field is empty or absent.
- A test for each case, and the rule added to the table in `SKILL.md`.

## 5. decision-log: list decisions due for review in the next N days

**Labels:** good first issue, decision-log, python

**Context.** Overdue reviews are flagged, but a team planning next month's review meeting wants what is coming up.

**Acceptance criteria.**
- `--due-within N` adds a "Due for review in the next N days" section (information only, no change to the exit code).
- A test with one decision due in 5 days and one in 50 days.
