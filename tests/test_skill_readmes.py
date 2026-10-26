"""Every skill has a README.md whose sample output is the real output of its sample command on its examples/ files;
scripts/skill_readmes.py --check reruns each command and fails when a README has drifted."""

from __future__ import annotations

import importlib.util

from conftest import ROOT, SKILLS


def load():
    spec = importlib.util.spec_from_file_location("skill_readmes", ROOT / "scripts" / "skill_readmes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_skill_has_a_readme_with_a_sample_and_examples():
    for skill_md in SKILLS.glob("*/SKILL.md"):
        folder = skill_md.parent
        text = (folder / "README.md").read_text(encoding="utf-8")
        assert "<!-- sample-output: start -->" in text and "## Try it" in text, folder.name
        assert any((folder / "examples").iterdir()), folder.name


def test_readme_samples_match_a_fresh_run(capsys):
    assert load().main(["--check"]) == 0, capsys.readouterr().err
