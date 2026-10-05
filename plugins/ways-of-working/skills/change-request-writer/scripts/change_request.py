#!/usr/bin/env python3
"""change_request.py: a CAB-style change record from a saved Terraform plan JSON or a saved gh pr view export.

Input (one JSON file, detected by shape):
  * Terraform plan: the output of `terraform show -json plan.out` (a top-level "resource_changes" list, or
    "format_version" with "terraform_version"). Each managed resource change is counted by its actions:
      ["create"] add, ["update"] change, ["delete"] destroy, ["delete", "create"] or ["create", "delete"] replace;
      ["no-op"] and ["read"] are not changes. Data sources (mode "data") are skipped.
  * Pull request: the output of `gh pr view N --json number,title,url,body,baseRefName,headRefName,files,additions,
    deletions,changedFiles,labels` (a top-level "number" with "files" or "title"). Files are listed with their line
    counts; a PR export has no add, change, destroy or replace counts, and the record says so.

Rules:
  * needs reviewer: a changed resource whose type falls in one of these classes, checked in this order (first match
    wins): iam (aws_iam_*, aws_organizations_policy*, *_iam_*, azurerm_role_*), security-group (aws_security_group*,
    aws_vpc_security_group_*, aws_network_acl*, azurerm_network_security_*, google_compute_firewall*), kms (aws_kms_*,
    google_kms_*, azurerm_key_vault_key*), s3-policy (aws_s3_bucket_policy, aws_s3_bucket_acl,
    aws_s3_bucket_public_access_block, aws_s3_account_public_access_block, aws_s3_bucket_ownership_controls), network
    (aws_vpc*, aws_subnet*, aws_route*, aws_internet_gateway*, aws_nat_gateway*, aws_ec2_transit_gateway*, aws_vpn_*,
    aws_networkfirewall_*, aws_lb_listener*, azurerm_virtual_network*, azurerm_subnet*, azurerm_route*,
    google_compute_network*, google_compute_subnetwork*, google_compute_route*). For a PR export the same classes are
    matched against file paths in the order s3-policy, iam, security-group, kms, network (words such as
    bucket_policy, iam, policy, security_group, firewall, kms, vpc, subnet, route, network) and marked "path match".
  * risk tier: high when any needs-reviewer item, destroy or replace exists; medium when the plan has in-place changes
    only; low when it only adds; none when nothing changes. A PR export is tiered high when any path matches, else
    "not tiered" (no plan).

Output: a Markdown change record on standard output (or --out FILE) with summary, scope, risk, rollback,
verification, approvers and maintenance window; --json prints the computed data instead. Nothing is approved, run or
sent anywhere. The output is deterministic for the same input and options.

Exit codes: 0 no needs-reviewer item, 1 at least one needs-reviewer item, 2 bad input (missing, empty or invalid
file, unknown shape, malformed resource change).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

PLACEHOLDER = "_to be completed by the change owner_"

TYPE_CLASSES: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    # (class, exact-or-prefix patterns ending in "*" for prefix, substrings)
    ("iam", ("aws_iam_*", "aws_organizations_policy*", "azurerm_role_*"), ("_iam_",)),
    (
        "security-group",
        (
            "aws_security_group*",
            "aws_vpc_security_group_*",
            "aws_network_acl*",
            "azurerm_network_security_*",
            "google_compute_firewall*",
        ),
        (),
    ),
    ("kms", ("aws_kms_*", "google_kms_*", "azurerm_key_vault_key*"), ()),
    (
        "s3-policy",
        (
            "aws_s3_bucket_policy",
            "aws_s3_bucket_acl",
            "aws_s3_bucket_public_access_block",
            "aws_s3_account_public_access_block",
            "aws_s3_bucket_ownership_controls",
        ),
        (),
    ),
    (
        "network",
        (
            "aws_vpc*",
            "aws_subnet*",
            "aws_route*",
            "aws_internet_gateway*",
            "aws_nat_gateway*",
            "aws_ec2_transit_gateway*",
            "aws_vpn_*",
            "aws_networkfirewall_*",
            "aws_lb_listener*",
            "azurerm_virtual_network*",
            "azurerm_subnet*",
            "azurerm_route*",
            "google_compute_network*",
            "google_compute_subnetwork*",
            "google_compute_route*",
        ),
        (),
    ),
]

PATH_CLASSES: list[tuple[str, re.Pattern[str]]] = [
    # s3-policy first so that "bucket_policy" is not taken by the generic "policy" word of the iam class.
    ("s3-policy", re.compile(r"bucket[_-]?policy|public[_-]?access[_-]?block|bucket[_-]?acl", re.I)),
    ("iam", re.compile(r"(?:^|[/_.-])(?:iam|scp|policy|policies|role|roles)(?:$|[/_.-])", re.I)),
    ("security-group", re.compile(r"security[_-]?group|firewall|(?:^|[/_.-])(?:sg|nacl)(?:$|[/_.-])", re.I)),
    ("kms", re.compile(r"(?:^|[/_.-])kms(?:$|[/_.-])", re.I)),
    (
        "network",
        re.compile(
            r"(?:^|[/_.-])(?:vpc|subnets?|routes?|route[_-]?tables?|network|transit[_-]?gateway|"
            r"nat)(?:$|[/_.-])",
            re.I,
        ),
    ),
]

ACTION_KIND = {
    ("create",): "add",
    ("update",): "change",
    ("delete",): "destroy",
    ("delete", "create"): "replace",
    ("create", "delete"): "replace",
    ("no-op",): "no-op",
    ("read",): "read",
}
KINDS = ("add", "change", "destroy", "replace")


class InputError(Exception):
    """Bad input: the message is printed and the script exits 2."""


def classify_type(rtype: str) -> str | None:
    for name, patterns, substrings in TYPE_CLASSES:
        for p in patterns:
            if (p.endswith("*") and rtype.startswith(p[:-1])) or rtype == p:
                return name
        if any(s in rtype for s in substrings):
            return name
    return None


def classify_path(path: str) -> str | None:
    for name, rx in PATH_CLASSES:
        if rx.search(path):
            return name
    return None


def load(path: Path) -> object:
    if not path.is_file():
        raise InputError(f"{path}: file not found")
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        raise InputError(f"{path}: file is empty")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(f"{path}: not valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}") from exc


def detect(data: object) -> str:
    if isinstance(data, dict):
        if "resource_changes" in data or ("format_version" in data and "terraform_version" in data):
            return "terraform-plan"
        if "number" in data and ("files" in data or "title" in data):
            return "pull-request"
    raise InputError(
        "input is neither a Terraform plan JSON (resource_changes) nor a gh pr view export (number with files or title)"
    )


def analyse_plan(data: dict) -> dict:
    changes = data.get("resource_changes") or []
    if not isinstance(changes, list):
        raise InputError("resource_changes is not a list")
    counts: Counter[str] = Counter()
    resources: list[dict] = []
    for i, rc in enumerate(changes):
        if not isinstance(rc, dict):
            raise InputError(f"resource_changes[{i}]: not an object")
        if rc.get("mode", "managed") == "data":
            continue
        actions = (rc.get("change") or {}).get("actions") if isinstance(rc.get("change"), dict) else None
        address, rtype = rc.get("address"), rc.get("type")
        if not isinstance(actions, list) or not actions or not isinstance(address, str) or not isinstance(rtype, str):
            raise InputError(f"resource_changes[{i}]: needs address, type and change.actions")
        kind = ACTION_KIND.get(tuple(actions))
        if kind is None:
            raise InputError(f"resource_changes[{i}] ({address}): unknown actions {actions}")
        if kind in ("no-op", "read"):
            counts["unchanged"] += 1
            continue
        counts[kind] += 1
        resources.append({"address": address, "type": rtype, "action": kind, "class": classify_type(rtype)})
    resources.sort(key=lambda r: (r["type"], r["address"]))
    by_type: dict[str, dict[str, int]] = {}
    for r in resources:
        by_type.setdefault(r["type"], {k: 0 for k in KINDS})[r["action"]] += 1
    needs = [r for r in resources if r["class"]]
    if needs or counts["destroy"] or counts["replace"]:
        tier = "high"
    elif counts["change"]:
        tier = "medium"
    elif counts["add"]:
        tier = "low"
    else:
        tier = "none"
    return {
        "source": "terraform-plan",
        "terraform_version": data.get("terraform_version"),
        "counts": {k: counts[k] for k in KINDS} | {"unchanged": counts["unchanged"]},
        "by_type": dict(sorted(by_type.items())),
        "resources": resources,
        "needs_reviewer": [
            {"item": r["address"], "class": r["class"], "action": r["action"], "basis": "type"} for r in needs
        ],
        "risk_tier": tier,
    }


def analyse_pr(data: dict) -> dict:
    files = data.get("files") or []
    if not isinstance(files, list):
        raise InputError("files is not a list")
    rows = []
    for i, f in enumerate(files):
        if not isinstance(f, dict) or not isinstance(f.get("path"), str):
            raise InputError(f"files[{i}]: needs a path")
        rows.append(
            {
                "path": f["path"],
                "additions": int(f.get("additions") or 0),
                "deletions": int(f.get("deletions") or 0),
                "class": classify_path(f["path"]),
            }
        )
    rows.sort(key=lambda r: r["path"])
    needs = [
        {"item": r["path"], "class": r["class"], "action": "edit", "basis": "path match"} for r in rows if r["class"]
    ]
    return {
        "source": "pull-request",
        "pr": {
            "number": data.get("number"),
            "title": data.get("title"),
            "url": data.get("url"),
            "base": data.get("baseRefName"),
            "head": data.get("headRefName"),
            "labels": sorted(lb.get("name", "") for lb in data.get("labels") or [] if isinstance(lb, dict)),
        },
        "counts": None,
        "files": rows,
        "lines": {"additions": sum(r["additions"] for r in rows), "deletions": sum(r["deletions"] for r in rows)},
        "needs_reviewer": needs,
        "risk_tier": "high" if needs else "not tiered",
    }


def render(rep: dict, title: str, change_id: str | None, window: str | None, input_name: str) -> str:
    out: list[str] = []
    pr = rep.get("pr")
    if not title:
        title = (pr or {}).get("title") or "infrastructure change"
    out.append(f"# Change record: {title}")
    out.append("")
    out.append("| Field | Value |")
    out.append("|---|---|")
    out.append(f"| Change ID | {change_id or PLACEHOLDER} |")
    out.append(f"| Generated from | `{input_name}` ({rep['source']}) |")
    if pr:
        ref = f"#{pr['number']}" + (f" {pr['url']}" if pr.get("url") else "")
        out.append(f"| Pull request | {ref} |")
    out.append(f"| Risk tier | {rep['risk_tier']} |")
    out.append(f"| Needs reviewer | {len(rep['needs_reviewer'])} item(s) |")
    out.append("")
    out.append("## Summary")
    out.append("")
    if rep["counts"] is not None:
        c = rep["counts"]
        out.append(
            f"Plan: {c['add']} to add, {c['change']} to change, {c['destroy']} to destroy, "
            f"{c['replace']} to replace ({c['unchanged']} unchanged, not listed)."
        )
    else:
        lines = rep["lines"]
        out.append(
            f"Pull request touches {len(rep['files'])} file(s), +{lines['additions']} -{lines['deletions']} "
            "lines. A PR export holds no add, change, destroy or replace counts: attach the plan JSON for them."
        )
    out.append("")
    out.append(f"Purpose: {PLACEHOLDER}")
    out.append("")
    out.append("## Scope")
    out.append("")
    if rep["counts"] is not None:
        if not rep["resources"]:
            out.append("No resource changes in this plan.")
        else:
            out.append("| Resource type | Add | Change | Destroy | Replace |")
            out.append("|---|---|---|---|---|")
            for rtype, c in rep["by_type"].items():
                out.append(f"| `{rtype}` | {c['add']} | {c['change']} | {c['destroy']} | {c['replace']} |")
            out.append("")
            out.append("| Address | Action |")
            out.append("|---|---|")
            for r in rep["resources"]:
                out.append(f"| `{r['address']}` | {r['action']} |")
    else:
        if not rep["files"]:
            out.append("No files in this export.")
        else:
            out.append("| File | + | - |")
            out.append("|---|---|---|")
            for f in rep["files"]:
                out.append(f"| `{f['path']}` | {f['additions']} | {f['deletions']} |")
    out.append("")
    out.append("## Risk")
    out.append("")
    out.append(
        f"Tier: **{rep['risk_tier']}** (rule: high when any needs-reviewer item, destroy or replace; medium "
        "for in-place changes only; low for additions only)."
    )
    out.append("")
    if rep["needs_reviewer"]:
        out.append("| Needs reviewer | Class | Action | Basis |")
        out.append("|---|---|---|---|")
        for n in rep["needs_reviewer"]:
            out.append(f"| `{n['item']}` | {n['class']} | {n['action']} | {n['basis']} |")
    else:
        out.append("No IAM, security group, KMS, S3 policy or network change found by the rules.")
    out.append("")
    out.append(f"Impact on users and services: {PLACEHOLDER}")
    out.append("")
    out.append("## Rollback")
    out.append("")
    gone = [r for r in rep.get("resources", []) if r["action"] in ("destroy", "replace")]
    out.append(f"Rollback plan: {PLACEHOLDER} (for example, revert the commit and apply the previous configuration).")
    if gone:
        out.append("")
        out.append(
            "These resources are destroyed or replaced; re-applying the old configuration creates new ones, so "
            "any data or identifiers they hold must be restored separately:"
        )
        out.append("")
        for r in gone:
            out.append(f"- `{r['address']}` ({r['action']})")
    out.append("")
    out.append("## Verification")
    out.append("")
    if rep["counts"] is not None:
        out.append("- After apply, a new `terraform plan` shows no changes.")
    else:
        out.append("- The merged commit's checks pass and the deployment it triggers completes.")
    for n in rep["needs_reviewer"]:
        out.append(f"- Reviewer confirms the {n['class']} change to `{n['item']}`.")
    out.append(f"- Service checks: {PLACEHOLDER}")
    out.append("")
    out.append("## Approvers")
    out.append("")
    out.append("| Role | Name | Decision | Date |")
    out.append("|---|---|---|---|")
    out.append("| Change owner |  |  |  |")
    if rep["needs_reviewer"]:
        for cls in sorted({n["class"] for n in rep["needs_reviewer"]}):
            out.append(f"| Reviewer for {cls} |  |  |  |")
    out.append("| Change advisory board |  |  |  |")
    out.append("")
    out.append("## Maintenance window")
    out.append("")
    out.append(window or PLACEHOLDER)
    out.append("")
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="change_request.py",
        description="Write a CAB-style change record from a saved Terraform plan JSON or gh pr view export.",
        epilog="Exit codes: 0 no needs-reviewer item, 1 at least one, 2 bad input.",
    )
    p.add_argument("input", help="plan JSON (terraform show -json plan.out > plan.json) or gh pr view --json export")
    p.add_argument(
        "--title", default="", help="title for the record (default: the PR title or 'infrastructure change')"
    )
    p.add_argument("--change-id", default=None, help="change ticket or reference to print (default: placeholder)")
    p.add_argument("--window", default=None, help="maintenance window text (default: placeholder)")
    p.add_argument("--json", action="store_true", help="print the computed data as JSON instead of Markdown")
    p.add_argument("--out", default=None, help="write the output to this file instead of standard output")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.input)
    try:
        data = load(path)
        kind = detect(data)
        rep = analyse_plan(data) if kind == "terraform-plan" else analyse_pr(data)
    except InputError as exc:
        print(f"change_request.py: {exc}", file=sys.stderr)
        return 2
    if args.json:
        text = json.dumps(rep, indent=2, sort_keys=True) + "\n"
    else:
        text = render(rep, args.title, args.change_id, args.window, path.name)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 1 if rep["needs_reviewer"] else 0


if __name__ == "__main__":
    sys.exit(main())
