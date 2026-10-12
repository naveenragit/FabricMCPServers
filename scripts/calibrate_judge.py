"""Gate G8: calibrate criteria_judge against numeric_audit and against seeded wrong answers.

Baseline responses (55, from the earlier semantic-model run) should pass. Seeded wrong answers are made from the same
responses and must fail: a figure just outside tolerance, a trap value presented as the answer, and a reversed ranking.
Run: .\.venv\Scripts\python.exe scripts\calibrate_judge.py [--split dev|test|all] [--workers 6] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential
from foundry_eval.dataset import Row, load_dataset
from foundry_eval.evaluators import config_from_env
from foundry_eval.evaluators.base import Case
from foundry_eval.evaluators.deterministic import numeric_audit
from foundry_eval.evaluators.judges import FoundryJudge, make_criteria_judge
from foundry_eval.numbers import extract_numbers
from foundry_eval.records import AttemptRecord
from foundry_eval.net import install_dns_retry

install_dns_retry()

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> None:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def attempt_for(row: Row, text: str, tag: str) -> AttemptRecord:
    now = datetime.now(UTC)
    return AttemptRecord(
        attempt_id=f"cal-{row.id}-{tag}", run_id="calibration", arm="dax", question_id=row.id, question=row.query,
        answer_text=text, status="completed", started_at=now, ended_at=now, duration_s=0.0, timing_basis="total_only",
    )


def fmt_like(original: str, value: float) -> str:
    """Write `value` with the same shape (currency, scale, decimals, percent) as the original text."""
    m = re.match(r"^(-?)(\$?)([\d,]+(?:\.\d+)?)(\s?%|\s?(?:billion|million|thousand|[BMK]))?$", original.strip(), re.IGNORECASE)
    if not m:
        return f"{value:,.2f}"
    sign, cur, digits, suffix = m.groups()
    suffix = suffix or ""
    scale = {"billion": 1e9, "b": 1e9, "million": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3}.get(suffix.strip().lower(), 1.0)
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    shown = abs(value) / scale
    body = f"{shown:,.{decimals}f}" if "," in digits or shown >= 1000 else f"{shown:.{decimals}f}"
    return f"{'-' if value < 0 else ''}{cur}{body}{suffix}"


def _matching(exp, text: str):
    """Every display of the expected figure, including a percentage written as a fraction (0.5971)."""
    found = []
    for n in extract_numbers(text):
        slack = max(exp.tol_abs, exp.tol_rel * abs(exp.value), 0.5 * n.unit)
        if abs(n.value - exp.value) <= slack:
            found.append((n, False))
        elif exp.kind == "percent" and not n.percent and abs(n.value) <= 1.5 and abs(n.value * 100 - exp.value) <= max(slack, 0.5 * n.unit * 100):
            found.append((n, True))
    return found


def _replace_all(text: str, matches, new_value: float) -> str:
    for shown, fraction in sorted({(m.text, f) for m, f in matches}, key=lambda t: len(t[0]), reverse=True):
        text = text.replace(shown, fmt_like(shown, new_value / 100 if fraction else new_value))
    return text


def seed_outside_tolerance(row: Row, text: str) -> str | None:
    """Replace every form of the first expected figure with one clearly outside tolerance."""
    for exp in row.expected:
        if exp.kind == "count" and exp.value < 20:
            continue
        matches = _matching(exp, text)
        if not matches:
            continue
        stated = max(exp.tol_abs, exp.tol_rel * abs(exp.value), 0.5 * matches[0][0].unit)
        step = max(stated * 20, 2.0) if exp.kind == "percent" else stated * 20
        wrong = exp.value * 1.1 + 1 if exp.kind == "count" else exp.value + step * (1 if exp.value >= 0 else -1)
        return _replace_all(text, matches, wrong)
    return None


def trap_number(row: Row) -> float | None:
    for c in row.criteria:
        if c.kind == "trap" and "must not" in c.instruction.lower():
            nums = extract_numbers(c.instruction)
            if nums:
                return nums[0].value
    return None


def seed_trap_as_answer(row: Row, text: str) -> str | None:
    trap = trap_number(row)
    if trap is None or not row.expected:
        return None
    exp = row.expected[0]
    matches = _matching(exp, text)
    return _replace_all(text, matches, trap) if matches else None


def seed_reversed_ranking(row: Row, text: str) -> str | None:
    lines = text.splitlines()
    idx = [i for i, ln in enumerate(lines) if ln.strip().startswith("|") and not re.match(r"^\s*\|[\s:|-]+\|\s*$", ln)]
    if len(idx) < 4:
        return None
    body = idx[1:]
    if not any(re.search(r"ranking|order|top|highest|lowest", c.instruction, re.IGNORECASE) for c in row.criteria):
        return None
    for src, dst in zip(body, reversed(body), strict=True):
        head = lines[src].split("|")[:2]
        tail = text.splitlines()[dst].split("|")[2:]
        lines[src] = "|".join(head + tail)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="all")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--effort", default=None)
    args = parser.parse_args()
    load_env()
    rows = {r.id: r for r in load_dataset(ROOT / "evals" / "eval-dataset.jsonl")}
    baseline = {}
    for line in (ROOT / "artifacts" / "evaluation" / "semantic-model-responses.jsonl").read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        baseline[item["name"].split("_")[0].upper()] = item["response"]

    client = AIProjectClient(endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"], credential=AzureCliCredential()).get_openai_client()
    judge_eval = make_criteria_judge(FoundryJudge(client, os.environ["FOUNDRY_JUDGE_MODEL"], args.effort or None))
    config = config_from_env(os.environ)

    jobs: list[tuple[str, str, Row, str, bool]] = []  # id, tag, row, text, should_pass
    for rid, text in baseline.items():
        row = rows.get(rid)
        if row is None or not [c for c in row.criteria if c.kind in ("fact", "trap", "behavior") and c.weight > 0]:
            continue
        if args.split != "all" and row.split != args.split:
            continue
        jobs.append((rid, "baseline", row, text, True))
        for tag, fn in (("outside_tolerance", seed_outside_tolerance), ("trap_as_answer", seed_trap_as_answer), ("reversed_ranking", seed_reversed_ranking)):
            seeded = fn(row, text)
            if seeded and seeded != text:
                jobs.append((rid, tag, row, seeded, False))
    if args.limit:
        jobs = jobs[: args.limit]
    print(f"{len(jobs)} scoring jobs ({sum(j[4] for j in jobs)} baseline, {sum(not j[4] for j in jobs)} seeded)", flush=True)

    def score(job):
        rid, tag, row, text, should_pass = job
        case = Case(row=row, attempt=attempt_for(row, text, tag), steps=[], config=config)
        return job, judge_eval.run(case), numeric_audit(case)

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, res in enumerate(pool.map(score, jobs), 1):
            results.append(res)
            if i % 10 == 0:
                print(f"  scored {i}/{len(jobs)}", flush=True)

    out = []
    for (rid, tag, row, text, should_pass), judged, audit in results:
        verdicts = (judged.details or {}).get("criteria", {})
        numeric_ids = {e.criterion for e in row.expected}
        audit_vals = (audit.details or {}).get("values", {})
        crit_audit = {}
        for cid in numeric_ids:
            vs = [v for k, v in audit_vals.items() if k.split(":")[0] == cid]
            crit_audit[cid] = all(vs) if vs else None
        out.append({
            "id": rid, "variant": tag, "split": row.split, "should_pass": should_pass, "judge_score": judged.score, "judge_status": judged.status,
            "judge_failed": [k for k, v in verdicts.items() if not v.get("met")],
            "audit_score": audit.score, "audit_failed": [c for c, ok in crit_audit.items() if ok is False],
            "numeric_criteria": sorted(numeric_ids), "verdicts": verdicts, "text": text if tag != "baseline" else None,
        })
    dest = ROOT / "artifacts" / "evaluation" / "judge-calibration.json"
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")

    scored = [o for o in out if o["judge_status"] == "ok"]
    base = [o for o in scored if o["variant"] == "baseline"]
    seeded = [o for o in scored if o["variant"] != "baseline"]
    # criterion-level agreement with the numeric audit on numeric criteria
    # The audit cannot see order, so ranking-reversal variants are judged on rejection only, not on audit agreement.
    agree = total = 0
    for o in scored:
        if o["variant"] == "reversed_ranking":
            continue
        for cid in o["numeric_criteria"]:
            judged_ok = cid not in o["judge_failed"] if o["verdicts"] else None
            audit_ok = cid not in o["audit_failed"]
            if judged_ok is None:
                continue
            total += 1
            agree += judged_ok == audit_ok
    false_fail = [o["id"] for o in base if o["judge_score"] != 1.0]
    false_pass = [f"{o['id']}:{o['variant']}" for o in seeded if o["judge_score"] == 1.0]
    print(f"\nbaseline rows scored: {len(base)}; judge says all criteria met: {len(base) - len(false_fail)} ({100 * (len(base) - len(false_fail)) / max(len(base), 1):.1f}%)")
    print(f"false-fail (baseline not fully met): {len(false_fail)} -> {false_fail}")
    print(f"seeded wrong answers scored: {len(seeded)}; judge rejected: {len(seeded) - len(false_pass)} ({100 * (len(seeded) - len(false_pass)) / max(len(seeded), 1):.1f}%)")
    print(f"false-pass (seeded accepted): {len(false_pass)} -> {false_pass}")
    print(f"criterion-level agreement with numeric_audit: {agree}/{total} ({100 * agree / max(total, 1):.1f}%)")
    print(f"errors: {[o['id'] for o in out if o['judge_status'] != 'ok']}")
    print(f"detail -> {dest.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
