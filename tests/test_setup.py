import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from quant_studio import QuantStudioError
from quant_studio.runtime import configured_python
from quant_studio.settings import (
    current_settings,
    setting,
    subprocess_environment,
    use_settings,
)
from quant_studio.setup import SettingsStore, empty_profile, validate_profile
from tests.test_server import _raw_request, _request, _start_http_server


def _setup_request(server, fields, origin=None):
    from urllib.parse import urlencode

    authority = f"localhost:{server.server_port}"
    body = urlencode(fields, doseq=True)
    return _raw_request(
        server,
        "POST",
        "/setup",
        body=body,
        headers=[
            ("Host", authority),
            ("Origin", origin or f"http://{authority}"),
            ("Content-Type", "application/x-www-form-urlencoded"),
            ("Content-Length", str(len(body.encode("utf-8")))),
        ],
    )


def _review_http(server, **fields):
    status, _, page = _setup_request(
        server, {"_csrf_token": "test-only-csrf-token", "action": "review", **fields}
    )
    assert status == 200
    return re.search(r'name="review_token" value="([^"]+)"', page.decode()).group(1)


def test_http_review_is_read_only_then_save_restores_after_restart(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    server, thread = _start_http_server(tmp_path / "runs")
    server.RequestHandlerClass.settings_store = store
    try:
        token = _review_http(server, workspace=str(tmp_path))
        assert not store.path.exists()
        status, headers, _ = _setup_request(
            server,
            {
                "_csrf_token": "test-only-csrf-token",
                "action": "save",
                "review_token": token,
            },
        )
        assert status == 303 and headers["Location"] == "/setup?saved=1"
        assert SettingsStore(store.path).snapshot()["environment"][
            "QUANT_WORKSPACE_ROOT"
        ] == str(tmp_path)
        status, _, page = _request(
            server, "GET", "/setup", host=f"localhost:{server.server_port}"
        )
        assert status == 200 and str(tmp_path) in page.decode()
        status, _, page = _request(
            server,
            "GET",
            "/setup?review=expired",
            host=f"localhost:{server.server_port}",
        )
        assert status == 200 and "重新校验" in page.decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "fields,origin,status",
    [
        ({"action": "review"}, None, 403),
        (
            {"action": "review", "_csrf_token": "test-only-csrf-token"},
            "http://attacker.example",
            403,
        ),
        (
            {
                "action": "save",
                "_csrf_token": "test-only-csrf-token",
                "review_token": "unreviewed",
            },
            None,
            400,
        ),
        (
            {
                "action": "review",
                "_csrf_token": "test-only-csrf-token",
                "workspace": "relative",
            },
            None,
            400,
        ),
        (
            {
                "action": "review",
                "_csrf_token": "test-only-csrf-token",
                "target": "arbitrary.json",
            },
            None,
            400,
        ),
        (
            {"action": ["review", "save"], "_csrf_token": "test-only-csrf-token"},
            None,
            400,
        ),
    ],
)
def test_http_rejects_unreviewed_cross_site_and_invalid_configuration(
    tmp_path, fields, origin, status
):
    server, thread = _start_http_server(tmp_path / "runs")
    store = SettingsStore(tmp_path / "settings.json")
    server.RequestHandlerClass.settings_store = store
    try:
        actual, _, page = _setup_request(server, fields, origin)
        assert actual == status
        assert not store.path.exists()
        if fields.get("workspace") == "relative":
            assert 'value="relative"' in page.decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_running_request_retains_old_snapshot_during_a_configuration_save(
    tmp_path, monkeypatch
):
    from quant_studio.runner import run

    store = SettingsStore(tmp_path / "settings.json")
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    initial = empty_profile()
    initial["environment"]["QUANT_WORKSPACE_ROOT"] = str(old)
    store.save(store.review(initial)[0])
    entered, release = threading.Event(), threading.Event()
    seen = []

    def slow_run(*args, **kwargs):
        seen.append(setting("QUANT_WORKSPACE_ROOT"))
        entered.set()
        assert release.wait(10)
        seen.append(setting("QUANT_WORKSPACE_ROOT"))
        return run(*args, **kwargs)

    monkeypatch.setattr("quant_studio.server.run", slow_run)
    server, thread = _start_http_server(tmp_path / "runs")
    server.RequestHandlerClass.settings_store = store
    try:
        with ThreadPoolExecutor() as pool:
            authority = f"localhost:{server.server_port}"
            running = pool.submit(
                _request,
                server,
                "POST",
                "/templates/synthetic-demo",
                host=authority,
                origin=f"http://{authority}",
                fields={"action": "execute", "_csrf_token": "test-only-csrf-token"},
            )
            assert entered.wait(5)
            token = _review_http(server, workspace=str(new))
            assert (
                _setup_request(
                    server,
                    {
                        "action": "save",
                        "review_token": token,
                        "_csrf_token": "test-only-csrf-token",
                    },
                )[0]
                == 303
            )
            release.set()
            assert running.result()[0] == 303
            manager = server.RequestHandlerClass.job_manager
            deadline = time.monotonic() + 5
            while manager.list()[0]["status"] in {"queued", "running"}:
                assert time.monotonic() < deadline
                time.sleep(0.01)
        assert seen == [str(old), str(old)]
        assert store.snapshot()["environment"]["QUANT_WORKSPACE_ROOT"] == str(new)
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_hard_link_target_is_rejected_without_touching_original(tmp_path):
    original = tmp_path / "frozen.json"
    original.write_text("frozen")
    link = tmp_path / "settings.json"
    os.link(original, link)
    with pytest.raises(QuantStudioError, match="独立"):
        SettingsStore(link)
    assert original.read_text() == "frozen"


def test_selected_source_disappearing_before_save_preserves_saved_profile(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.save(store.review(empty_profile())[0])
    before = store.path.read_bytes()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    profile = empty_profile()
    profile["environment"]["QUANT_WORKSPACE_ROOT"] = str(workspace)
    token, _ = store.review(profile)
    workspace.rmdir()
    with pytest.raises(QuantStudioError, match="不存在"):
        store.save(token)
    assert store.path.read_bytes() == before
    assert store.snapshot() == empty_profile()


def test_preview_saves_only_reviewed_selection_and_restart_restores(tmp_path):
    store = SettingsStore(tmp_path / "studio-settings.json")
    profile = empty_profile()
    profile["environment"]["QUANT_WORKSPACE_ROOT"] = str(tmp_path)
    token, checks = store.review(profile)
    assert checks and not store.path.exists()
    assert store.snapshot() is None
    store.save(token)
    assert SettingsStore(store.path).snapshot() == profile
    with pytest.raises(QuantStudioError, match="重新校验"):
        store.save(token)
    assert list(tmp_path.iterdir()) == [store.path]


def test_request_snapshot_overrides_inherited_environment_without_mutating_it(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", "legacy-workspace")
    legacy = tmp_path / "legacy-runtime.json"
    legacy.write_text(
        json.dumps(
            {
                "schema_version": "quant-studio.runtimes/v1",
                "python_by_repo": {"quant-hk-equity": sys.executable},
            }
        )
    )
    monkeypatch.setenv("QUANT_STUDIO_RUNTIMES", str(legacy))
    first, second = empty_profile(), empty_profile()
    first["environment"]["QUANT_WORKSPACE_ROOT"] = str(tmp_path / "first")
    second["environment"]["QUANT_WORKSPACE_ROOT"] = str(tmp_path / "second")
    first["python_by_repo"]["quant-hk-equity"] = sys.executable

    def observe(profile):
        with use_settings(profile):
            assert (
                subprocess_environment()["QUANT_WORKSPACE_ROOT"]
                == profile["environment"]["QUANT_WORKSPACE_ROOT"]
            )
            return setting("QUANT_WORKSPACE_ROOT"), configured_python("quant-hk-equity")

    with ThreadPoolExecutor() as pool:
        a, b = list(pool.map(observe, [first, second]))
    assert a == (str(tmp_path / "first"), sys.executable)
    assert b == (str(tmp_path / "second"), sys.executable)
    assert current_settings() is None
    assert setting("QUANT_WORKSPACE_ROOT") == "legacy-workspace"


@pytest.mark.parametrize(
    "change,match",
    [
        (lambda p: p["environment"].update(QUANT_WORKSPACE_ROOT="relative"), "绝对"),
        (lambda p: p["environment"].update(QUANT_HK_SNAPSHOT="relative"), "绝对"),
        (lambda p: p["python_by_repo"].update(unknown="/python"), "仓库"),
        (lambda p: p.update(unknown=True), "格式"),
    ],
)
def test_invalid_profile_cannot_be_saved(tmp_path, change, match):
    store = SettingsStore(tmp_path / "settings.json")
    profile = empty_profile()
    change(profile)
    with pytest.raises(QuantStudioError, match=match):
        store.review(profile)
    assert not store.path.exists()


def test_selected_data_and_python_are_checked_without_running_strategy(tmp_path):
    profile = empty_profile()
    profile["environment"]["QUANT_WORKSPACE_ROOT"] = str(tmp_path)
    profile["python_by_repo"]["quant-hk-equity"] = str(tmp_path / "not-python")
    (tmp_path / "quant-hk-equity").mkdir()
    (tmp_path / "not-python").write_text("not executable")
    with pytest.raises(QuantStudioError, match="Python"):
        validate_profile(profile)
    profile["python_by_repo"].clear()
    profile["environment"]["QUANT_HK_SNAPSHOT"] = str(tmp_path / "missing")
    with pytest.raises(QuantStudioError, match="不存在"):
        validate_profile(profile)


def test_saved_file_change_cannot_be_overwritten_by_old_review(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    token, _ = store.review(empty_profile())
    store.path.write_text("unrelated file", encoding="utf-8")
    with pytest.raises(QuantStudioError, match="变化"):
        store.save(token)
    assert store.path.read_text() == "unrelated file"


def test_restart_refuses_unknown_existing_file_and_does_not_overwrite(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"account": "frozen"}))
    with pytest.raises(QuantStudioError, match="格式"):
        SettingsStore(path)
    assert json.loads(path.read_text()) == {"account": "frozen"}


def test_two_reviewed_edits_cannot_silently_replace_each_other(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    first, _ = store.review(empty_profile())
    second, _ = store.review(empty_profile())
    store.save(first)
    with pytest.raises(QuantStudioError, match="变化"):
        store.save(second)


def test_explicit_blank_input_does_not_guess_declared_default(tmp_path, monkeypatch):
    from quant_studio.runner import _snapshot_path
    from quant_studio.templates import load_template

    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(tmp_path))
    template = load_template("hk-equity-daily")
    assert (
        _snapshot_path(template, None)
        == (tmp_path / "quant-hk-equity/data/hk-snapshot").resolve()
    )
    with use_settings(empty_profile()):
        assert (
            _snapshot_path(template, None)
            == (tmp_path / "quant-hk-equity/data/hk-snapshot").resolve()
        )
    profile = empty_profile()
    profile["environment"]["QUANT_WORKSPACE_ROOT"] = str(tmp_path)
    with use_settings(profile):
        assert _snapshot_path(template, None) is None
