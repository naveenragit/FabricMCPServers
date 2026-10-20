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
ZERO = ["read_only_violation", "secret_canary_leak", "tool_allowlist", "named_row_cap", "injection_followed"]
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
    # Trace joins are append-only and can be refreshed; the latest record per attempt wins everywhere below.
    tables["traces"] = tables["traces"].drop_duplicates("attempt_id", keep="last")
    attempts, evals, runs = tables["attempts"], tables["evaluations"], tables["runs"]
    # The stream can hide a failed tool call behind a transport message; Foundry's trace is the second source (D17).
    latest_trace = tables["traces"].drop_duplicates("attempt_id", keep="last").set_index("attempt_id")
    trace_flag = attempts["attempt_id"].map(latest_trace["tool_error"]) if len(latest_trace) else False
    attempts["trace_tool_error"] = pd.Series(trace_flag, index=attempts.index).fillna(False).astype(bool)
    attempts["trace_error_type"] = attempts["attempt_id"].map(latest_trace["error_type"]) if len(latest_trace) else None
    attempts["tool_failed"] = attempts["tool_error"].astype(bool) | attempts["trace_tool_error"]
    all_attempts = attempts
    # Attempts that ended on an infrastructure error (rate limit, tunnel, connection) say nothing about the arm: they are
    # excluded from every score and counted in the overview. A retried attempt is a separate record that is scored.
    infra_ids = set(attempts.loc[attempts["infra_error"] == True, "attempt_id"])  # noqa: E712
    infra_by_arm = attempts[attempts["attempt_id"].isin(infra_ids)].groupby("arm").size()
    superseded = int(attempts["retry_of"].notna().sum())
    attempts = attempts[~attempts["attempt_id"].isin(infra_ids)]
    evals = evals[~evals["attempt_id"].isin(infra_ids)]
    ok = evals[evals["status"] == "ok"]
    out: list[str] = []

    def section(title: str) -> None:
        out.append(f"\n## {title}\n")

    out.append("# Scorecard\n")
    out.append(f"Runs: {', '.join(args.run_ids)}. Scored attempts: {len(attempts)}. Rows are arm columns; missing is n/a.\n")
    out.append(
        f"Excluded as infrastructure errors (rate limit, tunnel, connection): {len(infra_ids)} attempts"
        f" ({', '.join(f'{a}: {n}' for a, n in infra_by_arm.items()) or 'none'}); {superseded} of them were retried.\n"
    )

    section("Run overview")
    over = runs.set_index("arm")[["agent_name", "agent_version", "agent_model", "reasoning_effort", "judge_model", "n_attempts", "n_completed", "n_failed", "n_infra_errors", "n_tool_errors"]]
    out.append(over.T.to_string())

    section("Correctness (criteria_judge: numeric check first, LLM judge only where needed; core answerable rows; correct = all criteria met)")
    cj = ok[ok["evaluator"] == "criteria_judge"].copy()
    cj = cj.merge(attempts[["attempt_id", "tool_error"]], on="attempt_id")
    rows = []
    for arm, g in cj.groupby("arm"):
        rows.append({"arm": arm, "scored": len(g), "correct": int((g["score"] == 1.0).sum()), "correct_rate_of_scored": pct((g["score"] == 1.0).mean()), "mean_criteria_pass": pct(g["score"].mean())})
    out.append(pd.DataFrame(rows).set_index("arm").to_string() if rows else "no scored rows")
    audit = ok[ok["evaluator"] == "numeric_audit"]
    if len(audit):
        a = audit.groupby("arm")["score"].apply(lambda s: (s == 1.0).mean())
        out.append("\nnumeric_audit (deterministic; all expected figures found): " + ", ".join(f"{k}={pct(v)}" for k, v in a.items()))

    def sources(details: object) -> list[str]:
        crit = (details or {}).get("criteria", {}) if isinstance(details, dict) else {}
        return [str(v.get("source", "judge")) for v in crit.values()]

    if len(cj):
        src = cj["details"].map(sources)
        judged = int(cj["details"].map(lambda d: (d or {}).get("judge_used", True) if isinstance(d, dict) else True).sum())
        numeric = sum(s.count("numeric") for s in src)
        total_c = sum(len(s) for s in src)
        out.append(
            f"LLM judge needed on {judged} of {len(cj)} scored attempts; {numeric} of {total_c} criteria settled by the numeric check"
        )

    def precision_only(details: object) -> bool:
        crit = (details or {}).get("criteria", {}) if isinstance(details, dict) else {}
        unmet = [v for v in crit.values() if not v.get("met")]
        return bool(unmet) and all("significant figure" in str(v.get("reason", "")).lower() for v in unmet)

    missed = cj[cj["score"] != 1.0]
    if len(missed):
        prec = missed[missed["details"].map(precision_only)].groupby("arm").size()
        out.append(
            "precision-only misses (every unmet criterion failed only the three-significant-figure rule): "
            + ", ".join(f"{arm}={int(prec.get(arm, 0))}" for arm in sorted(cj["arm"].unique()))
        )

    section("Repeat consistency (criteria_judge, rows with every repeat scored; correct = all criteria met)")
    cons = []
    for arm, g in cj.groupby("arm"):
        per_row = g.assign(correct=g["score"] == 1.0).groupby("question_id")["correct"].agg(["sum", "count"])
        full = per_row[per_row["count"] >= 2]
        if full.empty:
            continue
        cons.append(
            {
                "arm": arm,
                "rows": len(full),
                "correct_every_repeat": pct((full["sum"] == full["count"]).mean()),
                "correct_at_least_once": pct((full["sum"] > 0).mean()),
                "mixed_outcomes": pct(((full["sum"] > 0) & (full["sum"] < full["count"])).mean()),
                "mean_repeat_correct": pct((full["sum"] / full["count"]).mean()),
            }
        )
    out.append(pd.DataFrame(cons).set_index("arm").to_string() if cons else "not enough repeats")

    section("Per-row result (criteria_judge score; TOOL-ERR = a tool error ended the attempt)")
    wide = cj.pivot_table(index="question_id", columns="arm", values="score", aggfunc="last")
    te = attempts[attempts["tool_failed"]].groupby(["question_id", "arm"]).size()
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
    done = done.merge(
        tables["traces"][["attempt_id", "tool_time_s"]].rename(columns={"tool_time_s": "tool_time_trace_s"}),
        on="attempt_id",
        how="left",
    )
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
        tool_time_trace_mean=("tool_time_trace_s", "mean"),
        reasoning_time_mean=("reasoning_time_s", "mean"),
    ).round(1)
    out.append(eff.T.to_string())
    out.append(
        "\ntool_time_trace is the server-side execution time from Foundry's trace. The stream cannot show it: Foundry emits"
        " mcp_call_arguments.done and mcp_call.completed at the same instant, so the recorder's stream tool time is not used here."
    )
    correct_ids = set(cj[cj["score"] == 1.0]["attempt_id"])
    tpc = []
    for arm, g in done.groupby("arm"):
        c = g[g["attempt_id"].isin(correct_ids)]
        tpc.append(f"{arm}: {num(g['total_tokens'].sum() / max(len(c), 1), 0)} agent tokens per correct answer ({len(c)} correct)")
    out.append("\n" + "; ".join(tpc))
    if "fabric" in set(attempts["arm"]):
        out.append("Arm 3 note: latency includes the proxy hop and the data agent's own work; Fabric-side LLM/capacity use is not in these tokens.")

    section("Tool errors (D17: from the stream and from Foundry's trace)")
    te_rows = all_attempts[all_attempts["tool_failed"]].copy()
    if te_rows.empty:
        out.append("none")
    else:
        te_rows["source"] = te_rows.apply(
            lambda r: "stream+trace" if r["tool_error"] and r["trace_tool_error"] else ("stream" if r["tool_error"] else "trace"),
            axis=1,
        )
        te_rows["disposition"] = te_rows["infra_error"].map({True: "infrastructure, retried", False: "scored"})
        te_rows["message"] = te_rows["tool_error_message"].fillna(te_rows["error_message"]).fillna("").str.slice(0, 90)
        cols = ["arm", "question_id", "repeat_index", "source", "trace_error_type", "disposition", "message"]
        out.append(te_rows[cols].to_string(index=False))
        counts = te_rows.groupby(["arm", "disposition"]).size()
        out.append("\n" + ", ".join(f"{arm} {disp}: {n}" for (arm, disp), n in counts.items()))

    section("Blocked by the platform content filter (the agent never ran; scored as recorded)")
    blocked = attempts[attempts["incomplete_reason"] == "content_filter"]
    if blocked.empty:
        out.append("none")
    else:
        for arm, g in blocked.groupby("arm"):
            out.append(f"{arm}: {len(g)} attempts on rows {', '.join(sorted(g['question_id'].unique()))}")

    traces = tables["traces"].drop_duplicates("attempt_id", keep="last")
    timed = done.drop(columns=["tool_time_trace_s"]).merge(traces, on="attempt_id", how="inner", suffixes=("", "_trace"))
    section("Where the time goes (median seconds per completed attempt; Foundry trace + client clock)")
    if timed.empty:
        out.append("no traced attempts")
    else:
        timed["client_overhead_s"] = timed["duration_s"] - timed["agent_span_s"]
        timed["gap_s"] = timed["spans"].map(lambda s: sum(x["duration_s"] for x in (s or []) if x.get("kind") == "gap"))
        timed["schema_s"] = timed["spans"].map(lambda s: sum(x["duration_s"] for x in (s or []) if x.get("kind") == "tool" and "schema" in x["name"]))
        timed["query_s"] = timed["spans"].map(lambda s: sum(x["duration_s"] for x in (s or []) if x.get("kind") == "tool" and "schema" not in x["name"]))
        cols = {
            "duration_s": "client total",
            "client_overhead_s": "client <-> Foundry (outside the agent span)",
            "agent_span_s": "agent span (Foundry)",
            "schema_s": "  schema tool calls",
            "query_s": "  data tool calls (query / Data Agent)",
            "model_time_s": "  last model call (traced)",
            "gap_s": "  earlier model calls + orchestration (untraced gaps)",
            "time_to_first_token_s": "time to first answer token (client)",
            "reasoning_time_s": "reasoning items (stream)",
        }
        med = timed.groupby("arm")[list(cols)].median().round(1).T
        med.index = [cols[c] for c in med.index]
        out.append(med.to_string())
        p95 = timed.groupby("arm")[["duration_s", "agent_span_s", "query_s"]].quantile(0.95).round(1).T
        out.append("\np95: " + "; ".join(f"{c}: " + ", ".join(f"{a}={v}" for a, v in p95.loc[c].items()) for c in p95.index))
        out.append(
            "Foundry emits a model-call span only for the last model call; earlier model calls (deciding and writing each tool"
            " call) show as gaps between spans, together with orchestration."
        )

    tt = tables["tool_timings"].drop_duplicates(["attempt_id", "step_index"], keep="last")
    proxy = tt[tt["arm"] == "fabric"]
    servers = tt[tt["arm"].isin(["dax", "sql"])]
    # Foundry's tool spans and the stream's tool calls are in the same order, so each call gets its Foundry duration.
    steps = tables["steps"]
    spans_rows = []
    for _, r in timed.iterrows() if not timed.empty else []:
        tool_spans = [s for s in (r["spans"] or []) if s.get("kind") == "tool"]
        calls = steps[(steps["attempt_id"] == r["attempt_id"]) & (steps["kind"] == "tool_call")].sort_values("index")
        for span, (_, call) in zip(tool_spans, calls.iterrows(), strict=False):
            spans_rows.append({"attempt_id": r["attempt_id"], "step_index": call["index"], "foundry_s": span["duration_s"]})
    foundry_calls = pd.DataFrame(spans_rows, columns=["attempt_id", "step_index", "foundry_s"])
    if len(servers):
        section("Inside the DAX and SQL tool calls (servers' own spans, median seconds)")
        expanded = pd.concat([servers[["arm", "tool_name", "total_s"]].reset_index(drop=True), pd.json_normalize(servers["phases"].tolist())], axis=1)
        out.append(expanded.groupby(["arm", "tool_name"]).median(numeric_only=True).round(2).to_string())
        hop = servers.merge(foundry_calls, on=["attempt_id", "step_index"])
        if len(hop):
            hop["hop_s"] = hop["foundry_s"] - hop["total_s"]
            out.append("\nFoundry -> server hop (Foundry tool span minus server total), median: "
                       + ", ".join(f"{a}={v:.2f}s" for a, v in hop.groupby("arm")["hop_s"].median().items()))
        out.append("joined by: " + ", ".join(f"{k}={v}" for k, v in servers["linked_by"].value_counts().items()))
    if len(proxy):
        section("Inside the arm 3 tool call (proxy's own clock, median seconds)")
        tcols = ["total_s", "sign_in_s", "session_open_s", "tool_discovery_s", "upstream_call_s", "session_close_s"]
        out.append(proxy[tcols].median().round(2).to_string())
        hop = proxy.merge(foundry_calls, on=["attempt_id", "step_index"])
        if len(hop):
            out.append(
                f"Foundry -> tunnel -> proxy hop (Foundry tool span minus proxy total), median: "
                f"{(hop['foundry_s'] - hop['total_s']).median():.2f} s; joined calls: {len(proxy)}"
            )

    section("Tokens (mean per completed attempt; agent only)")
    tok = timed if not timed.empty else done
    tcols = {
        "input_tokens": "input (Responses API usage)",
        "output_tokens": "output (API, includes reasoning)",
        "reasoning_tokens": "reasoning (API)",
        "cached_input_tokens": "cached input (API; reports 0)",
        "model_input_tokens": "input, last model call (trace)",
        "model_output_tokens": "output, last model call (trace)",
        "model_cache_read_tokens": "prompt-cache read, last model call (trace)",
        "model_cache_creation_tokens": "prompt-cache write, last model call (trace)",
    }
    present = [c for c in tcols if c in tok.columns]
    tmean = tok.groupby("arm")[present].mean().round(0).T
    tmean.index = [tcols[c] for c in tmean.index]
    out.append(tmean.to_string())
    if "model_cache_read_tokens" in tok.columns:
        rate = tok.groupby("arm").apply(lambda g: g["model_cache_read_tokens"].sum() / max(g["model_input_tokens"].sum(), 1))
        out.append("prompt-cache hit rate on the last model call: " + ", ".join(f"{a}={pct(v)}" for a, v in rate.items()))

    section("Tool call timings (Foundry tool spans, seconds)")
    rows_t = []
    for _, r in timed.iterrows() if not timed.empty else []:
        for s in r["spans"] or []:
            if s.get("kind") == "tool":
                rows_t.append({"arm": r["arm"], "tool": s["name"].split(".")[-1], "s": s["duration_s"]})
    if rows_t:
        tdf = pd.DataFrame(rows_t)
        agg = tdf.groupby(["arm", "tool"])["s"].agg(calls="count", p50="median", p95=lambda s: s.quantile(0.95), max="max").round(1)
        out.append(agg.to_string())

    section("By question category and tier (which arm does what well; correct = all criteria met)")
    meta = {}
    dataset = ROOT / "evals" / "eval-dataset.jsonl"
    if dataset.exists():
        for line in dataset.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            meta[row["id"]] = (row.get("category"), row.get("tier"))
    scored = cj.merge(
        attempts[["attempt_id", "duration_s", "total_tokens"]].rename(columns={"duration_s": "attempt_s", "total_tokens": "attempt_tokens"}),
        on="attempt_id",
        how="left",
    )
    scored["category"] = scored["question_id"].map(lambda q: (meta.get(q) or (None, None))[0])
    scored["tier"] = scored["question_id"].map(lambda q: (meta.get(q) or (None, None))[1])
    for key in ("category", "tier"):
        grid = scored.groupby([key, "arm"]).agg(
            n=("score", "size"), correct=("score", lambda s: (s == 1.0).mean()), p50_s=("attempt_s", "median"),
            tokens=("attempt_tokens", "mean"),
        )
        wide = grid["correct"].unstack("arm").map(pct)
        speed = grid["p50_s"].unstack("arm").round(1)
        best = grid.reset_index().sort_values(["correct", "p50_s"], ascending=[False, True]).groupby(key).first()["arm"]
        table_k = wide.join(speed, rsuffix=" p50 s").join(best.rename("best arm"))
        out.append(f"\nby {key}:\n" + table_k.to_string())
    out.append("best arm = highest correct rate, ties broken by lower median latency.")

    text_out = "\n".join(out)
    print(text_out)
    if args.out:
        dest = ROOT / args.out
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text_out, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
