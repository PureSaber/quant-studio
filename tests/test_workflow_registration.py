import hashlib
import json

import pytest

from quant_studio import QuantStudioError, project_registration
from quant_studio.etf_daily import argv
from quant_studio.etf_status_context import explain_issue, history_context
from quant_studio.experiment_context import differences
from quant_studio.jobs import JobManager
from quant_studio.project_registration_web import post
from quant_studio.projects import ProjectStore


def test_old_frozen_interpreter_remains_independent(monkeypatch, tmp_path):
    from quant_studio import etf_daily

    config = tmp_path / "daily.json"
    config.write_text("{}")
    monkeypatch.setenv("QUANT_STUDIO_ETF_DAILY", str(config))
    monkeypatch.setattr(
        etf_daily,
        "configured_python",
        lambda key: "old-python" if key == "quant-pipeline-frozen" else "new-python",
    )
    assert argv("status")[0] == "old-python"
    monkeypatch.setattr(project_registration, "configured_python", lambda key: None)
    with pytest.raises(QuantStudioError, match="专用"):
        project_registration.configuration(tmp_path)


def test_earlier_verified_success_is_separate_and_corruption_reported(tmp_path):
    recipe = tmp_path / "recipe.yaml"
    recipe.write_text("frozen")
    root = tmp_path / "receipts"
    config = tmp_path / "daily.json"
    config.write_text(json.dumps({"recipe": str(recipe), "output_root": str(root)}))
    receipt = {
        "started_at": "2026-10-14T13:00:00Z",
        "state": "completed",
        "profile_sha256": "old",
        "stages": [{"stage": "preflight", "state": "completed"}],
    }
    receipt["sha256"] = hashlib.sha256(
        json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    path = root / "old" / "operation.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(receipt))
    result = history_context(config)
    assert result["earlier_success"]["profile_sha256"] == "old"
    assert result["earlier_preflight"]["state"] == "completed"
    assert result["errors"] == []
    receipt["state"] = "tampered"
    path.write_text(json.dumps(receipt))
    result = history_context(config)
    assert result["earlier_success"] is None and result["errors"]


@pytest.mark.parametrize(
    "code,destination",
    [
        ("TIMELY_ORDER_MISSING: test", "/accounts"),
        ("CALENDAR_NOT_COVERED", "/data-center"),
        ("Native code identity verification failed", "/delivery"),
        ("STALE_PREVIEW", "/projects"),
        ("unclassified native issue <script>", "#daily-evidence"),
    ],
)
def test_specific_causes_impact_and_destination(code, destination):
    result = explain_issue(code)
    assert result["href"] == destination and result["raw"] == code
    assert result["cause"] and result["impact"] and result["action"]


def test_confirmation_is_explicit_deduplicated_and_failed_jobs_retry(
    tmp_path, monkeypatch
):
    project = ProjectStore(tmp_path).save("test", "question")
    jobs = JobManager(tmp_path, start_worker=False)
    payload = {
        "collection_type": "simulation-registration",
        "project_id": project["id"],
        "preview_id": "a" * 32,
        "config_sha256": "b" * 64,
    }
    monkeypatch.setattr(
        project_registration,
        "inspect",
        lambda *_: {"request": {"revision": project["revision"]}},
    )
    monkeypatch.setattr(project_registration, "queue_payload", lambda *_: payload)

    class Handler:
        runs_root = tmp_path
        _request_profile = None

        def _jobs(self):
            return jobs

        def _redirect(self, location):
            self.location = location

    handler = Handler()
    data = {"preview_id": ["a" * 32], "confirmed": ["yes"]}
    try:
        with pytest.raises(QuantStudioError, match="明确确认"):
            post(
                handler,
                project["id"],
                project["revision"],
                "registration-confirm",
                {"preview_id": ["a" * 32]},
            )
        for _ in range(2):
            post(
                handler,
                project["id"],
                project["revision"],
                "registration-confirm",
                dict(data),
            )
        assert len(jobs.list()) == 1
        jobs._update(jobs.list()[0]["job_id"], status="interrupted")
        post(
            handler,
            project["id"],
            project["revision"],
            "registration-confirm",
            dict(data),
        )
        assert len(jobs.list()) == 2
        assert jobs.list()[0]["collection"] == payload
    finally:
        jobs.close()


def test_registration_uses_existing_collect_dispatch(tmp_path, monkeypatch):
    from quant_studio.datasets import collect_dataset

    expected = {"status": "succeeded"}
    monkeypatch.setattr(
        project_registration, "execute_registration", lambda *_: expected
    )
    assert (
        collect_dataset(
            tmp_path,
            {"collection": {"collection_type": "simulation-registration"}},
            None,
        )
        == expected
    )


def test_selection_rejects_typed_candidate_and_stale_project(tmp_path, monkeypatch):
    project = ProjectStore(tmp_path).save("test", "question")
    monkeypatch.setattr(project_registration, "candidates", lambda *_: [])
    with pytest.raises(QuantStudioError, match="已完成"):
        project_registration.prepare(
            tmp_path,
            project["id"],
            project["revision"],
            "invented",
            "2027-01-01",
            "2027-02-01",
        )
    with pytest.raises(QuantStudioError, match="项目已更新"):
        project_registration.prepare(
            tmp_path, project["id"], "old", "anything", "2027-01-01", "2027-02-01"
        )


def test_etf_comparison_preserves_failed_and_incomparable_rows(tmp_path):
    from quant_studio.etf_experiments import compare_body
    from quant_studio.result_integrity import output_hashes

    for identifier, capital, status in [
        ("first", 100, "succeeded"),
        ("changed", 200, "succeeded"),
        ("failed", 100, "failed"),
    ]:
        run = tmp_path / identifier
        (run / "strategy-output").mkdir(parents=True)
        (run / "request.json").write_text(
            json.dumps(
                {
                    "template_id": "etf-weekly-research",
                    "recipe": {"revision": identifier},
                }
            )
        )
        view = {
            "schema_version": "quant.etf-research-view/v1",
            "family_complete": True,
            "comparison_basis": {"costs": {"initial_capital": capital}},
            "results": [
                {
                    "candidate": "base",
                    "status": "completed",
                    "metrics": {"total_return": 0.1},
                }
            ],
        }
        (run / "strategy-output/studio-view.json").write_text(json.dumps(view))
        (run / "result.json").write_text(
            json.dumps(
                {
                    "status": status,
                    "message": "retained native failure" if status == "failed" else "",
                    "output_hashes": output_hashes(run),
                }
            )
        )
    html = compare_body(tmp_path, ["first", "changed", "failed"])
    assert "未合并排名" in html and "retained native failure" in html
    assert "初始" not in html or "initial_capital" in html
    assert "状态：失败" in html and "请求文件保存时间" in html
    assert "输入、参数与版本差异" in html and "200" in html
    assert differences({"inputs": {"a": 1}}, {"inputs": {"b": 2}}) == [
        ["配置.inputs.a", "1", "已移除"],
        ["配置.inputs.b", "未设置", "2"],
    ]


def test_registration_http_uses_existing_csrf_and_unknown_fields_guard(
    tmp_path, monkeypatch
):
    from tests.test_server import _request, _start_http_server

    project = ProjectStore(tmp_path).save("test", "question")
    calls = []
    monkeypatch.setattr(
        project_registration,
        "prepare",
        lambda *args: calls.append(args) or {"id": "a" * 32},
    )
    server, thread = _start_http_server(tmp_path)
    host = f"127.0.0.1:{server.server_port}"
    fields = {
        "revision": project["revision"],
        "selected": "choice",
        "start": "2027-01-01",
        "end": "2027-02-01",
    }
    try:
        path = f"/projects/{project['id']}/registration-preview"
        assert (
            _request(
                server, "POST", path, host=host, origin=f"http://{host}", fields=fields
            )[0]
            == 403
        )
        fields["_csrf_token"] = "test-only-csrf-token"
        status, headers, _ = _request(
            server, "POST", path, host=host, origin=f"http://{host}", fields=fields
        )
        assert status == 303 and "registration?preview=" in headers["Location"]
        assert len(calls) == 1
        fields["executable"] = "arbitrary-command"
        assert (
            _request(
                server, "POST", path, host=host, origin=f"http://{host}", fields=fields
            )[0]
            == 400
        )
        assert len(calls) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize(
    "change", ["data", "code", "execution", "legacy", "parameters"]
)
def test_actual_comparison_identity_not_paths(change):
    from copy import deepcopy

    from quant_studio.etf_experiments import comparison_identity

    first = {
        "family_complete": True,
        "comparison_basis": {"inputs": {"bundle": "same/path"}},
        "recipe": {"execution": {"timing": "next_open"}},
        "results": [
            {
                "status": "completed",
                "candidate": "base",
                "parameters": {"top_n": 2},
                "code_identity": {"pipeline": "commit-a"},
                "input_identity": {"bundle": {"raw": "hash-a"}},
                "comparison": {
                    "execution": {"timing": "next_open"},
                    "currency": "CNY",
                    "frequency": "daily",
                },
            }
        ],
    }
    second = deepcopy(first)
    if change == "data":
        second["results"][0]["input_identity"]["bundle"]["raw"] = "hash-b"
    elif change == "code":
        second["results"][0]["code_identity"]["pipeline"] = "commit-b"
    elif change == "execution":
        second["recipe"]["execution"]["timing"] = "same_close"
    elif change == "legacy":
        del second["results"][0]["input_identity"]
    else:
        second["results"][0]["parameters"]["top_n"] = 3
    if change == "parameters":
        assert comparison_identity(first) == comparison_identity(second)
    else:
        assert comparison_identity(first) != comparison_identity(second)
    if change == "legacy":
        assert comparison_identity(second) is None
