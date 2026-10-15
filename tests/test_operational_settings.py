import json
import sys

import pytest

from quant_studio import QuantStudioError
from quant_studio.settings import use_settings
from quant_studio.setup import (
    SERVER_CONFIG_KEYS,
    SettingsStore,
    empty_profile,
    profile_from_form,
    validate_profile,
    validate_shape,
)
from tests.test_server import _request, _start_http_server
from tests.test_setup import _review_http, _setup_request


def test_regular_setup_save_preserves_host_operational_bindings(tmp_path):
    profile = empty_profile()
    for key in SERVER_CONFIG_KEYS:
        path = tmp_path / f"{key}.json"
        path.write_text("{}")
        profile["environment"][key] = str(path)
    saved = tmp_path / "settings.json"
    saved.write_text(json.dumps(profile))
    store = SettingsStore(saved)
    server, thread = _start_http_server(tmp_path / "runs")
    server.RequestHandlerClass.settings_store = store
    try:
        token = _review_http(server, workspace=str(tmp_path))
        status, _, _ = _setup_request(
            server,
            {
                "_csrf_token": "test-only-csrf-token",
                "action": "save",
                "review_token": token,
            },
        )
        assert status == 303
        restored = SettingsStore(saved).snapshot()
        for key in SERVER_CONFIG_KEYS:
            assert restored["environment"][key] == profile["environment"][key]
        status, _, page = _request(
            server, "GET", "/setup", host=f"localhost:{server.server_port}"
        )
        assert status == 200
        assert "保存本页其他设置会继续保留" in page.decode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_operational_fields_cannot_be_rebound_by_form(tmp_path):
    with (
        use_settings(empty_profile()),
        pytest.raises(QuantStudioError, match="配置字段未知"),
    ):
        profile_from_form({"QUANT_STUDIO_REGISTRATION": [str(tmp_path / "other.json")]})


def test_registration_and_frozen_python_use_real_pipeline_checkout(tmp_path):
    (tmp_path / "quant-pipeline").mkdir()
    profile = empty_profile()
    profile["environment"]["QUANT_WORKSPACE_ROOT"] = str(tmp_path)
    profile["python_by_repo"] = {
        "quant-pipeline-registration": sys.executable,
        "quant-pipeline-frozen": sys.executable,
    }
    validate_shape(profile)
    validate_profile(profile)
