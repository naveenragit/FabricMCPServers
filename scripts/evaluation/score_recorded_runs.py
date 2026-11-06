"""Evaluate stored runs: local evaluators (deterministic + judges) and, on request, the Foundry cloud built-ins.

Run: .\.venv\Scripts\python.exe scripts\evaluation\score_recorded_runs.py <run_id> [<run_id> ...] [--scoring judge|deterministic] [--cloud]
    [--no-local] [--workers 6] [--cloud-batch 5] [--cloud-workers 5] [--dataset <jsonl>]
The LLM judge runs only where the deterministic checks cannot decide (D23); --scoring deterministic uses no judge and
marks undecided criteria and behaviors needs_review. The cloud built-ins are LLM-judged and
optional (--cloud); they go in runs of --cloud-batch responses, at most --cloud-workers runs at a time.
--dataset must be the file the runs were made with; a hash mismatch stops the run.
Trace join (Foundry spans for tool errors) is separate: python -m foundry_eval traces artifacts/runs <run_id>
"""

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential

from foundry_eval import RunStore
from foundry_eval.cloud import BUILTINS, evaluate_run_in_cloud
from foundry_eval.dataset import dataset_sha256, load_dataset
from foundry_eval.evaluators import SCORING_MODES, config_from_env, evaluators_for
from foundry_eval.evaluators.judges import FoundryJudge
from foundry_eval.evaluators.runner import evaluate_attempt
from foundry_eval.net import install_dns_retry

install_dns_retry()

ROOT = Path(__file__).resolve().parents[2]
CLOUD = {k: v for k, v in BUILTINS.items() if k != "response_completeness"}


def load_env() -> None:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_ids", nargs="+")
    parser.add_argument("--cloud", action="store_true", help="also run the Foundry cloud built-ins (LLM-judged)")
    parser.add_argument("--no-local", action="store_true")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--cloud-batch", type=int, default=5, help="responses per cloud eval run")
    parser.add_argument("--cloud-workers", type=int, default=5, help="cloud eval runs in flight at once")
    parser.add_argument("--scoring", choices=SCORING_MODES, default="judge", help="deterministic: no LLM judge")
    parser.add_argument("--dataset", default=str(ROOT / "evaluation" / "datasets" / "wealth-management-benchmark.jsonl"))
    args = parser.parse_args()

    load_env()
    dataset = Path(args.dataset)
    digest = dataset_sha256(dataset)
    stores = [RunStore.open(ROOT / "artifacts" / "runs", run_id) for run_id in args.run_ids]
    stale = [s.run.run_id for s in stores if s.run.dataset_sha256 and s.run.dataset_sha256 != digest]
    if stale:
        sys.exit(f"{dataset.name} ({digest[:16]}) is not the dataset these runs used: {', '.join(stale)}. Pass --dataset.")
    judge_model = os.environ["FOUNDRY_JUDGE_MODEL"] if args.scoring == "judge" or args.cloud else None
    client = None
    if judge_model:
        project = AIProjectClient(endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"], credential=AzureCliCredential())
        client = project.get_openai_client()
    rows = {r.id: r for r in load_dataset(dataset)}
    config = config_from_env(os.environ)
    evaluators = evaluators_for(args.scoring, FoundryJudge(client, judge_model, "medium") if args.scoring == "judge" else None)
    local_judge = judge_model if args.scoring == "judge" else None

    for store in stores:
        run_id = store.run.run_id
        print(f"== {run_id}: {len(store.attempts())} attempts", flush=True)
        if not args.no_local:
            steps: dict[str, list] = {}
            for step in store.steps():
                steps.setdefault(step.attempt_id, []).append(step)
            done = {(e.attempt_id, e.evaluator, e.evaluator_version) for e in store.evaluations() if e.status == "ok"}

            def local(attempt, store=store, steps=steps, done=done):
                row = rows.get(attempt.question_id)
                if row is None:
                    return []
                wanted = [e for e in evaluators if (attempt.attempt_id, e.name, e.version) not in done]
                records = evaluate_attempt(
                    attempt, sorted(steps.get(attempt.attempt_id, []), key=lambda s: s.index), row, wanted, config, judge_model=local_judge
                )
                store.add_evaluations(records)
                return records

            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                scoreable = [a for a in store.attempts() if not a.infra_error]  # infrastructure errors are not scored
                results = [r for batch in pool.map(local, scoreable) for r in batch]
            judged = sum(1 for r in results if r.judge_input_tokens is not None)
            print(f"   local evaluators: {len(results)} results, {judged} needed an LLM judge call", flush=True)
        if args.cloud:
            records, failures = evaluate_run_in_cloud(
                client,
                store,
                judge_model,
                CLOUD,
                batch_size=args.cloud_batch,
                workers=args.cloud_workers,
                progress=lambda line: print(line, flush=True),
            )
            ok = sum(r.status == "ok" for r in records)
            print(f"   cloud built-ins: {ok}/{len(records)} ok, {len(failures)} evaluators with failed batches", flush=True)
            for short, message in failures.items():
                print(f"     FAILED {short}: {message}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
