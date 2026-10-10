import json

from quant_studio.onboarding import onboarding_body
from quant_studio.projects import ProjectStore
from quant_studio.setup import empty_profile
from tests.test_server import _request, _start_http_server


def test_onboarding_empty_workspace_gives_ordered_actionable_path(tmp_path):
    page = onboarding_body(tmp_path, "secret-token")

    positions = [
        page.index(label)
        for label in (
            "导入原件",
            "通过质量准入",
            "固定研究项目",
            "选择Notebook模板",
            "提交批量实验",
            "查看结果",
        )
    ]
    assert positions == sorted(positions)
    for link in ("/setup", "/intake", "/projects", "/batches", "/experiments"):
        assert f'href="{link}"' in page
    assert "secret-token" not in page
    assert "合成样例只用于熟悉流程" in page
    assert "市场验证" not in page


def test_onboarding_uses_real_saved_state_and_escapes_names(tmp_path):
    (tmp_path / "studio-settings.json").write_text(
        json.dumps(empty_profile()), encoding="utf-8"
    )
    dataset_id = "a" * 32
    datasets = tmp_path / ".datasets"
    datasets.mkdir()
    (datasets / f"{dataset_id}.json").write_text(
        json.dumps(
            {
                "schema": "quant-studio.dataset/v1",
                "id": dataset_id,
                "name": "<script>行情</script>",
                "template_id": "research-intake",
                "kind": "intake",
                "registered_at": "2026-10-10T00:00:00+00:00",
                "intake": {"check": {"allowed": True}},
            }
        ),
        encoding="utf-8",
    )
    project = ProjectStore(tmp_path).save(
        "<img src=x onerror=alert(1)>", "研究问题", datasets=[dataset_id]
    )
    notebook = tmp_path / ".notebooks" / project["id"]
    notebook.mkdir(parents=True)
    (notebook / "research.ipynb").write_text("{}", encoding="utf-8")
    jobs = tmp_path / ".jobs"
    jobs.mkdir()
    (jobs / "job.json").write_text(
        json.dumps({"job_id": "job", "status": "running"}), encoding="utf-8"
    )

    page = onboarding_body(tmp_path, "token")

    assert "配置已保存" in page
    assert "已有1个通过准入的数据版本" in page
    assert "已有1个固定项目" in page
    assert "已有1个Notebook草稿" in page
    assert "当前有1个任务正在排队或执行" in page
    assert "<script>行情</script>" not in page
    assert "<img src=x onerror=alert(1)>" not in page
    assert "&lt;script&gt;行情&lt;/script&gt;" in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page


def test_onboarding_survives_invalid_optional_state(tmp_path):
    (tmp_path / "studio-settings.json").write_text("not-json", encoding="utf-8")
    (tmp_path / ".jobs").mkdir()
    (tmp_path / ".jobs" / "broken.json").write_text("[", encoding="utf-8")

    page = onboarding_body(tmp_path, "token")

    assert "配置需要修正" in page
    assert "开始研究" in page


def test_onboarding_is_available_over_http(tmp_path):
    server, thread = _start_http_server(tmp_path)
    host = f"localhost:{server.server_port}"
    try:
        status, _, payload = _request(server, "GET", "/onboarding", host=host)
        assert status == 200
        assert "开始研究" in payload.decode()
        assert 'aria-current="page">开始研究</a>' in payload.decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
