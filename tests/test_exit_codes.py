"""The pack's exit-code convention, checked for every skill: 0 nothing flagged, 1 something needs a person, 2 bad
input (3 only for a refused path). Each SKILL.md states it, each script's docstring states it, and each script exits
2 on an unknown option and 0 on --help."""
from __future__ import annotations

import re

import pytest
from conftest import ROOT, load_script, run_main

SKILLS = sorted((ROOT / "plugins" / "ways-of-working" / "skills").glob("*/SKILL.md"))
CONVENTION = re.compile(r"Exit codes[^:\n]*: 0 [^\n]*?, 1 [^\n]*?, 2 bad input")


def scripts():
    for skill_md in SKILLS:
        for path in sorted((skill_md.parent / "scripts").glob("*.py")):
            yield skill_md.parent.name, path.name


def test_every_skill_is_covered():
    assert len(SKILLS) == 13


@pytest.mark.parametrize("skill_md", SKILLS, ids=lambda p: p.parent.name)
def test_skill_md_states_the_convention(skill_md):
    text = skill_md.read_text(encoding="utf-8")
    assert CONVENTION.search(text), f"{skill_md.parent.name}: no 'Exit codes: 0 ..., 1 ..., 2 bad input' line"
    assert "differs from the rest of the pack" not in text


@pytest.mark.parametrize(("skill", "name"), list(scripts()))
def test_script_docstring_help_and_bad_option(skill, name):
    mod = load_script(skill, name)
    doc = " ".join((mod.__doc__ or "").split())
    assert re.search(r"Exit codes[^:]*: 0 .*?, 1 .*?, 2 bad input", doc), f"{name}: docstring lacks the convention"
    assert run_main(mod, ["--help"])[0] == 0
    assert run_main(mod, ["--no-such-option-xyz"])[0] == 2


def inputs_blocks(skill: str) -> list[str]:
    """Fenced code blocks of the SKILL.md Inputs section, in order (fences are tracked, so a '## ' heading inside an
    example does not end the section)."""
    lines = (ROOT / "plugins" / "ways-of-working" / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    out, cur, in_inputs = [], None, False
    for line in lines.splitlines():
        if cur is None and line.startswith("## "):
            in_inputs = line.strip() == "## Inputs"
            continue
        if not in_inputs:
            continue
        if line.startswith("```"):
            if cur is None:
                cur = []
            else:
                out.append("\n".join(cur) + "\n")
                cur = None
        elif cur is not None:
            cur.append(line)
    return out


def documented_case(skill: str, tmp_path):
    """(script, argv, out flag) for the example in the skill's Inputs section."""
    b = inputs_blocks(skill)

    def w(name: str, i: int) -> str:
        (tmp_path / name).write_text(b[i], encoding="utf-8")
        return str(tmp_path / name)

    if skill == "change-request-writer":
        return "change_request.py", [w("plan.json", 1)], "--out"
    if skill == "decision-log":
        (tmp_path / "d").mkdir()
        (tmp_path / "d" / "D-0012.md").write_text(b[0], encoding="utf-8")
        return "decision_log.py", [str(tmp_path / "d"), "--as-of", "2026-10-05"], "--out"
    if skill == "meeting-actions-ledger":
        (tmp_path / "n").mkdir()
        (tmp_path / "n" / "2026-09-22.md").write_text(b[0], encoding="utf-8")
        return "meeting_ledger.py", [str(tmp_path / "n"), "--as-of", "2026-10-05"], "--out"
    if skill == "rfc-lifecycle":
        (tmp_path / "r").mkdir()
        (tmp_path / "r" / "0001-x.md").write_text(b[0], encoding="utf-8")
        return "rfc_lint.py", [str(tmp_path / "r"), "--as-of", "2026-10-05"], "--out"
    if skill == "risk-exception-register":
        return "exception_register.py", [w("ex.csv", 0), "--as-of", "2026-10-05"], "--out"
    if skill == "shift-handover":
        return "shift_handover.py", [w("inc.csv", 0), "--as-of", "2026-10-05T08:00:00Z"], "--out"
    if skill == "vendor-scorecard":
        return "vendor_scorecard.py", [w("scores.csv", 0)], "--out"
    if skill == "working-agreement-check":
        (tmp_path / "CODEOWNERS").write_text("* @org/platform\n", encoding="utf-8")
        owners = str(tmp_path / "CODEOWNERS")
        return "working_agreement_check.py", [w("agreement.md", 0), "--codeowners", owners], "--out"
    if skill == "focus-plan":
        return "focus_plan.py", [w("cal.ics", 0), "--start", "2026-10-05", "--tz", "UTC"], "--output"
    if skill == "risk-register-refresh":
        return "risk_register_refresh.py", [w("reg.csv", 0), "--findings", w("f.json", 1), "--as-of",
                                            "2026-10-05"], "--output"
    raise KeyError(skill)


WRITERS = [
    "change-request-writer", "decision-log", "meeting-actions-ledger", "rfc-lifecycle", "risk-exception-register",
    "shift-handover", "vendor-scorecard", "working-agreement-check", "focus-plan", "risk-register-refresh",
]


@pytest.mark.parametrize("skill", WRITERS)
def test_documented_example_runs_and_an_unwritable_output_exits_2(skill, tmp_path):
    name, argv, out_flag = documented_case(skill, tmp_path)
    mod = load_script(skill, name)
    rc, out, err = run_main(mod, argv)
    assert rc in (0, 1), f"{skill}: the SKILL.md example exits {rc}: {err}"
    target = tmp_path / "no-such-folder" / "out.md"
    if skill == "meeting-actions-ledger":  # --out is a folder it creates, so block it with a file of that name
        target = tmp_path / "a-file"
        target.write_text("x", encoding="utf-8")
    rc, _, err = run_main(mod, [*argv, out_flag, str(target)])
    assert rc == 2 and "Traceback" not in err, f"{skill}: unwritable output exits {rc}"


def test_decision_log_index_path_that_cannot_be_written_exits_2(tmp_path):
    _, argv, _ = documented_case("decision-log", tmp_path)
    rc, _, err = run_main(load_script("decision-log", "decision_log.py"),
                          [*argv, "--index", str(tmp_path / "no" / "INDEX.md")])
    assert rc == 2 and "cannot write" in err


def test_working_agreement_export_that_is_not_utf8_exits_2(tmp_path):
    _, argv, _ = documented_case("working-agreement-check", tmp_path)
    bad = tmp_path / "protection.json"
    bad.write_bytes(b"\xff\xfe{")
    rc, _, err = run_main(load_script("working-agreement-check", "working_agreement_check.py"),
                          [*argv, "--protection", str(bad)])
    assert rc == 2 and "not UTF-8" in err
