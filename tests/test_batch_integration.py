import copy
import time

from quant_studio import QuantStudioError
from quant_studio.jobs import JobManager
from quant_studio.recipes import RecipeStore
from quant_studio.research_batches import BatchStore, BatchSupervisor
from quant_studio.settings import current_settings
from quant_studio.templates import load_template
from tests.test_server import _request, _start_http_server


def test_batch_keeps_profile_when_settings_change_between_check_and_execute(tmp_path):
    selected = {"environment": {"config": "first"}}
    observed = []

    def execute(job, _control):
        observed.append(copy.deepcopy(current_settings()))
        selected["environment"]["config"] = "changed"
        return {"status": "checked" if job["action"] == "check" else "succeeded"}

    template = load_template("crypto-basis-fixture")
    recipe = RecipeStore(tmp_path).save("配置冻结", template.id, template.base_config)
    store = BatchStore(tmp_path)
    batch = store.prepare(
        recipe["id"], recipe["revision"], {"seed": [3]}, max_candidates=1
    )
    manager = JobManager(tmp_path, execute=execute)
    supervisor = BatchSupervisor(manager, store=store, profile=lambda: selected)
    try:
        supervisor.submit(batch["batch_id"], submission_key="test")
        deadline = time.monotonic() + 5
        while store.get(batch["batch_id"])["status"] != "completed":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert observed == [{"environment": {"config": "first"}}] * 2
    finally:
        supervisor.close()
        manager.close()


def test_batches_are_routed_and_require_csrf(tmp_path):
    server, thread = _start_http_server(tmp_path)
    host = f"localhost:{server.server_port}"
    try:
        status, _, body = _request(server, "GET", "/batches", host=host)
        assert status == 200
        assert "批量实验".encode() in body
        status, _, _ = _request(
            server, "POST", "/batches/prepare", host=host, origin=f"http://{host}"
        )
        assert status == 403
        assert not list((tmp_path / ".batches").glob("*.json"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_dispatch_failure_is_persisted_and_does_not_leave_batch_running(tmp_path):
    template = load_template("crypto-basis-fixture")
    recipe = RecipeStore(tmp_path).save("派发失败", template.id, template.base_config)
    store = BatchStore(tmp_path)
    batch = store.prepare(
        recipe["id"], recipe["revision"], {"seed": [1, 2]}, max_candidates=2
    )

    def reject(**_request):
        raise QuantStudioError("冻结配置完整性失败")

    record = store.submit(
        batch["batch_id"], submission_key="submit", enqueue=reject, owner_id="server"
    )
    assert record["status"] == "interrupted"
    assert all(row["status"] == "interrupted" for row in record["candidates"])
    assert "完整性" in record["candidates"][0]["attempts"][0]["preflight_message"]
    assert store.get(batch["batch_id"])["status"] == "interrupted"
