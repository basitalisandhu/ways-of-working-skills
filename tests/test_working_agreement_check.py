"""Tests for working_agreement_check.py. Agreements and gh exports are synthetic files written in each test."""

from __future__ import annotations

import json

from conftest import load_script, run_json, run_main, write_files

mod = load_script("working-agreement-check", "working_agreement_check.py")

AGREEMENT = """
    # Example team working agreement

    How we work together.

    - PRs need one review before merge.
    - `main` is protected: no force pushes.
    - CI must pass before merge.
    - CODEOWNERS covers `src/` and `infra/`.
    - Code owners must approve changes to their paths.
    - We pair on Fridays.

    ```text
    - this list item is inside a code fence and is ignored
    ```
    """

PROTECTION = {
    "required_status_checks": {"strict": True, "contexts": ["test"], "checks": [{"context": "test", "app_id": 1}]},
    "required_pull_request_reviews": {
        "dismiss_stale_reviews": False,
        "require_code_owner_reviews": False,
        "required_approving_review_count": 1,
    },
    "enforce_admins": {"enabled": False},
    "allow_force_pushes": {"enabled": False},
    "allow_deletions": {"enabled": False},
}

CODEOWNERS = """
    # example owners
    *          @example/platform
    /src/      @example/app
    /infra/
    """

WORKFLOWS = [{"name": "ci", "path": ".github/workflows/ci.yml", "state": "active"}]


def files(tmp_path, agreement=AGREEMENT, protection=PROTECTION, codeowners=CODEOWNERS, workflows=WORKFLOWS, extra=None):
    spec = {"agreement.md": agreement}
    if protection is not None:
        spec["protection.json"] = json.dumps(protection)
    if codeowners is not None:
        spec["CODEOWNERS"] = codeowners
    if workflows is not None:
        spec["workflows.json"] = json.dumps(workflows)
    spec.update(extra or {})
    write_files(tmp_path, spec)
    args = [str(tmp_path / "agreement.md")]
    for flag, name in (
        ("--protection", "protection.json"),
        ("--codeowners", "CODEOWNERS"),
        ("--workflows", "workflows.json"),
    ):
        if name in spec:
            args += [flag, str(tmp_path / name)]
    return args


def by_check(rep: dict) -> dict[str, str]:
    return {c["check"]: c["status"] for c in rep["checks"]}


def test_rules_map_to_checks_and_unmet_ones_exit_1(tmp_path):
    rc, rep = run_json(mod, files(tmp_path))
    assert rc == 1
    assert by_check(rep) == {
        "required-reviews 1": "met",
        "branch-protected": "met",
        "no-force-push": "met",
        "ci-required": "met",
        "codeowners-covers src/": "met",
        "codeowners-covers infra/": "not met",
        "code-owner-review": "not met",
    }
    assert rep["unrecognised"] == [{"line": 10, "text": "We pair on Fridays."}]
    assert rep["counts"] == {"met": 5, "not met": 2, "not checkable": 0}


def test_review_count_from_a_ruleset_and_reviews_too_few(tmp_path):
    agreement = "- PRs need two approvals.\n- Code owners review their paths.\n"
    ruleset = {
        "id": 7,
        "name": "main rules",
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {
                "type": "pull_request",
                "parameters": {"required_approving_review_count": 2, "require_code_owner_review": True},
            },
        ],
    }
    args = files(tmp_path, agreement=agreement, extra={"ruleset.json": json.dumps(ruleset)})
    rc, rep = run_json(mod, [*args, "--rulesets", str(tmp_path / "ruleset.json")])
    assert rc == 0
    assert by_check(rep) == {"required-reviews 2": "met", "code-owner-review": "met"}
    rc, rep = run_json(mod, files(tmp_path, agreement=agreement))
    assert rc == 1 and by_check(rep)["required-reviews 2"] == "not met"


def test_inactive_or_other_branch_rulesets_are_not_counted(tmp_path):
    agreement = "- main is protected.\n"
    rulesets = [
        {"id": 1, "name": "draft", "enforcement": "evaluate", "rules": [{"type": "deletion"}]},
        {
            "id": 2,
            "name": "release",
            "enforcement": "active",
            "conditions": {"ref_name": {"include": ["refs/heads/release/*"], "exclude": []}},
            "rules": [],
        },
        {"id": 3, "name": "listing only", "enforcement": "active"},
    ]
    args = files(
        tmp_path,
        agreement=agreement,
        protection={"message": "Branch not protected", "status": "404"},
        extra={"rulesets.json": json.dumps(rulesets)},
    )
    rc, rep = run_json(mod, [*args, "--rulesets", str(tmp_path / "rulesets.json")])
    assert rc == 1
    assert by_check(rep) == {"branch-protected": "not met"}
    assert len(rep["notes"]) == 4 and "Branch not protected" in rep["notes"][0]


def test_not_checkable_without_exports_and_strict(tmp_path):
    agreement = "- CI must be green.\n- CI runs on every pull request.\n- CODEOWNERS covers everything.\n"
    args = files(tmp_path, agreement=agreement, protection=None, codeowners=None, workflows=None)
    rc, rep = run_json(mod, args)
    assert rc == 0
    assert set(by_check(rep).values()) == {"not checkable"}
    assert run_main(mod, [*args, "--strict"])[0] == 1


def test_workflow_text_listing_and_catch_all_codeowners(tmp_path):
    agreement = "- CI runs on every pull request.\n- CODEOWNERS covers everything.\n"
    args = files(
        tmp_path,
        agreement=agreement,
        workflows=None,
        extra={"wf.txt": "ci\tdisabled_manually\t11\nnightly\tdisabled_manually\t12\n"},
    )
    rc, rep = run_json(mod, [*args, "--workflows", str(tmp_path / "wf.txt")])
    assert rc == 1
    assert by_check(rep) == {"ci-runs": "not met", "codeowners-covers *": "met"}


def test_bad_inputs_exit_2(tmp_path):
    assert run_main(mod, files(tmp_path, agreement=""))[0] == 2
    args = files(tmp_path, extra={"broken.json": "{not json"})
    rc, _, err = run_main(mod, [args[0], "--protection", str(tmp_path / "broken.json")])
    assert rc == 2 and "not valid JSON" in err
    assert run_main(mod, [args[0], "--codeowners", str(tmp_path / "nope")])[0] == 2
    assert run_main(mod, [str(tmp_path / "missing.md")])[0] == 2


def test_golden_markdown(tmp_path):
    rc, out, _ = run_main(mod, files(tmp_path))
    assert rc == 1
    lines = out.splitlines()
    assert lines[:8] == [
        "# Working agreement check: agreement.md",
        "",
        "Branch main. Exports read: protection, codeowners, workflows. 7 check(s): 5 met, 2 not met, "
        "0 not checkable from these exports.",
        "",
        "## Not met (2)",
        "",
        '- Line 8: "CODEOWNERS covers `src/` and `infra/`." [codeowners-covers infra/]',
        "  - CODEOWNERS line 4: /infra/ has no owner (last match wins)",
    ]
    assert "  - branch protection: allow_force_pushes false" in lines
    assert '- Line 10: "We pair on Fridays."' in lines
    assert "ignored" not in out


def test_help_exits_0():
    rc, out, _ = run_main(mod, ["--help"])
    assert rc == 0 and out.startswith("usage: working_agreement_check.py")
