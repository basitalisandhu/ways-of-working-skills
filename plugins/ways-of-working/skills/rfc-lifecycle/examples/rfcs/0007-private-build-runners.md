---
title: Move build runners to a private subnet
status: review
authors: platform team
reviewers: security team, networking
created: 2026-09-01
review-started: 2026-09-10
decision-date:
superseded-by:
---

## Problem
Build runners have public IP addresses.

## Proposal
Move them to a private subnet behind a NAT gateway.

## Security review
Q: What happens to jobs that need the public package mirror?
A: They go through the NAT gateway; see Rollout.
Q: Who owns the NAT gateway costs?

## Open questions
- Do we keep one public runner for the release job?
