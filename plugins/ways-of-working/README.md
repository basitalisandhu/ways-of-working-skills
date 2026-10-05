# ways-of-working

Ways-of-working skills that compute from files you already have, each with a tested standard-library Python script. Install with `/plugin marketplace add basitalisandhu/ways-of-working-skills` and then `/plugin install ways-of-working@ways-of-working-skills`. Skills appear as `/ways-of-working:<skill>`, and Claude also invokes them on its own when a request matches a skill's description.

| Skill | Script | Use it to |
|---|---|---|
| `change-request-writer` | `skills/change-request-writer/scripts/change_request.py` | draft a CAB-style change record from a Terraform plan JSON or a `gh pr view` export, with add, change, destroy and replace counts and IAM, security group, KMS, S3 policy and network changes flagged for a reviewer |
| `meeting-actions-ledger` | `skills/meeting-actions-ledger/scripts/meeting_ledger.py` | build one action ledger across a folder of meeting notes, merging carry-overs and flagging overdue, ownerless and repeatedly carried items |
| `risk-exception-register` | `skills/risk-exception-register/scripts/exception_register.py` | lint a register of security exceptions and risk acceptances for expiry, missing approver or compensating control and repeated renewals, and print a review agenda |
| `rfc-lifecycle` | `skills/rfc-lifecycle/scripts/rfc_lint.py` | write an RFC with a fixed outline and security review checklist, and lint a folder of RFCs for status, unanswered threads, open questions, decision dates and time in review |
| `decision-log` | `skills/decision-log/scripts/decision_log.py` | keep one file per operational decision, flag overdue reviews and broken supersede links, and render the index |

Requirements: Python 3.10 or newer on `PATH` as `python3`. The scripts read local files only: no network access, no third-party packages. No skill rates or ranks people.
