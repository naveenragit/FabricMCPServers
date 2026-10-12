"""Per-arm scorecard from stored runs: correctness, process, risk gates, efficiency, tool errors.

Run: .\.venv\Scripts\python.exe scripts\scorecard.py <run_id> [<run_id> ...] [--out artifacts/scorecards/<name>.md]
Needs the analysis extra (pandas). Reads the plan's tables only; no reference value is printed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
from foundry_eval.analysis import load_tables

ROOT = Path(__file__).resolve().parents[1]
ZERO = ["read_only_violation", "secret_canary_leak", "tool_allowlist", "named_row_cap"]
PROCESS = ["data_tool_called", "schema_first", "numbers_traceable"]
BUILTIN = ["task_completion", "intent_resolution", "tool_call_accuracy", "tool_input_accuracy", "tool_selection",
           "tool_call_success", "tool_output_utilization", "groundedness", "relevance", "fluency", "coherence", "task_adherence"]


def pct(x: float | None) -> str:
    return "n/a" if x is None or pd.isna(x) else f"{100 * x:.0f}%"


def num(x: float | None, nd: int = 1) -> str:
    return "n/a" if x is None or pd.isna(x) else f"{x:.{nd}f}"


def table(frame: pd.DataFrame) -> str:
    return frame.to_markdown(index=True) if hasattr(frame, "to_markdown") else frame.to_string()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_ids", nargs="+")
    parser.add_argument("--out")
    args = parser.parse_args()
    tables = load_tables(ROOT / "artifacts" / "runs", args.run_ids)
    attempts, evals, steps, runs = tables["attempts"], tables["evaluations"], tables["steps"], tables["runs"]
    ok = evals[evals["status"] == "ok"]
    out: list[str] = []

    def section(title: str) -> None:
        out.append(f"\n## {title}\n")

    out.append("# Scorecard\n")
    out.append(f"Runs: {', '.join(args.run_ids)}. Attempts: {len(attempts)}. Rows are arm columns; missing is n/a.\n")

    section("Run overview")
    over = runs.set_index("arm")[["agent_name", "agent_version", "agent_model", "reasoning_effort", "judge_model", "n_attempts", "n_completed", "n_failed", "n_infra_errors", "n_tool_errors"]]
    out.append(over.T.to_string())

    section("Correctness (criteria_judge, core answerable rows; correct = all criteria met)")
    cj = ok[ok["evaluator"] == "criteria_judge"].copy()
    cj = cj.merge(attempts[["attempt_id", "tool_error"]], on="attempt_id")
    rows = []
    for arm, g in cj.groupby("arm"):
        n_att = int((attempts["arm"] == arm).sum())
        rows.append({"arm": arm, "scored": len(g), "correct": int((g["score"] == 1.0).sum()), "correct_rate_of_scored": pct((g["score"] == 1.0).mean()), "mean_criteria_pass": pct(g["score"].mean())})
    out.append(pd.DataFrame(rows).set_index("arm").to_string() if rows else "no scored rows")
    audit = ok[ok["evaluator"] == "numeric_audit"]
    if len(audit):
        a = audit.groupby("arm")["score"].apply(lambda s: (s == 1.0).mean())
        out.append("\nnumeric_audit (deterministic; all expected figures found): " + ", ".join(f"{k}={pct(v)}" for k, v in a.items()))

    section("Per-row result (criteria_judge score; TOOL-ERR = a tool error ended the attempt)")
    wide = cj.pivot_table(index="question_id", columns="arm", values="score", aggfunc="last")
    te = attempts[attempts["tool_error"]].groupby(["question_id", "arm"]).size()
    for (qid, arm) in te.index:
        wide.loc[qid, arm] = float("nan")
    text = wide.map(lambda v: "n/a" if pd.isna(v) else f"{v:.2f}")
    for (qid, arm) in te.index:
        text.loc[qid, arm] = "TOOL-ERR"
    out.append(text.to_string())

    section("Risk gates (zero tolerance: a single failure fails the arm; manual review before a verdict is final)")
    rows = []
    for arm in sorted(attempts["arm"].unique()):
        r = {"arm": arm}
        for name in ZERO + ["behavior_judge"]:
            g = ok[(ok["arm"] == arm) & (ok["evaluator"] == name)]
            r[name] = f"{int((g['passed'] == True).sum())}/{len(g)} pass"  # noqa: E712
        rows.append(r)
    out.append(pd.DataFrame(rows).set_index("arm").to_string())
    fails = ok[(ok["evaluator"].isin(ZERO)) & (ok["passed"] == False)]  # noqa: E712
    for _, f in fails.iterrows():
        out.append(f"  FAIL {f['arm']} {f['question_id']} {f['evaluator']}: {str(f['reason'])[:140]}")
    bj = ok[(ok["evaluator"] == "behavior_judge") & (ok["passed"] == False)]  # noqa: E712
    for _, f in bj.iterrows():
        out.append(f"  BEHAVIOR-FAIL {f['arm']} {f['question_id']}: {str(f['reason'])[:160]}")

    section("Agentic process (custom)")
    rows = []
    for arm in sorted(attempts["arm"].unique()):
        r = {"arm": arm}
        for name in PROCESS:
            g = ok[(ok["arm"] == arm) & (ok["evaluator"] == name)]
            r[name] = "n/a" if g.empty else pct((g["passed"] == True).mean())  # noqa: E712
        rows.append(r)
    out.append(pd.DataFrame(rows).set_index("arm").to_string())

    section("Foundry built-in evaluators (mean score; threshold in the cloud report)")
    b = ok[ok["evaluator"].str.startswith("builtin.")].copy()
    if len(b):
        b["name"] = b["evaluator"].str.replace("builtin.", "", regex=False)
        piv = b.pivot_table(index="name", columns="arm", values="score", aggfunc="mean").round(2)
        pas = b.pivot_table(index="name", columns="arm", values="passed", aggfunc="mean").map(pct)
        out.append("mean score\n" + piv.to_string() + "\n\nshare passing\n" + pas.to_string())
    else:
        out.append("no built-in results")

    section("Efficiency (completed attempts only; agent tokens only, judge tokens excluded)")
    done = attempts[attempts["status"] == "completed"]
    eff = done.groupby("arm").agg(
        attempts=("attempt_id", "count"),
        duration_p50=("duration_s", "median"),
        duration_p95=("duration_s", lambda s: s.quantile(0.95)),
        input_tokens_mean=("input_tokens", "mean"),
        output_tokens_mean=("output_tokens", "mean"),
        reasoning_tokens_mean=("reasoning_tokens", "mean"),
        tool_calls_mean=("tool_calls", "mean"),
        schema_calls_mean=("schema_calls", "mean"),
        query_calls_mean=("query_calls", "mean"),
        tool_time_mean=("tool_time_s", "mean"),
        reasoning_time_mean=("reasoning_time_s", "mean"),
    ).round(1)
    out.append(eff.T.to_string())
    correct_ids = set(cj[cj["score"] == 1.0]["attempt_id"])
    tpc = []
    for arm, g in done.groupby("arm"):
        c = g[g["attempt_id"].isin(correct_ids)]
        tpc.append(f"{arm}: {num(g['total_tokens'].sum() / max(len(c), 1), 0)} agent tokens per correct answer ({len(c)} correct)")
    out.append("\n" + "; ".join(tpc))
    if "fabric" in set(attempts["arm"]):
        out.append("Arm 3 note: latency includes the proxy hop and the data agent's own work; Fabric-side LLM/capacity use is not in these tokens.")

    section("Tool errors (D17)")
    te_rows = attempts[attempts["tool_error"]][["arm", "question_id", "repeat_index", "tool_error_tool", "tool_error_message"]]
    out.append("none" if te_rows.empty else te_rows.to_string(index=False))
    tr = tables["traces"]
    if len(tr):
        out.append("\nFoundry trace cross-check: " + ", ".join(f"{a}: {int(g['tool_error'].sum())} flagged of {len(g)} traced" for a, g in tr.groupby("arm")))

    text_out = "\n".join(out)
    print(text_out)
    if args.out:
        dest = ROOT / args.out
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text_out, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
