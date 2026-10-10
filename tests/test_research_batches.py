import copy
import threading
import time

import pytest

from quant_studio import QuantStudioError
from quant_studio.jobs import JobManager
from quant_studio.recipes import RecipeStore
from quant_studio.research_batches import (
    BatchStore,
    BatchSupervisor,
    job_manager_enqueue,
)
from quant_studio.templates import load_template


def _recipe(root, template_id="crypto-basis-fixture"):
    template = load_template(template_id)
    return RecipeStore(root).save(
        "批量研究",
        template_id,
        copy.deepcopy(template.base_config),
        snapshot=(
            str((root / "input").resolve())
            if template.metadata.get("requires_snapshot")
            else None
        ),
    )


def _wait_batch(store, batch_id, manager, timeout=8):
    deadline = time.monotonic() + timeout
    enqueue = job_manager_enqueue(manager, profile=None)
    while time.monotonic() < deadline:
        record = store.status(
            batch_id,
            lookup=manager.get,
            enqueue=enqueue,
            owner_id=manager.instance_id,
        )
        if record["status"] in {"completed", "cancelled", "interrupted"}:
            return record
        time.sleep(0.01)
    raise AssertionError(store.status(batch_id, lookup=manager.get))


def test_prepare_counts_before_expansion_and_freezes_exact_recipe_revision(tmp_path):
    recipes = RecipeStore(tmp_path)
    template = load_template("crypto-basis-fixture")
    first_config = copy.deepcopy(template.base_config)
    first_config["seed"] = 9
    first = recipes.save("第一版", template.id, first_config)
    second_config = copy.deepcopy(first_config)
    second_config["seed"] = 99
    recipes.save(
        "第二版",
        template.id,
        second_config,
        recipe_id=first["id"],
        expected=first["revision"],
    )
    store = BatchStore(tmp_path)
    prepared = store.prepare(
        first["id"],
        first["revision"],
        {"source": ["binance", "okx"], "seed": [1, 2]},
        max_candidates=4,
    )
    assert prepared["recipe"]["revision"] == first["revision"]
    assert prepared["recipe"]["config"]["seed"] == 9
    assert prepared["candidate_count"] == 4
    assert [row["knobs"] for row in prepared["candidates"]] == [
        {"seed": 1, "source": "binance"},
        {"seed": 2, "source": "binance"},
        {"seed": 1, "source": "okx"},
        {"seed": 2, "source": "okx"},
    ]
    with pytest.raises(QuantStudioError, match="候选数"):
        store.prepare(
            first["id"],
            first["revision"],
            {"source": ["binance", "okx"], "seed": list(range(1000))},
            max_candidates=10,
        )
    with pytest.raises(QuantStudioError, match="有限数组"):
        store.prepare(
            first["id"],
            first["revision"],
            {"seed": (value for value in range(2))},
            max_candidates=10,
        )


def test_declared_date_and_seed_knobs_are_accepted_but_unknown_values_are_rejected(
    tmp_path,
):
    fund = _recipe(tmp_path, "fund-fof")
    dated = BatchStore(tmp_path).prepare(
        fund["id"],
        fund["revision"],
        {"start": ["2024-01-02", "2024-02-01"]},
        max_candidates=2,
    )
    assert dated["candidate_count"] == 2
    crypto = _recipe(tmp_path, "crypto-basis-fixture")
    seeded = BatchStore(tmp_path).prepare(
        crypto["id"], crypto["revision"], {"seed": [0, 7]}, max_candidates=2
    )
    assert seeded["candidate_count"] == 2
    with pytest.raises(QuantStudioError, match="未知参数"):
        BatchStore(tmp_path).prepare(
            crypto["id"], crypto["revision"], {"profit": [1]}, max_candidates=1
        )
    with pytest.raises(QuantStudioError, match="参数 seed 无效"):
        BatchStore(tmp_path).prepare(
            crypto["id"], crypto["revision"], {"seed": [-1]}, max_candidates=1
        )


def test_fifo_preflight_gate_preserves_partial_failures_and_every_attempt(tmp_path):
    recipe = _recipe(tmp_path)
    actions = []

    def execute(job, _control):
        actions.append((job["action"], job["knobs"]["seed"]))
        if job["action"] == "check":
            status = "check_failed" if job["knobs"]["seed"] == 2 else "checked"
        else:
            status = "failed" if job["knobs"]["seed"] == 3 else "succeeded"
        return {"status": status, "run_id": f"{job['action']}-{job['knobs']['seed']}"}

    manager = JobManager(tmp_path, execute=execute)
    store = BatchStore(tmp_path)
    try:
        batch = store.prepare(
            recipe["id"], recipe["revision"], {"seed": [1, 2, 3]}, max_candidates=3
        )
        submitted = store.submit(
            batch["batch_id"],
            submission_key="first-click",
            enqueue=job_manager_enqueue(manager, profile=None),
            owner_id=manager.instance_id,
        )
        repeated = store.submit(
            batch["batch_id"],
            submission_key="first-click",
            enqueue=job_manager_enqueue(manager, profile=None),
            owner_id=manager.instance_id,
        )
        assert repeated["submission_key"] == submitted["submission_key"]
        done = _wait_batch(store, batch["batch_id"], manager)
        assert actions == [
            ("check", 1),
            ("execute", 1),
            ("check", 2),
            ("check", 3),
            ("execute", 3),
        ]
        assert done["summary"] == {
            "prepared": 0,
            "active": 0,
            "succeeded": 1,
            "failed": 2,
            "cancelled": 0,
        }
        assert [row["status"] for row in done["candidates"]] == [
            "succeeded",
            "preflight_failed",
            "execution_failed",
        ]
        assert all(len(row["attempts"]) == 1 for row in done["candidates"])
    finally:
        manager.close()


def test_supervisor_completes_persisted_batch_without_status_get_polling(tmp_path):
    recipe = _recipe(tmp_path)
    actions = []

    def execute(job, _control):
        actions.append((job["action"], job["knobs"]["seed"]))
        return {
            "status": "checked" if job["action"] == "check" else "succeeded",
            "run_id": f"{job['action']}-{job['knobs']['seed']}",
        }

    manager = JobManager(tmp_path, execute=execute)
    store = BatchStore(tmp_path)
    supervisor = BatchSupervisor(manager, store=store, profile=None, poll_interval=0.01)
    try:
        batch = store.prepare(
            recipe["id"], recipe["revision"], {"seed": [4, 5]}, max_candidates=2
        )
        supervisor.submit(batch["batch_id"], submission_key="browser-click")
        deadline = time.monotonic() + 8
        while store.get(batch["batch_id"])["status"] != "completed":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert actions == [
            ("check", 4),
            ("execute", 4),
            ("check", 5),
            ("execute", 5),
        ]
    finally:
        supervisor.close()
        manager.close()
    assert not supervisor._thread.is_alive()


def test_cancel_remaining_retry_failed_and_restart_does_not_resume(tmp_path):
    recipe = _recipe(tmp_path)
    store = BatchStore(tmp_path)
    batch = store.prepare(
        recipe["id"], recipe["revision"], {"seed": [1, 2]}, max_candidates=2
    )
    jobs = {}
    calls = []

    def enqueue(**request):
        job_id = f"job-{len(calls)}"
        calls.append(request)
        jobs[job_id] = {"job_id": job_id, "status": "queued", "run_id": None}
        return jobs[job_id]

    submitted = store.submit(
        batch["batch_id"],
        submission_key="submit",
        enqueue=enqueue,
        owner_id="old-server",
    )
    observed = store.status(
        batch["batch_id"],
        lookup=jobs.__getitem__,
        enqueue=enqueue,
        owner_id="old-server",
    )
    observed_again = store.status(
        batch["batch_id"],
        lookup=jobs.__getitem__,
        enqueue=enqueue,
        owner_id="old-server",
    )
    assert observed["revision"] == observed_again["revision"] == submitted["revision"]
    cancelled = store.cancel(
        batch["batch_id"],
        cancel=lambda job_id: jobs[job_id].update(status="cancelled") or jobs[job_id],
    )
    assert cancelled["status"] in {"cancelling", "cancelled"}
    final = store.status(batch["batch_id"], lookup=jobs.__getitem__)
    assert final["status"] == "cancelled"
    assert final["summary"]["cancelled"] == 2

    failed_batch = store.prepare(
        recipe["id"], recipe["revision"], {"seed": [3, 4]}, max_candidates=2
    )
    store.submit(
        failed_batch["batch_id"],
        submission_key="submit-failed",
        enqueue=enqueue,
        owner_id="old-server",
    )
    active_job = store.get(failed_batch["batch_id"])["candidates"][0]["attempts"][0][
        "preflight_job_id"
    ]
    jobs[active_job].update(status="interrupted")
    interrupted = store.status(
        failed_batch["batch_id"],
        lookup=jobs.__getitem__,
        enqueue=enqueue,
        owner_id="new-server",
    )
    assert interrupted["status"] == "interrupted"
    assert [row["status"] for row in interrupted["candidates"]] == [
        "interrupted",
        "interrupted",
    ]
    assert len(calls) == 2
    retried = store.retry(
        failed_batch["batch_id"],
        [failed_batch["candidates"][0]["candidate_id"]],
        retry_key="retry-1",
        enqueue=enqueue,
        owner_id="new-server",
    )
    assert len(retried["candidates"][0]["attempts"]) == 2
    assert retried["candidates"][0]["attempts"][0]["status"] == "interrupted"
    assert retried["candidates"][1]["status"] == "interrupted"
    assert len(calls) == 3


def test_concurrent_duplicate_submission_dispatches_only_once(tmp_path):
    recipe = _recipe(tmp_path)
    first = BatchStore(tmp_path)
    batch = first.prepare(
        recipe["id"], recipe["revision"], {"seed": [1]}, max_candidates=1
    )
    with pytest.raises(QuantStudioError, match="已更新"):
        first.submit(
            batch["batch_id"],
            submission_key="same-key",
            enqueue=lambda **_: {"job_id": "must-not-run", "status": "queued"},
            owner_id="server",
            expected="0" * 64,
        )
    assert first.get(batch["batch_id"])["status"] == "prepared"
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def enqueue(**request):
        calls.append(request)
        entered.set()
        assert release.wait(5)
        return {"job_id": "only-job", "status": "queued"}

    errors = []

    def submit(store):
        try:
            store.submit(
                batch["batch_id"],
                submission_key="same-key",
                enqueue=enqueue,
                owner_id="server",
                expected=batch["revision"],
            )
        except Exception as exc:  # pragma: no cover - assertion reports details
            errors.append(exc)

    one = threading.Thread(target=submit, args=(first,))
    two = threading.Thread(target=submit, args=(BatchStore(tmp_path),))
    one.start()
    assert entered.wait(5)
    two.start()
    time.sleep(0.05)
    release.set()
    one.join(5)
    two.join(5)
    assert errors == []
    assert len(calls) == 1
    assert BatchStore(tmp_path).get(batch["batch_id"])["revision"]
