#!/usr/bin/env python3
"""working_agreement_check.py: check a team's written working agreement against saved GitHub exports.

Inputs:
  * agreement: a Markdown file. Every list item (`-`, `*`, `+` or `1.`) outside code fences is read as one rule;
    other lines are ignored.
  * --protection FILE: the JSON from `gh api repos/<owner>/<repo>/branches/<branch>/protection`. A saved 404 body
    ({"message": "Branch not protected"}) is read as "no branch protection".
  * --rulesets FILE: JSON from `gh api repos/<owner>/<repo>/rulesets/<id>` (one ruleset, or a list of them), or from
    `gh api repos/<owner>/<repo>/rules/branches/<branch>` (a list of rules). The plain `.../rulesets` listing has no
    rules in it; rulesets without rules are reported and ignored. Only rulesets with enforcement "active" (or no
    enforcement field) whose ref_name conditions include the branch, ~DEFAULT_BRANCH or ~ALL are used.
  * --codeowners FILE: a CODEOWNERS file.
  * --workflows FILE: `gh workflow list --json name,path,state` JSON, or `gh workflow list` text (tab-separated name
    and state), or one workflow file name per line (read as active).
Any export can be left out; rules that need it are then reported as "not checkable from these exports".

Recognised rule patterns (a list item can match more than one; matching ignores case):
  * required-reviews: "<n> review(s)" or "<n> approval(s)", with n a digit or one to five, "a" or "an";
    met when branch protection or an active ruleset requires at least n approving reviews;
  * code-owner-review: "code owner(s)" with review, approve or approval; require_code_owner_reviews or the ruleset
    require_code_owner_review parameter;
  * branch-protected: "protected" or "branch protection"; met when the protection export is a protection object or an
    active ruleset applies to the branch;
  * ci-required: CI, checks, tests or build with pass, green or passing; met when required status checks are set and,
    if a workflow listing is given, at least one workflow is active;
  * ci-runs: "CI runs", "workflow" or "pipeline" with run or runs; met when the workflow listing has an active
    workflow;
  * no-force-push: "force push" or "force-push"; allow_force_pushes disabled or a non_fast_forward rule;
  * no-deletion: "delete" or "deletion" with branch, main or master; allow_deletions disabled or a deletion rule;
  * linear-history: "linear history"; required_linear_history or a required_linear_history rule;
  * signed-commits: "signed" or "signature"; required_signatures or a required_signatures rule;
  * admins-included: "admin" with included, too, also, apply or bypass; enforce_admins enabled;
  * stale-reviews-dismissed: "stale"; dismiss_stale_reviews or dismiss_stale_reviews_on_push;
  * conversation-resolution: "conversation" or "thread" with resolved; required_conversation_resolution or
    required_review_thread_resolution;
  * codeowners-covers: "codeowners" with a path (backticked, or a word containing "/" or a dot); met when the last
    matching CODEOWNERS pattern for that path names at least one owner. "everything", "all files" or "every file"
    checks for a catch-all pattern. "codeowners" with no path checks that the file has at least one owned pattern.
List items that match no pattern are listed as not recognised.

Output: a Markdown report on standard output (or --out FILE) with each rule as met, not met or not checkable, and the
evidence for each; --json prints the computed data instead. The output is deterministic for the same inputs.

Exit codes: 0 every recognised rule met (with --strict, nothing not checkable either), 1 a rule not met (or, with
--strict, not checkable), 2 bad input (agreement missing or empty, an export missing or not valid JSON).
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from pathlib import Path

NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
REVIEW_RE = re.compile(r"\b(\d+|one|two|three|four|five|an?)\s+(?:\w+\s+)?(?:reviews?|approvals?|reviewers?)\b", re.I)
PATH_RE = re.compile(r"`([^`]+)`|(\S*/\S*|\S+\.\w+)")
STATUSES = ("met", "not met", "not checkable")


class InputError(Exception):
    """Bad input: the message is printed and the script exits 2."""


def load_json(path: str | None, flag: str):
    if path is None:
        return None
    p = Path(path)
    if not p.is_file():
        raise InputError(f"{flag} {path}: file not found")
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise InputError(f"{flag} {path}: not valid JSON ({exc.msg} at line {exc.lineno})") from exc


def read_agreement(path: Path) -> list[tuple[int, str]]:
    if not path.is_file():
        raise InputError(f"{path}: file not found")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if not text.strip():
        raise InputError(f"{path}: file is empty")
    items, fenced = [], False
    for n, line in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(line):
            fenced = not fenced
            continue
        m = None if fenced else ITEM_RE.match(line)
        if m:
            items.append((n, m.group(1)))
    return items


def enabled(obj, name: str) -> bool | None:
    value = obj.get(name) if isinstance(obj, dict) else None
    if isinstance(value, dict):
        return bool(value.get("enabled"))
    return None if value is None else bool(value)


class Sources:
    def __init__(self, protection, rulesets, codeowners: str | None, workflows: str | None, branch: str):
        self.notes: list[str] = []
        self.branch = branch
        self.protection_given = protection is not None
        self.protection = None
        if isinstance(protection, dict) and "message" not in protection:
            self.protection = protection
        elif protection is not None:
            msg = protection.get("message", "") if isinstance(protection, dict) else "not an object"
            self.notes.append(f"branch protection export: {msg or 'not a protection object'}")
        self.rulesets_given = rulesets is not None
        self.rules: list[tuple[str, dict]] = []  # (source label, rule)
        self.ruleset_names: list[str] = []
        if rulesets is not None:
            self._load_rulesets(rulesets)
        self.codeowners = None if codeowners is None else parse_codeowners(codeowners)
        self.workflows = None if workflows is None else parse_workflows(workflows)

    def _load_rulesets(self, data) -> None:
        items = data if isinstance(data, list) else [data]
        if items and all(isinstance(i, dict) and "type" in i and "rules" not in i for i in items):
            for rule in items:
                label = f"rule from ruleset {rule.get('ruleset_id', '?')}"
                self.rules.append((label, rule))
            if items:
                self.ruleset_names.append("branch rules export")
            return
        for rs in items:
            if not isinstance(rs, dict):
                continue
            label = f"ruleset {rs.get('name', '?')!r} (id {rs.get('id', '?')})"
            if "rules" not in rs:
                self.notes.append(f"{label}: no rules in the export (save gh api .../rulesets/<id> for each one)")
                continue
            if rs.get("enforcement", "active") != "active":
                self.notes.append(f"{label}: enforcement is {rs.get('enforcement')!r}, not counted")
                continue
            if rs.get("target", "branch") != "branch" or not self._applies(rs):
                self.notes.append(f"{label}: does not target branch {self.branch!r}, not counted")
                continue
            self.ruleset_names.append(label)
            for rule in rs.get("rules") or []:
                if isinstance(rule, dict):
                    self.rules.append((label, rule))

    def _applies(self, rs: dict) -> bool:
        ref = (rs.get("conditions") or {}).get("ref_name") or {}
        include, exclude = ref.get("include"), ref.get("exclude") or []
        if include is None:
            return True
        names = {f"refs/heads/{self.branch}", self.branch}

        def hit(patterns: list[str]) -> bool:
            for p in patterns:
                if p in ("~ALL", "~DEFAULT_BRANCH") or any(fnmatch.fnmatchcase(n, p) for n in names):
                    return True
            return False

        return hit(include) and not hit(exclude)

    def rule_of(self, kind: str) -> list[tuple[str, dict]]:
        return [(label, r) for label, r in self.rules if r.get("type") == kind]

    @property
    def has_branch_source(self) -> bool:
        return self.protection_given or self.rulesets_given


def parse_codeowners(text: str) -> list[tuple[int, str, list[str]]]:
    rules = []
    for n, line in enumerate(text.splitlines(), start=1):
        line = line.split(" #", 1)[0].strip() if not line.lstrip().startswith("#") else ""
        if not line:
            continue
        parts = line.split()
        rules.append((n, parts[0], parts[1:]))
    return rules


def codeowners_regex(pattern: str) -> re.Pattern:
    anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
    body = pattern.strip("/")
    out = ""
    i = 0
    while i < len(body):
        if body.startswith("**", i):
            out += ".*"
            i += 2
        elif body[i] == "*":
            out += "[^/]*"
            i += 1
        elif body[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(body[i])
            i += 1
    prefix = "^" if anchored else "^(?:.*/)?"
    if pattern.endswith("/*"):
        suffix = "$"
    elif pattern.endswith("/"):
        suffix = "/.*$"
    else:
        suffix = "(?:/.*)?$"
    return re.compile(prefix + out + suffix)


def owner_of(rules: list[tuple[int, str, list[str]]], path: str) -> tuple[int, str, list[str]] | None:
    match = None
    for rule in rules:
        if codeowners_regex(rule[1]).match(path):
            match = rule
    return match


def parse_workflows(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, list):
        return [
            {"name": str(w.get("name") or w.get("path") or "?"), "state": str(w.get("state") or "active")}
            for w in data
            if isinstance(w, dict)
        ]
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        cells = [c.strip() for c in line.split("\t")]
        out.append({"name": cells[0], "state": cells[1] if len(cells) > 1 and cells[1] else "active"})
    return out


def result(status: str, evidence: list[str]) -> tuple[str, list[str]]:
    return status, evidence


def check_bool(src: Sources, prot_key: str, rule_type: str | None, want: bool = True) -> tuple[str, list[str]]:
    if not src.has_branch_source:
        return result("not checkable", ["needs --protection or --rulesets"])
    evidence = []
    met = False
    if src.protection is not None:
        value = enabled(src.protection, prot_key)
        evidence.append(f"branch protection: {prot_key} " + ("absent" if value is None else str(value).lower()))
        met = met or value is want
    elif src.protection_given:
        evidence.append("branch protection: none")
    if rule_type:
        found = src.rule_of(rule_type)
        evidence += [f"{label}: {rule_type} rule" for label, _ in found]
        met = met or bool(found)
    return result("met" if met else "not met", evidence)


def check(kind: str, arg, src: Sources) -> tuple[str, list[str]]:
    prot = src.protection or {}
    reviews = prot.get("required_pull_request_reviews") or {}
    pr_rules = src.rule_of("pull_request")
    if kind == "branch-protected":
        if not src.has_branch_source:
            return result("not checkable", ["needs --protection or --rulesets"])
        evidence = []
        if src.protection is not None:
            evidence.append("branch protection: present")
        elif src.protection_given:
            evidence.append("branch protection: none")
        evidence += [f"{n}: applies" for n in src.ruleset_names]
        return result("met" if src.protection is not None or src.ruleset_names else "not met", evidence)
    if kind == "required-reviews":
        if not src.has_branch_source:
            return result("not checkable", ["needs --protection or --rulesets"])
        counts, evidence = [], []
        if src.protection is not None:
            n = int(reviews.get("required_approving_review_count") or 0) if reviews else 0
            counts.append(n)
            evidence.append(f"branch protection: required_approving_review_count {n}")
        elif src.protection_given:
            evidence.append("branch protection: none")
        for label, rule in pr_rules:
            n = int((rule.get("parameters") or {}).get("required_approving_review_count") or 0)
            counts.append(n)
            evidence.append(f"{label}: required_approving_review_count {n}")
        return result("met" if counts and max(counts) >= arg else "not met", evidence)
    if kind == "code-owner-review":
        if not src.has_branch_source:
            return result("not checkable", ["needs --protection or --rulesets"])
        evidence, met = [], False
        if src.protection is not None:
            value = bool(reviews.get("require_code_owner_reviews"))
            evidence.append(f"branch protection: require_code_owner_reviews {str(value).lower()}")
            met = value
        for label, rule in pr_rules:
            value = bool((rule.get("parameters") or {}).get("require_code_owner_review"))
            evidence.append(f"{label}: require_code_owner_review {str(value).lower()}")
            met = met or value
        return result("met" if met else "not met", evidence or ["no pull request rules"])
    if kind == "stale-reviews-dismissed":
        if not src.has_branch_source:
            return result("not checkable", ["needs --protection or --rulesets"])
        evidence, met = [], False
        if src.protection is not None:
            value = bool(reviews.get("dismiss_stale_reviews"))
            evidence.append(f"branch protection: dismiss_stale_reviews {str(value).lower()}")
            met = value
        for label, rule in pr_rules:
            value = bool((rule.get("parameters") or {}).get("dismiss_stale_reviews_on_push"))
            evidence.append(f"{label}: dismiss_stale_reviews_on_push {str(value).lower()}")
            met = met or value
        return result("met" if met else "not met", evidence or ["no pull request rules"])
    if kind == "conversation-resolution":
        status, evidence = check_bool(src, "required_conversation_resolution", None)
        if status == "not checkable":
            return status, evidence
        for label, rule in pr_rules:
            value = bool((rule.get("parameters") or {}).get("required_review_thread_resolution"))
            evidence.append(f"{label}: required_review_thread_resolution {str(value).lower()}")
            if value:
                status = "met"
        return result(status, evidence)
    if kind == "ci-required":
        if not src.has_branch_source:
            return result("not checkable", ["needs --protection or --rulesets"])
        checks, evidence = [], []
        if src.protection is not None:
            rsc = prot.get("required_status_checks") or {}
            names = list(rsc.get("contexts") or []) + [
                c.get("context", "") for c in rsc.get("checks") or [] if isinstance(c, dict)
            ]
            names = sorted(set(n for n in names if n))
            checks += names
            evidence.append("branch protection: required status checks " + (", ".join(names) or "none"))
        elif src.protection_given:
            evidence.append("branch protection: none")
        for label, rule in src.rule_of("required_status_checks"):
            params = (rule.get("parameters") or {}).get("required_status_checks") or []
            names = sorted(c.get("context", "") for c in params if isinstance(c, dict))
            checks += names
            evidence.append(f"{label}: required status checks " + (", ".join(names) or "none"))
        met = bool(checks)
        if src.workflows is not None:
            active = [w["name"] for w in src.workflows if w["state"].lower() == "active"]
            evidence.append("workflows active: " + (", ".join(active) or "none"))
            met = met and bool(active)
        return result("met" if met else "not met", evidence)
    if kind == "ci-runs":
        if src.workflows is None:
            return result("not checkable", ["needs --workflows"])
        active = [w["name"] for w in src.workflows if w["state"].lower() == "active"]
        others = [f"{w['name']} ({w['state']})" for w in src.workflows if w["state"].lower() != "active"]
        evidence = ["workflows active: " + (", ".join(active) or "none")]
        if others:
            evidence.append("workflows not active: " + ", ".join(others))
        return result("met" if active else "not met", evidence)
    if kind == "no-force-push":
        return check_bool(src, "allow_force_pushes", "non_fast_forward", want=False)
    if kind == "no-deletion":
        return check_bool(src, "allow_deletions", "deletion", want=False)
    if kind == "linear-history":
        return check_bool(src, "required_linear_history", "required_linear_history")
    if kind == "signed-commits":
        return check_bool(src, "required_signatures", "required_signatures")
    if kind == "admins-included":
        if src.protection is None:
            return result("not checkable" if not src.protection_given else "not met", ["needs --protection"])
        return check_bool(src, "enforce_admins", None)
    if kind == "codeowners-covers":
        if src.codeowners is None:
            return result("not checkable", ["needs --codeowners"])
        if arg == "*":
            catch_all = [r for r in src.codeowners if r[1] in ("*", "/*", "**", "/**") and r[2]]
            if catch_all:
                n, pattern, owners = catch_all[-1]
                return result("met", [f"CODEOWNERS line {n}: {pattern} {' '.join(owners)}"])
            return result("not met", ["CODEOWNERS has no catch-all pattern (*) with an owner"])
        if arg is None:
            owned = [r for r in src.codeowners if r[2]]
            return result("met" if owned else "not met", [f"CODEOWNERS: {len(owned)} pattern(s) with owners"])
        probe = arg.lstrip("/")
        probe = probe.rstrip("/") + "/file" if arg.endswith("/") else probe
        match = owner_of(src.codeowners, probe)
        if match is None:
            return result("not met", [f"no CODEOWNERS pattern matches {arg}"])
        n, pattern, owners = match
        if not owners:
            return result("not met", [f"CODEOWNERS line {n}: {pattern} has no owner (last match wins)"])
        return result("met", [f"CODEOWNERS line {n}: {pattern} {' '.join(owners)}"])
    raise AssertionError(kind)


def recognise(text: str) -> list[tuple[str, object]]:
    t = text.lower()
    found: list[tuple[str, object]] = []
    m = REVIEW_RE.search(text)
    if m and "stale" not in t:
        found.append(("required-reviews", NUMBERS.get(m.group(1).lower()) or int(m.group(1))))
    if re.search(r"code ?owners?\b", t) and re.search(r"\b(review|reviews|approve|approves|approval)\b", t):
        found.append(("code-owner-review", None))
    elif "codeowners" in t:
        if re.search(r"\b(everything|all files|every file)\b", t):
            found.append(("codeowners-covers", "*"))
        else:
            paths = []
            after = text[t.index("codeowners") + len("codeowners") :]
            before = text[: t.index("codeowners")]
            for m in PATH_RE.finditer(after + " " + before):
                value = (m.group(1) or m.group(2) or "").strip(".,;:()\"'")
                if value and value.lower() != "codeowners" and value not in paths:
                    paths.append(value)
            found += [("codeowners-covers", p) for p in paths] or [("codeowners-covers", None)]
    if re.search(r"\bprotected\b|branch protection", t):
        found.append(("branch-protected", None))
    if re.search(r"\b(ci|checks?|tests?|builds?|status checks?)\b", t) and re.search(
        r"\b(pass|passes|passing|green)\b", t
    ):
        found.append(("ci-required", None))
    elif re.search(r"\b(ci|workflows?|pipelines?)\b.*\bruns?\b", t):
        found.append(("ci-runs", None))
    if re.search(r"force[- ]push", t):
        found.append(("no-force-push", None))
    if re.search(r"\b(delete|deleted|deletion)\b", t) and re.search(r"\b(branch|main|master)\b", t):
        found.append(("no-deletion", None))
    if "linear history" in t:
        found.append(("linear-history", None))
    if re.search(r"\bsigned\b|signature", t):
        found.append(("signed-commits", None))
    if re.search(r"\badmins?\b|administrators?", t) and re.search(
        r"\b(include|included|too|also|apply|applies|bypass)\b", t
    ):
        found.append(("admins-included", None))
    if "stale" in t:
        found.append(("stale-reviews-dismissed", None))
    if re.search(r"\b(conversations?|threads?)\b", t) and "resolved" in t:
        found.append(("conversation-resolution", None))
    return found


def analyse(items: list[tuple[int, str]], src: Sources, strict: bool) -> dict:
    checks, unrecognised = [], []
    for line, text in items:
        kinds = recognise(text)
        if not kinds:
            unrecognised.append({"line": line, "text": text})
            continue
        for kind, arg in kinds:
            status, evidence = check(kind, arg, src)
            label = kind if arg is None else f"{kind} {arg}"
            checks.append({"line": line, "rule": text, "check": label, "status": status, "evidence": evidence})
    counts = {s: sum(1 for c in checks if c["status"] == s) for s in STATUSES}
    needs_person = counts["not met"] > 0 or (strict and counts["not checkable"] > 0)
    return {
        "branch": src.branch,
        "exports": {
            "protection": src.protection_given,
            "rulesets": src.rulesets_given,
            "codeowners": src.codeowners is not None,
            "workflows": src.workflows is not None,
        },
        "rulesets_counted": src.ruleset_names,
        "notes": src.notes,
        "checks": checks,
        "counts": counts,
        "unrecognised": unrecognised,
        "strict": strict,
        "needs_person": needs_person,
    }


def render(rep: dict, source: str) -> str:
    c = rep["counts"]
    given = [k for k, v in rep["exports"].items() if v]
    out = [f"# Working agreement check: {source}", ""]
    out.append(
        f"Branch {rep['branch']}. Exports read: {', '.join(given) or 'none'}. {len(rep['checks'])} check(s): "
        f"{c['met']} met, {c['not met']} not met, {c['not checkable']} not checkable from these exports."
    )
    out.append("")
    titles = {"not met": "Not met", "not checkable": "Not checkable from these exports", "met": "Met"}
    for status in ("not met", "not checkable", "met"):
        rows = [x for x in rep["checks"] if x["status"] == status]
        if not rows:
            continue
        out += [f"## {titles[status]} ({len(rows)})", ""]
        for x in rows:
            out.append(f'- Line {x["line"]}: "{x["rule"]}" [{x["check"]}]')
            out += [f"  - {e}" for e in x["evidence"]]
        out.append("")
    if rep["unrecognised"]:
        out += [f"## Not recognised ({len(rep['unrecognised'])})", ""]
        out.append("These lines match no known pattern; check them by hand.")
        out.append("")
        out += [f'- Line {u["line"]}: "{u["text"]}"' for u in rep["unrecognised"]]
        out.append("")
    if rep["notes"]:
        out += ["## Notes on the exports", ""]
        out += [f"- {n}" for n in rep["notes"]]
        out.append("")
    if not rep["checks"] and not rep["unrecognised"]:
        out += ["No list items found in the agreement.", ""]
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="working_agreement_check.py",
        description="Check the rules in a team's written working agreement against saved GitHub exports: branch "
        "protection, rulesets, CODEOWNERS and the workflow list.",
        epilog="Exit codes: 0 every recognised rule met, 1 a rule not met (or not checkable with --strict), "
        "2 bad input.",
    )
    p.add_argument("agreement", help="the working agreement, Markdown; each list item is one rule")
    p.add_argument("--protection", help="JSON from gh api repos/<o>/<r>/branches/<branch>/protection")
    p.add_argument("--rulesets", help="JSON from gh api repos/<o>/<r>/rulesets/<id> or .../rules/branches/<branch>")
    p.add_argument("--codeowners", help="the CODEOWNERS file")
    p.add_argument("--workflows", help="gh workflow list --json name,path,state output, or one workflow per line")
    p.add_argument("--branch", default="main", help="branch the agreement is about (default main)")
    p.add_argument("--strict", action="store_true", help="exit 1 when a rule is not checkable from the exports")
    p.add_argument("--json", action="store_true", help="print the computed data as JSON instead of Markdown")
    p.add_argument("--out", default=None, help="write the output to this file instead of standard output")
    return p


def read_text(path: str | None, flag: str) -> str | None:
    if path is None:
        return None
    p = Path(path)
    if not p.is_file():
        raise InputError(f"{flag} {path}: file not found")
    return p.read_text(encoding="utf-8-sig", errors="replace")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        path = Path(args.agreement)
        items = read_agreement(path)
        src = Sources(
            load_json(args.protection, "--protection"),
            load_json(args.rulesets, "--rulesets"),
            read_text(args.codeowners, "--codeowners"),
            read_text(args.workflows, "--workflows"),
            args.branch,
        )
        rep = analyse(items, src, args.strict)
    except InputError as exc:
        print(f"working_agreement_check.py: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(rep, indent=2, sort_keys=True) + "\n" if args.json else render(rep, path.name)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 1 if rep["needs_person"] else 0


if __name__ == "__main__":
    sys.exit(main())
