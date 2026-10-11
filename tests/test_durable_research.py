import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from quant_studio import QuantStudioError
from quant_studio.durable_research import DurableResearchClient
from tests.test_server import _request, _start_http_server


@pytest.fixture
def configured(tmp_path, monkeypatch):
    config = {
        "schema_version": "quant-studio.durable-research/v1",
        "python": sys.executable,
        "database": str(tmp_path / "jobs.db"),
        "artifact_root": str(tmp_path / "artifacts"),
        "cache_database": str(tmp_path / "cache.db"),
        "requests_root": str(tmp_path / "requests"),
        "snapshots": {
            "fixed": {
                "title": '<script>alert("x")</script>',
                "request": {
                    "kind": "rolling_research",
                    "run_id": "admin-request",
                    "payload": {
                        "snapshot": {"identity": "admin-owned-immutable-input"},
                        "factors": [{"name": "momentum", "window_periods": 5}],
                    },
                },
            }
        },
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("QUANT_STUDIO_DURABLE_CONFIG", str(path))
    return path, config


def test_submit_preserves_admin_input_and_content_identity(configured, monkeypatch):
    path, original = configured
    client = DurableResearchClient(path)
    commands = []

    def execute(*args):
        commands.append(args)
        return {"job_id": "a" * 32}

    monkeypatch.setattr(client, "_call", execute)
    for _ in range(2):
        client.submit("fixed", "20", "b" * 32, client.revision("fixed"))
    assert commands[0] == commands[1]
    assert commands[0][-3:] == ("--key", "studio/" + "b" * 32, "--research")
    files = list(Path(original["requests_root"]).iterdir())
    assert len(files) == 1 and files[0].suffix == ".json"
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert (
        saved["payload"]["snapshot"]
        == original["snapshots"]["fixed"]["request"]["payload"]["snapshot"]
    )
    assert saved["payload"]["factors"] == [{"name": "momentum", "window_periods": 20}]
    assert client.config == original


@pytest.mark.parametrize("window", ["0", "-1", "4097", "5.0", "20;whoami"])
def test_invalid_window_never_calls_worker(configured, monkeypatch, window):
    client = DurableResearchClient(configured[0])
    monkeypatch.setattr(
        client, "_call", lambda *_: pytest.fail("invalid input reached CLI")
    )
    with pytest.raises(QuantStudioError, match="观察窗口"):
        client.submit("fixed", window, "b" * 32, client.revision("fixed"))


def test_changed_config_and_browser_supplied_paths_are_rejected(
    configured, monkeypatch
):
    client = DurableResearchClient(configured[0])
    monkeypatch.setattr(
        client, "_call", lambda *_: pytest.fail("invalid input reached CLI")
    )
    with pytest.raises(QuantStudioError, match="配置已经改变"):
        client.submit("fixed", "20", "b" * 32, "0" * 64)
    with pytest.raises(QuantStudioError, match="快照"):
        client.submit("../untrusted.json", "20", "b" * 32, "0" * 64)


def test_client_isolates_python_and_reports_subprocess_failure(configured, monkeypatch):
    client = DurableResearchClient(configured[0])
    monkeypatch.setenv("PYTHONPATH", "untrusted-source")
    monkeypatch.setenv("QUANT_RESEARCH_REGISTRY_DB", "wrong-registry")
    observed = {}

    def execute(command, **options):
        observed.update(command=command, options=options)
        return subprocess.CompletedProcess(command, 1, "", "private-error-details")

    monkeypatch.setattr(subprocess, "run", execute)
    with pytest.raises(QuantStudioError) as error:
        client.list()
    assert "private-error-details" not in str(error.value)
    assert observed["command"][1:4] == ["-I", "-m", "quant_pipeline.durable_client"]
    environment = observed["options"]["env"]
    assert "PYTHONPATH" not in environment
    assert "QUANT_RESEARCH_REGISTRY_DB" not in environment
    assert environment["QUANT_RESEARCH_STORE_ROOT"] == configured[1]["artifact_root"]


def test_foreign_job_cannot_be_cancelled(configured, monkeypatch):
    client = DurableResearchClient(configured[0])
    calls = []

    def execute(*args):
        calls.append(args)
        return {"request": {"kind": "other"}}

    monkeypatch.setattr(client, "_call", execute)
    with pytest.raises(QuantStudioError, match="不属于"):
        client.query("cancel", "a" * 32)
    assert calls == [("get", "a" * 32)]


@pytest.mark.parametrize(
    "payload", [None, [], {"payload": []}, {"payload": {"factors": [None]}}]
)
def test_malformed_config_is_a_user_visible_error(configured, payload):
    path, config = configured
    value = deepcopy(config)
    value["snapshots"]["fixed"]["request"] = payload
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(QuantStudioError):
        DurableResearchClient(path)


def test_http_submission_uses_csrf_and_rejects_extra_fields(
    configured, monkeypatch, tmp_path
):
    calls = []

    def execute(self, *args):
        calls.append(args)
        if args[0] == "list":
            return []
        return {"job_id": "a" * 32}

    monkeypatch.setattr(DurableResearchClient, "_call", execute)
    server, thread = _start_http_server(tmp_path / "runs")
    host = f"127.0.0.1:{server.server_port}"
    origin = f"http://{host}"
    fields = {
        "snapshot": "fixed",
        "window": "20",
        "key": "b" * 32,
        "revision": DurableResearchClient(configured[0]).revision("fixed"),
    }
    try:
        status, _, body = _request(server, "GET", "/durable-research", host=host)
        assert status == 200 and b"<script>alert" not in body
        assert b"&lt;script&gt;" in body
        calls.clear()
        for extra, expected in [
            ({}, 403),
            ({"_csrf_token": "test-only-csrf-token", "handler": "untrusted"}, 400),
            ({"_csrf_token": "test-only-csrf-token"}, 303),
        ]:
            status, headers, _ = _request(
                server,
                "POST",
                "/durable-research/submit",
                host=host,
                origin=origin,
                fields=fields | extra,
            )
            assert status == expected
            if status == 303:
                assert headers["Location"] == "/durable-research/" + "a" * 32
        assert len(calls) == 1
        status, _, _ = _request(
            server,
            "POST",
            "/durable-research/submit",
            host=host,
            origin="http://foreign.invalid",
            fields=fields | {"_csrf_token": "test-only-csrf-token"},
        )
        assert status == 403 and len(calls) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
