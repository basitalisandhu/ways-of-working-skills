"""Scaling checks for the four hot spots fixed on 2026-10-26, each found by timing the scripts on synthetic inputs 10
times larger than usual. The equivalence tests compare the fast code with the straightforward version on seeded
random inputs; the timing tests use inputs where the old code took several seconds and the new code well under one,
with a wide margin for slow CI machines."""

from __future__ import annotations

import json
import random
import time
from fractions import Fraction

from conftest import load_script, run_main

decision_log = load_script("decision-log", "decision_log.py")
vendor = load_script("vendor-scorecard", "vendor_scorecard.py")


def random_records(rng: random.Random, n: int, duplicate_ids: bool) -> list[dict]:
    ids = [f"D-{i}" for i in range(n)]
    if duplicate_ids:
        ids[rng.randrange(n)] = ids[rng.randrange(n)]
    recs = []
    for i, rid in enumerate(ids):
        links = [rng.choice(ids + ["D-missing"])] if rng.random() < 0.8 else []
        recs.append({"id": rid, "stem": f"file-{i}", "superseded_by": links, "supersedes": []})
    return recs


def resolver(records: list[dict]):
    by_key: dict[str, dict] = {}
    for r in records:
        by_key.setdefault(r["id"], r)
    for r in records:
        by_key.setdefault(r["stem"], r)
    return by_key.get


def test_supersede_cycles_match_walking_every_chain():
    rng = random.Random(20261026)
    for trial in range(400):
        recs = random_records(rng, rng.randrange(1, 12), duplicate_ids=trial % 4 == 0)
        resolve = resolver(recs)
        expected = [(r["id"], chain) for r in recs
                    if (chain := decision_log.walk_back_to_start(r, resolve)) is not None]
        got = [(r["id"], chain) for r, chain in decision_log.supersede_cycles(recs, resolve)]
        assert got == expected, recs


def brute_force_moves(criteria, vendors, step):
    base_w = {c["criterion"]: c["weight"] for c in criteria}
    out = []
    for c in criteria:
        for sign in (1, -1):
            w = dict(base_w, **{c["criterion"]: c["weight"] + sign * step})
            if w[c["criterion"]] < 0 or not sum(w.values()):
                out.append(None)
            else:
                out.append(vendor.ranking(vendor.totals(criteria, vendors, w)))
    return out


def test_vendor_sensitivity_matches_a_full_recompute(tmp_path):
    rng = random.Random(7)
    for _ in range(60):
        vendors = [f"V{i}" for i in range(rng.randrange(1, 5))]
        rows = ["criterion,weight," + ",".join(vendors)]
        for i in range(rng.randrange(1, 7)):
            rows.append(f"c{i},{rng.choice(['0', '1', '2', '0.5', '3'])}," + ",".join(str(rng.randrange(1, 6))
                                                                                    for _ in vendors))
        (tmp_path / "s.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
        rc, out, _ = run_main(vendor, [str(tmp_path / "s.csv"), "--json"])
        if rc == 2:
            continue
        rep = json.loads(out)
        criteria = [{"criterion": c["criterion"], "weight": Fraction(c["weight"]),
                     "scores": c["scores"]} for c in rep["criteria"]]
        expected = brute_force_moves(criteria, vendors, Fraction(1))
        assert [m["order"] if m["tested"] else None for m in rep["sensitivity"]] == expected


def timed(module, argv) -> float:
    start = time.perf_counter()
    rc, _, err = run_main(module, argv)
    assert rc in (0, 1), err
    return time.perf_counter() - start


def test_vendor_scorecard_with_many_criteria_is_fast(tmp_path):
    vendors = [f"V{v}" for v in range(10)]
    rows = ["criterion,weight," + ",".join(vendors)]
    rows += [f"c{i},{i % 5 + 1}," + ",".join(str((i * v) % 5 + 1) for v in range(10)) for i in range(600)]
    (tmp_path / "s.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert timed(vendor, [str(tmp_path / "s.csv")]) < 3


def test_long_supersede_chain_is_fast(tmp_path):
    n = 2000
    for i in range(n):
        links = (f"supersedes: D-{i - 1:05d}\n" if i else "") + (
            f"superseded-by: D-{i + 1:05d}\nstatus: superseded\n" if i < n - 1 else "status: active\n")
        (tmp_path / f"D-{i:05d}.md").write_text(
            f"---\nid: D-{i:05d}\ntitle: t\n{links}decided-by: leads\ndate: 2026-01-01\nreview-date: 2027-01-01\n"
            "---\n\n## Context\nx\n\n## Decision\ny\n", encoding="utf-8")
    assert timed(decision_log, [str(tmp_path), "--as-of", "2026-10-05"]) < 3


def test_large_codeowners_file_is_fast(tmp_path):
    module = load_script("working-agreement-check", "working_agreement_check.py")
    (tmp_path / "a.md").write_text("".join(f"- CODEOWNERS covers `dir{i}/`.\n" for i in range(800)), encoding="utf-8")
    (tmp_path / "CODEOWNERS").write_text("".join(f"/dir{i}/ @org/team{i}\n" for i in range(2000)), encoding="utf-8")
    assert timed(module, [str(tmp_path / "a.md"), "--codeowners", str(tmp_path / "CODEOWNERS")]) < 3


def test_many_mail_threads_merge_fast(tmp_path):
    module = load_script("next-actions-sweep", "next_actions.py")
    n = 20000
    msgs = [{"id": f"m{i}", "subject": f"Subject {i}", "conversationId": f"c{i}",
             "from": {"emailAddress": {"name": f"P {i % 97}", "address": f"p{i % 97}@example.com"}},
             "receivedDateTime": f"2026-10-15T{i % 24:02d}:{i % 60:02d}:00Z", "bodyPreview": "Can you check this?"}
            for i in range(n)]
    (tmp_path / "mail.json").write_text(json.dumps({"value": msgs}), encoding="utf-8")
    argv = ["--source", f"w={tmp_path / 'mail.json'}", "--me", "me@example.com", "--as-of", "2026-10-16T09:00:00Z"]
    assert timed(module, argv) < 6
