#!/usr/bin/env python3
"""vendor_scorecard.py: weighted scoring of vendors or tools from a CSV of criteria, with a sensitivity check.

Input: one CSV file. The header is `criterion,weight,<vendor>,<vendor>,...`; an optional `notes` column is kept as
text and is not a vendor. Header names `criterion` and `weight` are matched ignoring case. Each row is one criterion:
  * weight: a number of 0 or more (decimals allowed, for example 2 or 1.5);
  * a score per vendor: a whole number from 1 to 5.
At least one vendor column and one usable row are needed.

Rules:
  * weighted score per vendor = sum(weight x score) / sum(weight), shown to two decimal places; computed with exact
    fractions so ties are real ties;
  * ranking: highest weighted score first; vendors with equal scores share a rank and are listed by name;
  * sensitivity: each criterion's weight is moved by +step and -step (default 1) in turn, the others held still,
    and the ranking recomputed. A move that would take a weight below 0 is not tested. For each move the report says
    whether the leader and the full order survive;
  * leader-not-robust (flagged): some single move changes who is first (or creates or breaks a tie for first);
  * tie-for-first (flagged): two or more vendors share the top score;
  * data problems (flagged; the row is left out of the scoring): malformed-row (wrong number of cells),
    invalid-weight, invalid-score (missing, not a whole number, or outside 1 to 5), duplicate-criterion,
    missing-criterion.
Order changes below first place are reported but not flagged. The script scores what the CSV says; it does not
choose the criteria, set the weights or pick a vendor.

Output: a Markdown scorecard on standard output (or --out FILE); --json prints the computed data instead. The output
is deterministic for the same file and options.

Exit codes: 0 nothing flagged, 1 something flagged for a person, 2 bad input (file missing or empty, no criterion or
weight column, no vendor columns, no usable rows, bad --step).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from fractions import Fraction
from pathlib import Path

DATA_RULES = ("malformed-row", "invalid-weight", "invalid-score", "duplicate-criterion", "missing-criterion")


class InputError(Exception):
    """Bad input: the message is printed and the script exits 2."""


def key(name: str) -> str:
    return re.sub(r"[\s_-]+", "_", name.strip().lower())


def parse_number(value: str) -> Fraction | None:
    if not re.fullmatch(r"\d+(\.\d+)?", value.strip()):
        return None
    return Fraction(value.strip())


def fmt(value: Fraction) -> str:
    return f"{float(value):.2f}"


def fmt_weight(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{float(value):g}"


def read_rows(path: Path) -> tuple[list[str], list[tuple[int, list[str]]]]:
    if not path.is_file():
        raise InputError(f"{path}: file not found")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if not text.strip():
        raise InputError(f"{path}: file is empty")
    reader = csv.reader(io.StringIO(text))
    rows = [(reader.line_num, [c.strip() for c in r]) for r in reader]
    rows = [(n, r) for n, r in rows if any(r)]
    if not rows:
        raise InputError(f"{path}: no rows")
    return rows[0][1], rows[1:]


def totals(criteria: list[dict], vendors: list[str], weights: dict[str, Fraction]) -> dict[str, Fraction]:
    total_weight = sum(weights[c["criterion"]] for c in criteria)
    out = {}
    for v in vendors:
        points = sum(weights[c["criterion"]] * c["scores"][v] for c in criteria)
        out[v] = points / total_weight if total_weight else Fraction(0)
    return out


def ranking(scores: dict[str, Fraction]) -> list[list[str]]:
    """Vendors grouped by equal score, best first; names sorted inside a group."""
    groups: list[list[str]] = []
    for value in sorted(set(scores.values()), reverse=True):
        groups.append(sorted(v for v in scores if scores[v] == value))
    return groups


def analyse(header: list[str], rows: list[tuple[int, list[str]]], step: Fraction) -> dict:
    keys = [key(h) for h in header]
    if "criterion" not in keys or "weight" not in keys:
        raise InputError("header needs 'criterion' and 'weight' columns")
    ci, wi = keys.index("criterion"), keys.index("weight")
    ni = keys.index("notes") if "notes" in keys else None
    vendor_cols = [(i, h) for i, h in enumerate(header) if i not in (ci, wi, ni) and h]
    if not vendor_cols:
        raise InputError("no vendor columns after criterion and weight")
    vendors = [h for _, h in vendor_cols]
    if len(set(vendors)) != len(vendors):
        raise InputError("vendor column names must be unique")
    findings: list[dict] = []
    criteria: list[dict] = []
    seen: set[str] = set()

    def problem(rule: str, line: int, criterion: str, detail: str) -> None:
        findings.append({"rule": rule, "line": line, "criterion": criterion, "detail": detail})

    for line, cells in rows:
        name = cells[ci] if ci < len(cells) else ""
        if len(cells) != len(header):
            problem("malformed-row", line, name, f"expected {len(header)} cells, found {len(cells)}")
            continue
        if not name:
            problem("missing-criterion", line, "", "criterion is empty")
            continue
        if name.lower() in seen:
            problem("duplicate-criterion", line, name, "criterion already listed above")
            continue
        weight = parse_number(cells[wi])
        if weight is None:
            problem("invalid-weight", line, name, f"weight {cells[wi]!r} is not a number of 0 or more")
            continue
        scores: dict[str, int] = {}
        bad = []
        for i, vendor in vendor_cols:
            value = cells[i]
            if re.fullmatch(r"[1-5]", value):
                scores[vendor] = int(value)
            else:
                bad.append(f"{vendor}={value!r}" if value else f"{vendor} is empty")
        if bad:
            problem("invalid-score", line, name, "scores must be whole numbers 1 to 5: " + ", ".join(bad))
            continue
        seen.add(name.lower())
        criteria.append(
            {
                "criterion": name,
                "weight": weight,
                "scores": scores,
                "notes": cells[ni] if ni is not None else "",
                "line": line,
            }
        )
    if not criteria:
        raise InputError("no usable criterion rows")
    base_weights = {c["criterion"]: c["weight"] for c in criteria}
    if not sum(base_weights.values()):
        raise InputError("all weights are 0")
    base = totals(criteria, vendors, base_weights)
    base_rank = ranking(base)
    if len(base_rank[0]) > 1:
        findings.append(
            {
                "rule": "tie-for-first",
                "line": 0,
                "criterion": "",
                "detail": "equal top score " + fmt(base[base_rank[0][0]]) + ": " + ", ".join(base_rank[0]),
            }
        )
    moves = []
    for c in criteria:
        for sign, label in ((1, "+"), (-1, "-")):
            new_weight = c["weight"] + sign * step
            entry = {
                "criterion": c["criterion"],
                "move": f"{label}{fmt_weight(step)}",
                "weight": fmt_weight(new_weight) if new_weight >= 0 else None,
            }
            weights = dict(base_weights, **{c["criterion"]: new_weight})
            if new_weight < 0 or not sum(weights.values()):
                entry.update(tested=False, leader_kept=None, order_kept=None, leader=[], order=[])
                moves.append(entry)
                continue
            rank = ranking(totals(criteria, vendors, weights))
            entry.update(
                tested=True,
                leader_kept=rank[0] == base_rank[0],
                order_kept=rank == base_rank,
                leader=rank[0],
                order=rank,
            )
            moves.append(entry)
            if not entry["leader_kept"]:
                findings.append(
                    {
                        "rule": "leader-not-robust",
                        "line": c["line"],
                        "criterion": c["criterion"],
                        "detail": f"weight {fmt_weight(c['weight'])} -> {fmt_weight(new_weight)} puts "
                        + " and ".join(rank[0])
                        + " first instead of "
                        + " and ".join(base_rank[0]),
                    }
                )
    order = {r: i for i, r in enumerate(DATA_RULES + ("tie-for-first", "leader-not-robust"))}
    findings.sort(key=lambda f: (order[f["rule"]], f["line"], f["criterion"]))
    tested = [m for m in moves if m["tested"]]
    return {
        "step": fmt_weight(step),
        "vendors": vendors,
        "criteria": [
            {
                "criterion": c["criterion"],
                "weight": fmt_weight(c["weight"]),
                "scores": c["scores"],
                "notes": c["notes"],
                "line": c["line"],
            }
            for c in criteria
        ],
        "weighted_scores": {v: fmt(base[v]) for v in vendors},
        "ranking": base_rank,
        "sensitivity": moves,
        "ranking_survives": bool(tested) and all(m["order_kept"] for m in tested),
        "leader_survives": bool(tested) and all(m["leader_kept"] for m in tested),
        "findings": findings,
    }


def cell(text: str) -> str:
    return text.replace("|", "\\|")


def render(rep: dict, source: str) -> str:
    vendors = rep["vendors"]
    out = [f"# Vendor scorecard: {source}", ""]
    out.append(
        f"{len(rep['criteria'])} criteria, {len(vendors)} vendor(s). Scores 1 to 5; weighted score = "
        f"sum(weight x score) / sum(weight). Sensitivity step: {rep['step']}."
    )
    out += ["", "## Scores", ""]
    out.append("| Criterion | Weight | " + " | ".join(cell(v) for v in vendors) + " |")
    out.append("|---|---:|" + "---:|" * len(vendors))
    for c in rep["criteria"]:
        out.append(
            f"| {cell(c['criterion'])} | {c['weight']} | " + " | ".join(str(c["scores"][v]) for v in vendors) + " |"
        )
    out.append("| **Weighted score** | | " + " | ".join(f"**{rep['weighted_scores'][v]}**" for v in vendors) + " |")
    out += ["", "## Ranking", ""]
    for n, group in enumerate(rep["ranking"], start=1):
        tie = " (tie)" if len(group) > 1 else ""
        out.append(f"{n}. {' and '.join(group)}: {rep['weighted_scores'][group[0]]}{tie}")
    out += ["", "## Sensitivity", ""]
    if rep["leader_survives"] and rep["ranking_survives"]:
        out.append(f"The full ranking survives every weight moved by {rep['step']} in either direction.")
    elif rep["leader_survives"]:
        out.append("The leader survives every single move; the order below first place changes under some moves.")
    else:
        out.append("The leader changes under at least one single move: the result depends on the weights chosen.")
    out += ["", "| Criterion | Move | New weight | Leader | Order | First place |", "|---|---|---:|---|---|---|"]
    for m in rep["sensitivity"]:
        if not m["tested"]:
            out.append(f"| {cell(m['criterion'])} | {m['move']} | n/a | not tested (weight below 0) | | |")
            continue
        out.append(
            f"| {cell(m['criterion'])} | {m['move']} | {m['weight']} | "
            f"{'kept' if m['leader_kept'] else '**changed**'} | {'kept' if m['order_kept'] else 'changed'} | "
            f"{cell(' and '.join(m['leader']))} |"
        )
    flagged = [f for f in rep["findings"] if f["rule"] != "leader-not-robust"]
    if flagged:
        out += ["", "## Findings", ""]
        for f in flagged:
            where = f" (line {f['line']})" if f["line"] else ""
            name = f"{f['criterion']}{where}: " if f["criterion"] else (f"line {f['line']}: " if f["line"] else "")
            out.append(f"- [{f['rule']}] {name}{f['detail']}")
    notes = [c for c in rep["criteria"] if c["notes"]]
    if notes:
        out += ["", "## Notes", ""]
        out += [f"- {c['criterion']}: {c['notes']}" for c in notes]
    out += ["", "## Decision", "", "- Chosen vendor, decided by, date and reason: ____", ""]
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vendor_scorecard.py",
        description="Weighted scorecard of vendors or tools from a CSV (criterion, weight, one column per vendor "
        "with scores 1 to 5), with a check of whether the ranking survives each weight moved by one step.",
        epilog="Exit codes: 0 nothing flagged, 1 leader not robust, tie for first or data problems, 2 bad input.",
    )
    p.add_argument("scores", help="CSV file: criterion,weight,<vendor>,... (optional notes column)")
    p.add_argument("--step", default="1", help="how far each weight is moved up and down (default 1)")
    p.add_argument("--json", action="store_true", help="print the computed data as JSON instead of Markdown")
    p.add_argument("--out", default=None, help="write the output to this file instead of standard output")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        step = parse_number(args.step)
        if step is None or step == 0:
            raise InputError(f"--step {args.step!r} must be a number above 0")
        path = Path(args.scores)
        header, rows = read_rows(path)
        rep = analyse(header, rows, step)
    except InputError as exc:
        print(f"vendor_scorecard.py: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(rep, indent=2, sort_keys=True) + "\n" if args.json else render(rep, path.name)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 1 if rep["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
