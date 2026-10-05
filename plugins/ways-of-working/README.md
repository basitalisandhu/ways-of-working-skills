# ways-of-working

Ways-of-working skills that compute from files you already have, each with a tested standard-library Python script. Install with `/plugin marketplace add basitalisandhu/ways-of-working-skills` and then `/plugin install ways-of-working@ways-of-working-skills`. Skills appear as `/ways-of-working:<skill>`, and Claude also invokes them on its own when a request matches a skill's description.

| Skill | Script | Use it to |
|---|---|---|
| `change-request-writer` | `skills/change-request-writer/scripts/change_request.py` | draft a CAB-style change record from a Terraform plan JSON or a `gh pr view` export, with add, change, destroy and replace counts and IAM, security group, KMS, S3 policy and network changes flagged for a reviewer |
| `meeting-actions-ledger` | `skills/meeting-actions-ledger/scripts/meeting_ledger.py` | build one action ledger across a folder of meeting notes, merging carry-overs and flagging overdue, ownerless and repeatedly carried items |
| `risk-exception-register` | `skills/risk-exception-register/scripts/exception_register.py` | lint a register of security exceptions and risk acceptances for expiry, missing approver or compensating control and repeated renewals, and print a review agenda |
| `rfc-lifecycle` | `skills/rfc-lifecycle/scripts/rfc_lint.py` | write an RFC with a fixed outline and security review checklist, and lint a folder of RFCs for status, unanswered threads, open questions, decision dates and time in review |
| `decision-log` | `skills/decision-log/scripts/decision_log.py` | keep one file per operational decision, flag overdue reviews and broken supersede links, and render the index |
| `weekly-status-rollup` | `skills/weekly-status-rollup/scripts/weekly_status_rollup.py` | write one lead's weekly status note from saved `git log` text and `gh` exports, with shipped, in review, in progress, blocked and carried-over items and weeks carried counted from last week's note |
| `one-on-one-ledger` | `skills/one-on-one-ledger/scripts/one_on_one_ledger.py` | keep a private per-person 1:1 ledger from local notes: days since the last 1:1 against a cadence, open commitments on each side, repeated topics; refuses shared or repository paths |
| `focus-plan` | `skills/focus-plan/scripts/focus_plan.py` | report meeting load, back-to-back runs and the longest free blocks per day from a saved calendar export (.ics), and draft a focus plan |
| `risk-register-refresh` | `skills/risk-register-refresh/scripts/risk_register_refresh.py` | compare a CSV risk register with findings exports and last quarter's snapshot, and list unmapped findings, unsupported risks, changed values, overdue reviews and missing owners for the quarterly review |
| `vendor-scorecard` | `skills/vendor-scorecard/scripts/vendor_scorecard.py` | score vendors or tools on weighted criteria from a CSV, check whether the ranking survives each weight moved one step, and write a scorecard with a blank decision line |
| `working-agreement-check` | `skills/working-agreement-check/scripts/working_agreement_check.py` | check the rules in a team's written working agreement against saved branch protection, rulesets, CODEOWNERS and workflow exports, as met, not met or not checkable |
| `shift-handover` | `skills/shift-handover/scripts/shift_handover.py` | write an on-call or shift handover from incident and alert exports: open incidents by age, stale and ownerless items, noisy alerts and silences that expire |

Requirements: Python 3.10 or newer on `PATH` as `python3`. The scripts read local files only: no network access, no third-party packages. No skill rates or ranks people.
