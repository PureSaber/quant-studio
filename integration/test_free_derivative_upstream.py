"""Native CLI collection and immutable Studio study with offline synthetic files."""

import csv
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from quant_studio.datasets import DatasetStore
from quant_studio.runner import preflight, run
from quant_studio.settings import use_settings


@pytest.mark.parametrize("kind", ["global", "options"])
def test_free_source_native_cli_and_study(kind, tmp_path):
    option = kind == "options"
    template = "options-free-sources" if option else "global-futures-free-sources"
    repo = Path(os.environ["QUANT_UPSTREAM_REPO"]).resolve()
    files = tmp_path / "official"
    files.mkdir()
    if option:
        row = {
            "TradDt": "2025-01-08",
            "Src": "NSE",
            "Sgmt": "FO",
            "FinInstrmTp": "IDO",
            "FinInstrmId": "100",
            "FinInstrmNm": "NIFTY25JAN23500PE",
            "TckrSymb": "NIFTY",
            "FininstrmActlXpryDt": "2025-01-30",
            "NewBrdLotQty": "25",
            "OpnPric": "225",
            "HghPric": "288",
            "LwPric": "194",
            "ClsPric": "208",
            "SttlmPric": "208",
            "TtlTradgVol": "100",
            "OpnIntrst": "2500",
            "UndrlygPric": "23688",
            "StrkPric": "23500",
            "OptnTp": "PE",
        }
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
        with zipfile.ZipFile(
            files / "BhavCopy_NSE_FO_0_0_0_20250108_F_0000.csv.zip", "w"
        ) as z:
            z.writestr("test.csv", stream.getvalue())
        provider, selection = "nse-fo", "NIFTY25JAN23500PE"
    else:
        (files / "VX_2025-02-19.csv").write_text(
            "Trade Date,Futures,Open,High,Low,Close,Settle,Total Volume,Open Interest\n"
            "2025-01-08,G (Feb 2025),18,20,17,19,19.1,100,200\n",
            encoding="utf-8",
        )
        provider, selection = "cboe-vix", "2025-02-19"
    source = tmp_path / "bundle"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "quant_data_kit.derivatives.cli",
            "free-fetch",
            "--provider",
            provider,
            "--kind",
            "option" if option else "future",
            "--selection",
            selection,
            "--start",
            "2025-01-08",
            "--end",
            "2025-01-08",
            "--rights-note",
            "synthetic official-shaped fixture",
            "--files-directory",
            str(files),
            "--output",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    profile = {
        "environment": {"QUANT_WORKSPACE_ROOT": str(repo.parent)},
        "python_by_repo": {
            "quant-options" if option else "quant-futures-global": sys.executable
        },
    }
    with use_settings(profile):
        store = DatasetStore(tmp_path / "runs")
        item = store.register("test", template, source)
        cfg = item["suggested_config"]
        assert cfg["mode"] == "analysis"
        checked = preflight(template, cfg, snapshot=source, runs_root=tmp_path / "runs")
        assert checked.status == "checked", checked.as_json()
        result = run(
            template, cfg, snapshot=source, execute=True, runs_root=tmp_path / "runs"
        )
        assert result.status == "succeeded", result.as_json()
        study = json.loads(
            (result.run_dir / "strategy-output/study.json").read_text(encoding="utf-8")
        )
        assert study["config"]["mode"] == "analysis"
        rejected = preflight(
            template,
            cfg | {"mode": "replay"},
            snapshot=source,
            runs_root=tmp_path / "runs",
        )
        assert rejected.status == "check_failed"
