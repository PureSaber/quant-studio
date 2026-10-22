import json
from copy import deepcopy
from pathlib import Path

import pytest

from quant_studio import QuantStudioError
from quant_studio.durable_research import DurableResearchClient
from tests import test_durable_research as research_tests
from tests.test_server import _request, _start_http_server


@pytest.fixture
def configured(tmp_path, monkeypatch):
    return research_tests.configured.__wrapped__(tmp_path, monkeypatch)


def complete_config(configured):
    path, value = configured
    value = deepcopy(value)
    value["snapshots"]["fixed"]["request"].update(
        project_id="research-project", inputs=[], timeout_seconds=30, max_attempts=2
    )
    path.write_text(json.dumps(value), encoding="utf-8")
    return DurableResearchClient(path)


def test_batch_freezes_shared_input_and_keeps_project_identity(configured, monkeypatch):
    client = complete_config(configured)
    calls = []
    monkeypatch.setattr(client, "_call", lambda *args: calls.append(args) or {})
    client.submit_batch("fixed", "5, 20,60", "a" * 32, client.revision("fixed"))
    assert calls[0][0] == "batch-submit" and calls[0][-1] == "--research"
    saved = json.loads(Path(calls[0][2]).read_text(encoding="utf-8"))
    assert saved["project_id"] == "research-project"
    assert saved["shared_inputs"] == []
    assert [row["candidate_id"] for row in saved["candidates"]] == [
        "window-5",
        "window-20",
        "window-60",
    ]
    assert [
        row["payload"]["factors"][0]["window_periods"] for row in saved["candidates"]
    ] == [5, 20, 60]
    assert (
        client.snapshots["fixed"]["request"]["payload"]["factors"][0]["window_periods"]
        == 5
    )


@pytest.mark.parametrize(
    "windows", ["", "0", "5,5", "5,", "4097", "20;whoami", "1," * 65]
)
def test_invalid_batch_never_reaches_cli(configured, monkeypatch, windows):
    client = complete_config(configured)
    monkeypatch.setattr(
        client, "_call", lambda *_: pytest.fail("invalid batch reached CLI")
    )
    with pytest.raises(QuantStudioError, match="批次需要"):
        client.submit_batch("fixed", windows, "a" * 32, client.revision("fixed"))


def batch_state():
    return {
        "batch_id": "a" * 32,
        "status": "failed",
        "terminal": True,
        "retry_of": None,
        "request": {"project_id": "<script>project</script>", "run_id": "batch"},
        "counts": {
            "queued": 0,
            "running": 0,
            "failed": 1,
            "cancelled": 0,
            "succeeded": 1,
        },
        "members": [
            {
                "candidate_id": "window-5",
                "job": {
                    "job_id": "b" * 32,
                    "status": "failed",
                    "request": {"kind": "rolling_research"},
                },
            },
            {
                "candidate_id": "window-20",
                "job": {
                    "job_id": "c" * 32,
                    "status": "succeeded",
                    "request": {"kind": "rolling_research"},
                },
            },
        ],
    }


def test_retry_only_failed_candidates_and_rejects_foreign_batch(
    configured, monkeypatch
):
    client = complete_config(configured)
    calls = []
    state = batch_state()

    def execute(*args):
        calls.append(args)
        return deepcopy(state)

    monkeypatch.setattr(client, "_call", execute)
    client.batch("retry", "a" * 32, "d" * 32)
    assert calls[-1][-2:] == ("--candidate", "window-5")
    assert "window-20" not in calls[-1]
    state["members"][1]["job"]["request"]["kind"] = "foreign"
    calls.clear()
    with pytest.raises(QuantStudioError, match="不属于"):
        client.batch("cancel", "a" * 32)
    assert calls == [("batch-get", "a" * 32)]


def test_batch_http_form_uses_csrf_and_preserves_history_links(
    configured, monkeypatch, tmp_path
):
    client = complete_config(configured)
    calls = []

    def execute(self, *args):
        calls.append(args)
        if args[0] == "capabilities":
            return {"batch_request": "quant.job-batch/v1"}
        if args[0] in {"list", "batch-list"}:
            return []
        return batch_state()

    monkeypatch.setattr(DurableResearchClient, "_call", execute)
    server, thread = _start_http_server(tmp_path / "runs")
    host = f"127.0.0.1:{server.server_port}"
    origin = f"http://{host}"
    fields = {
        "snapshot": "fixed",
        "windows": "5,20",
        "key": "d" * 32,
        "revision": client.revision("fixed"),
    }
    try:
        status, _, body = _request(server, "GET", "/durable-research", host=host)
        assert status == 200 and b"/durable-research/batch-submit" in body
        for extra, expected in [
            ({}, 403),
            ({"_csrf_token": "test-only-csrf-token", "handler": "x"}, 400),
            ({"_csrf_token": "test-only-csrf-token"}, 303),
        ]:
            status, headers, _ = _request(
                server,
                "POST",
                "/durable-research/batch-submit",
                host=host,
                origin=origin,
                fields=fields | extra,
            )
            assert status == expected
            if status == 303:
                assert headers["Location"] == "/durable-research/batches/" + "a" * 32
        status, _, body = _request(
            server, "GET", "/durable-research/batches/" + "a" * 32, host=host
        )
        assert status == 200 and b"<script>project</script>" not in body
        assert b"&lt;script&gt;project&lt;/script&gt;" in body
        assert ("/durable-research/" + "b" * 32).encode() in body
        status, _, _ = _request(
            server,
            "POST",
            "/durable-research/batches/" + "a" * 32 + "/retry",
            host=host,
            origin=origin,
            fields={"key": "d" * 32, "_csrf_token": "test-only-csrf-token"},
        )
        assert status == 303
        assert calls[-1][-2:] == ("--candidate", "window-5")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_old_runtime_capability_probe_keeps_single_study_compatible(
    configured, monkeypatch
):
    client = complete_config(configured)

    def unavailable(*_):
        raise QuantStudioError("older CLI")

    monkeypatch.setattr(client, "_call", unavailable)
    assert not client.supports_batches()
    monkeypatch.setattr(client, "_call", lambda *_: [])
    assert not client.supports_batches()
    monkeypatch.setattr(
        client, "_call", lambda *_: {"batch_request": "quant.job-batch/v1"}
    )
    assert client.supports_batches()
