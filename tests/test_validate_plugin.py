import re
import subprocess
import sys

from conftest import ROOT


def test_repository_passes_its_own_validator():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_plugin.py")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    skills = len(list(ROOT.glob("plugins/*/skills/*/SKILL.md")))
    assert re.search(rf"^{skills} skills, 0 error\(s\)$", proc.stdout, re.MULTILINE), proc.stdout


def test_every_script_has_at_least_six_tests():
    for script in sorted(ROOT.glob("plugins/*/skills/*/scripts/[a-z]*.py")):
        tests = ROOT / "tests" / f"test_{script.stem}.py"
        count = len(re.findall(r"^def test_", tests.read_text(encoding="utf-8"), re.MULTILINE))
        assert count >= 6, f"{tests.name} has {count} tests"
