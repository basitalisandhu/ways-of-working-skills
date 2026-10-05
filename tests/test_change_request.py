"""Tests for change_request.py. Inputs are synthetic plan JSON and gh pr view exports built in each test."""

from __future__ import annotations

import json

from conftest import load_script, run_json, run_main

mod = load_script("change-request-writer", "change_request.py")


def rc_(address: str, rtype: str, actions: list[str], mode: str = "managed") -> dict:
    return {
        "address": address,
        "mode": mode,
        "type": rtype,
        "name": address.split(".")[-1],
        "change": {"actions": actions},
    }


def plan(tmp_path, changes: list[dict], name: str = "plan.json"):
    p = tmp_path / name
    p.write_text(
        json.dumps({"format_version": "1.2", "terraform_version": "1.9.5", "resource_changes": changes}),
        encoding="utf-8",
    )
    return p


SAMPLE = [
    rc_("aws_iam_role.ci", "aws_iam_role", ["update"]),
    rc_("aws_db_instance.main", "aws_db_instance", ["delete", "create"]),
    rc_("aws_s3_bucket.logs", "aws_s3_bucket", ["create"]),
    rc_("aws_s3_bucket_policy.logs", "aws_s3_bucket_policy", ["create"]),
    rc_("aws_security_group.web", "aws_security_group", ["update"]),
    rc_("aws_vpc_security_group_ingress_rule.https", "aws_vpc_security_group_ingress_rule", ["create"]),
    rc_("aws_kms_key.data", "aws_kms_key", ["update"]),
    rc_("aws_route_table.private", "aws_route_table", ["delete"]),
    rc_("aws_instance.old", "aws_instance", ["delete"]),
    rc_("aws_cloudwatch_log_group.app", "aws_cloudwatch_log_group", ["no-op"]),
    rc_("data.aws_caller_identity.me", "aws_caller_identity", ["read"], mode="data"),
]


def test_counts_add_change_destroy_replace(tmp_path):
    rc, rep = run_json(mod, [str(plan(tmp_path, SAMPLE))])
    assert rc == 1
    assert rep["counts"] == {"add": 3, "change": 3, "destroy": 2, "replace": 1, "unchanged": 1}
    assert rep["by_type"]["aws_instance"] == {"add": 0, "change": 0, "destroy": 1, "replace": 0}
    assert rep["risk_tier"] == "high"


def test_flags_iam_security_group_kms_s3_policy_and_network(tmp_path):
    _, rep = run_json(mod, [str(plan(tmp_path, SAMPLE))])
    flagged = {n["item"]: n["class"] for n in rep["needs_reviewer"]}
    assert flagged == {
        "aws_iam_role.ci": "iam",
        "aws_s3_bucket_policy.logs": "s3-policy",
        "aws_security_group.web": "security-group",
        "aws_vpc_security_group_ingress_rule.https": "security-group",
        "aws_kms_key.data": "kms",
        "aws_route_table.private": "network",
    }
    assert "aws_s3_bucket.logs" not in flagged and "aws_instance.old" not in flagged


def test_unflagged_plan_exits_0_and_tiers_by_rule(tmp_path):
    rc, rep = run_json(mod, [str(plan(tmp_path, [rc_("aws_s3_bucket.a", "aws_s3_bucket", ["create"])]))])
    assert (rc, rep["risk_tier"]) == (0, "low")
    rc, rep = run_json(mod, [str(plan(tmp_path, [rc_("aws_instance.a", "aws_instance", ["update"])]))])
    assert (rc, rep["risk_tier"]) == (0, "medium")
    rc, rep = run_json(mod, [str(plan(tmp_path, [rc_("aws_instance.a", "aws_instance", ["delete"])]))])
    assert (rc, rep["risk_tier"]) == (0, "high")


def test_empty_plan_gives_a_record_with_no_changes(tmp_path):
    rc, out, _ = run_main(mod, [str(plan(tmp_path, []))])
    assert rc == 0
    assert "Plan: 0 to add, 0 to change, 0 to destroy, 0 to replace" in out
    assert "No resource changes in this plan." in out


def test_empty_file_and_invalid_json_exit_2(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    rc, _, err = run_main(mod, [str(empty)])
    assert rc == 2 and "file is empty" in err
    broken = tmp_path / "broken.json"
    broken.write_text('{"resource_changes": [', encoding="utf-8")
    rc, _, err = run_main(mod, [str(broken)])
    assert rc == 2 and "not valid JSON" in err
    rc, _, err = run_main(mod, [str(tmp_path / "missing.json")])
    assert rc == 2 and "file not found" in err


def test_malformed_resource_change_and_unknown_shape_exit_2(tmp_path):
    p = plan(tmp_path, [{"address": "aws_iam_role.x", "type": "aws_iam_role"}])
    rc, _, err = run_main(mod, [str(p)])
    assert rc == 2 and "resource_changes[0]" in err and "change.actions" in err
    other = tmp_path / "other.json"
    other.write_text('{"hello": "world"}', encoding="utf-8")
    rc, _, err = run_main(mod, [str(other)])
    assert rc == 2 and "neither a Terraform plan" in err


def test_pr_export_lists_files_and_flags_by_path(tmp_path):
    pr = tmp_path / "pr.json"
    pr.write_text(
        json.dumps(
            {
                "number": 42,
                "title": "Tighten runner egress",
                "url": "https://github.com/example-org/infra/pull/42",
                "baseRefName": "main",
                "headRefName": "egress",
                "labels": [{"name": "infra"}],
                "files": [
                    {"path": "modules/network/vpc.tf", "additions": 10, "deletions": 2},
                    {"path": "iam/ci-role.tf", "additions": 4, "deletions": 4},
                    {"path": "buckets/logs_bucket_policy.json", "additions": 3, "deletions": 0},
                    {"path": "README.md", "additions": 1, "deletions": 1},
                ],
            }
        ),
        encoding="utf-8",
    )
    rc, rep = run_json(mod, [str(pr)])
    assert rc == 1
    assert rep["counts"] is None and rep["lines"] == {"additions": 18, "deletions": 7}
    assert {n["item"]: n["class"] for n in rep["needs_reviewer"]} == {
        "modules/network/vpc.tf": "network",
        "iam/ci-role.tf": "iam",
        "buckets/logs_bucket_policy.json": "s3-policy",
    }
    rc, out, _ = run_main(mod, [str(pr)])
    assert "# Change record: Tighten runner egress" in out
    assert "A PR export holds no add, change, destroy or replace counts" in out


def test_out_writes_only_the_named_file(tmp_path):
    p = plan(tmp_path, [rc_("aws_s3_bucket.a", "aws_s3_bucket", ["create"])])
    target = tmp_path / "out" / "record.md"
    target.parent.mkdir()
    rc, out, _ = run_main(mod, [str(p), "--out", str(target)])
    assert rc == 0 and out == ""
    assert sorted(x.name for x in target.parent.iterdir()) == ["record.md"]


GOLDEN = """\
# Change record: Rotate CI role

| Field | Value |
|---|---|
| Change ID | CHG-0042 |
| Generated from | `plan.json` (terraform-plan) |
| Risk tier | high |
| Needs reviewer | 1 item(s) |

## Summary

Plan: 0 to add, 1 to change, 0 to destroy, 1 to replace (0 unchanged, not listed).

Purpose: _to be completed by the change owner_

## Scope

| Resource type | Add | Change | Destroy | Replace |
|---|---|---|---|---|
| `aws_db_instance` | 0 | 0 | 0 | 1 |
| `aws_iam_role` | 0 | 1 | 0 | 0 |

| Address | Action |
|---|---|
| `aws_db_instance.main` | replace |
| `aws_iam_role.ci` | change |

## Risk

Tier: **high** (rule: high when any needs-reviewer item, destroy or replace; medium for in-place changes only; \
low for additions only).

| Needs reviewer | Class | Action | Basis |
|---|---|---|---|
| `aws_iam_role.ci` | iam | change | type |

Impact on users and services: _to be completed by the change owner_

## Rollback

Rollback plan: _to be completed by the change owner_ (for example, revert the commit and apply the previous \
configuration).

These resources are destroyed or replaced; re-applying the old configuration creates new ones, so any data or \
identifiers they hold must be restored separately:

- `aws_db_instance.main` (replace)

## Verification

- After apply, a new `terraform plan` shows no changes.
- Reviewer confirms the iam change to `aws_iam_role.ci`.
- Service checks: _to be completed by the change owner_

## Approvers

| Role | Name | Decision | Date |
|---|---|---|---|
| Change owner |  |  |  |
| Reviewer for iam |  |  |  |
| Change advisory board |  |  |  |

## Maintenance window

2026-10-12 22:00 to 23:00 UTC
"""


def test_golden_markdown_record(tmp_path):
    p = plan(
        tmp_path,
        [
            rc_("aws_iam_role.ci", "aws_iam_role", ["update"]),
            rc_("aws_db_instance.main", "aws_db_instance", ["create", "delete"]),
        ],
    )
    rc, out, _ = run_main(
        mod,
        [str(p), "--title", "Rotate CI role", "--change-id", "CHG-0042", "--window", "2026-10-12 22:00 to 23:00 UTC"],
    )
    assert rc == 1
    assert out == GOLDEN
