"""Software upgrades must retain the frozen account's exact execution environment."""

import sys

import pytest

from quant_studio import QuantStudioError
from quant_studio.etf_daily import configuration
from quant_studio.runtime import configured_python
from quant_studio.settings import use_settings
from quant_studio.setup import validate_shape


def test_daily_runtime_is_independent_of_research_and_missing_pin_fails(
    tmp_path, monkeypatch
):
    profile_path = tmp_path / "daily.json"
    profile_path.write_text("{}")
    monkeypatch.setenv("QUANT_STUDIO_ETF_DAILY", str(profile_path))
    old = tmp_path / "frozen-python.exe"
    old.touch()
    settings = {
        "schema_version": "quant-studio.settings/v1",
        "environment": {},
        "python_by_repo": {
            "quant-pipeline": sys.executable,
            "quant-pipeline-frozen": str(old),
        },
    }
    validate_shape(settings)
    with use_settings(settings):
        assert configuration() == (profile_path, str(old))
        assert configured_python("quant-pipeline") == sys.executable
        old.unlink()
        with pytest.raises(QuantStudioError, match="Python不存在"):
            configuration()
    del settings["python_by_repo"]["quant-pipeline-frozen"]
    with use_settings(settings):
        assert configuration() == (profile_path, sys.executable)
