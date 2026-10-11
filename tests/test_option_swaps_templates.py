import json
import os
import sys
from pathlib import Path

import pytest

from quant_studio.runner import preflight, run
from quant_studio.settings import use_settings
from quant_studio.templates import load_template, render_template

IDS = [
    "options-american-chain",
    "options-lifecycle",
    "options-american-dividends",
    "options-surface-calibration",
    "options-strategy-margin",
    "swaps-market-curves",
    "swaps-eur-multicurve",
    "swaps-gbp-sonia",
    "swaps-counterparty-exposure",
    "swaps-clearing-reconciliation",
]


@pytest.mark.parametrize("identifier", IDS)
def test_derivative_template_contract(identifier):
    t = load_template(identifier)
    rendered = render_template(t)
    assert rendered.config
    assert t.argv[:2] == ["python", "-m"]
    assert t.preflight_argv[-1] == "--preflight"
    assert t.metadata["result_evidence"]["values"] == ["synthetic", "retrospective"]
    assert t.metadata["runtime_key"] == t.workspace_repo + "-market"


@pytest.mark.skipif(
    not os.environ.get("QUANT_MARKET_INTEGRATION_ROOT"),
    reason="explicit installed integration workspace required",
)
@pytest.mark.parametrize("identifier", IDS)
def test_real_cli_preflight_run_and_report_collection(identifier, tmp_path):
    root = Path(os.environ["QUANT_MARKET_INTEGRATION_ROOT"])
    bundle = (
        os.environ["QUANT_MARKET_INTEGRATION_BUNDLE"]
        if identifier in {"options-lifecycle", "options-american-chain"}
        else None
    )
    settings = {
        "environment": {"QUANT_WORKSPACE_ROOT": str(root)},
        "python_by_repo": {
            "quant-options-market": sys.executable,
            "quant-swaps-market": sys.executable,
        },
    }
    with use_settings(settings):
        checked = preflight(
            identifier, snapshot=bundle, runs_root=tmp_path / "checks", timeout=60
        )
        assert checked.status == "checked", checked.as_json()
        result = run(
            identifier,
            execute=True,
            snapshot=bundle,
            runs_root=tmp_path / "runs",
            timeout=60,
        )
        assert result.status == "succeeded", result.as_json()
    report = result.run_dir / result.report
    assert report.is_file()
    study = json.loads((result.run_dir / "study.json").read_text(encoding="utf-8"))
    assert study["evidence_kind"] == "synthetic"
    for filename in load_template(identifier).result_files:
        if filename != "report.html":
            assert (result.run_dir / filename).is_file(), filename
