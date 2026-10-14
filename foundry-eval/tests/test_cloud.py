"""Cloud built-ins run in small batches with a bounded number in flight, and a stuck run is cancelled, not left queued."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from types import SimpleNamespace

from foundry_eval import RunStore
from foundry_eval.cloud import evaluate_run_in_cloud
from foundry_eval.records import AttemptRecord


class FakeEvals:
    def __init__(self, stuck: bool = False, fail_first: int = 0) -> None:
        self.stuck = stuck
        self.fail_first = fail_first
        self.batches: list[list[str]] = []
        self.cancelled: list[str] = []
        self.in_flight = self.peak = 0
        self._lock = threading.Lock()
        self._runs: dict[str, list[str]] = {}
        self.runs = SimpleNamespace(
            create=self._create_run, retrieve=self._retrieve, cancel=self._cancel, output_items=SimpleNamespace(list=self._items)
        )

    def create(self, **kwargs):
        return SimpleNamespace(id=f"eval-{kwargs['name']}")

    def _create_run(self, eval_id, name, data_source):
        ids = [c["item"]["resp_id"] for c in data_source["item_generation_params"]["source"]["content"]]
        with self._lock:
            run_id = f"run-{len(self._runs)}"
            self._runs[run_id] = ids
            self.batches.append(ids)
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        return SimpleNamespace(id=run_id, status="queued")

    def _retrieve(self, run_id, eval_id):
        if self.stuck:
            return SimpleNamespace(id=run_id, status="in_progress")
        with self._lock:
            self.in_flight -= 1
            if self.fail_first:
                self.fail_first -= 1
                return SimpleNamespace(id=run_id, status="failed")
        return SimpleNamespace(id=run_id, status="completed")

    def _cancel(self, run_id, eval_id):
        self.cancelled.append(run_id)

    def _items(self, run_id, eval_id):
        return [
            SimpleNamespace(
                model_dump=lambda mode, rid=rid: {
                    "id": f"item-{rid}",
                    "datasource_item": {"response_id": rid},
                    "results": [{"name": "relevance", "status": "completed", "score": 4.0, "passed": True}],
                }
            )
            for rid in self._runs[run_id]
        ]


def store_with(tmp_path, n: int) -> RunStore:
    store = RunStore.create(tmp_path, arm="dax")
    now = datetime.now(UTC)
    for i in range(n):
        attempt = AttemptRecord(
            attempt_id=f"a{i}", run_id=store.run.run_id, arm="dax", question_id=f"Q{i:02d}", question="?", answer_text="x",
            status="completed", started_at=now, ended_at=now, duration_s=1.0, timing_basis="total_only", response_id=f"resp_{i}",
        )
        store.add_attempt(attempt, [])
    return store


def test_responses_go_in_batches_of_five_with_at_most_five_in_flight(tmp_path):
    store = store_with(tmp_path, 23)
    evals = FakeEvals()
    records, failures = evaluate_run_in_cloud(
        SimpleNamespace(evals=evals), store, "judge", {"relevance": "quality"}, batch_size=5, workers=5
    )
    assert failures == {}
    assert sorted(len(b) for b in evals.batches) == [3, 5, 5, 5, 5]
    assert evals.peak <= 5
    assert {r.attempt_id for r in records} == {f"a{i}" for i in range(23)}
    assert len(store.evaluations()) == 23
    again, _ = evaluate_run_in_cloud(SimpleNamespace(evals=evals), store, "judge", {"relevance": "quality"})
    assert again == [], "a re-run sends only what has no result yet"


def test_a_stuck_batch_is_cancelled_and_reported(tmp_path):
    store = store_with(tmp_path, 2)
    evals = FakeEvals(stuck=True)
    records, failures = evaluate_run_in_cloud(
        SimpleNamespace(evals=evals), store, "judge", {"relevance": "quality"}, timeout_s=0.0
    )
    assert records == [] and "timeout" in failures["relevance"]
    assert evals.cancelled == ["run-0"]


def test_a_run_the_service_fails_outright_is_retried(tmp_path, monkeypatch):
    monkeypatch.setattr("foundry_eval.cloud.time.sleep", lambda s: None)
    store = store_with(tmp_path, 3)
    evals = FakeEvals(fail_first=1)
    records, failures = evaluate_run_in_cloud(SimpleNamespace(evals=evals), store, "judge", {"relevance": "quality"})
    assert failures == {} and len(records) == 3
    assert len(evals.batches) == 2, "one failed run, then one fresh run"
