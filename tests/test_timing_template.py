import json
import sys

import pytest

from quant_studio import QuantStudioError
from quant_studio.runner import _validate_preflight, preview
from quant_studio.server import render_template_page
from quant_studio.templates import load_template


def test_timing_file_preview_and_cost_control(tmp_path, monkeypatch):
    monkeypatch.delenv("QUANT_STUDIO_RUNTIMES", raising=False)
    monkeypatch.setenv("QUANT_TIMING_CONFIG", str(tmp_path / "original.yaml"))
    result = preview(
        "timing-research", {"cost_multiplier": 2}, runs_root=tmp_path / "runs"
    )
    assert result.argv[result.argv.index("--config") + 1].endswith("original.yaml")
    assert result.argv[:3] == [sys.executable, "-m", "quant_timing"]
    assert json.loads((result.run_dir / "config.json").read_text()) == {
        "cost_multiplier": 2
    }
    page = render_template_page("timing-research")
    assert "择时研究配置文件" in page and "原成本系数" in page
    assert "original.yaml" in page.split('<pre id="compiled">')[1]


@pytest.mark.parametrize(
    "change",
    [
        {"folds": 0},
        {"read_only": False},
        {"investable": True},
        {"evidence_kind": "historical_pit"},
        {"software_preflight": "not_run"},
    ],
)
def test_timing_contract_does_not_accept_weaker_evidence(change):
    report = {
        "schema_version": "quant-timing.preflight/v1",
        "software_preflight": "pass",
        "rows": 360,
        "symbols": 5,
        "folds": 5,
        "read_only": True,
        "investable": False,
        "evidence_kind": "synthetic",
    }
    with pytest.raises(QuantStudioError):
        _validate_preflight(load_template("timing-research"), report | change)
