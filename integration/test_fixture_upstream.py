"""Real fixture CLIs and exact ledger projection in independent upstream runtimes."""

import csv
import json
import os
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from quant_studio.nav import parse_nav_csv
from quant_studio.runner import preflight, run
from quant_studio.runtime import configured_python
from quant_studio.server import render_run
from quant_studio.standard_view import fingerprint
from quant_studio.templates import load_template

# Runs in the upstream environment. Compare every projected fixed-point cell
# with the native integer, including nullable prices and shared margin scales.
VERIFY_NATIVE = """
import csv, json, sys
from decimal import Decimal
from pathlib import Path
import pandas as pd
from quant_lab import load_and_validate_standard_run
run, view = map(Path, sys.argv[1:])
manifest = load_and_validate_standard_run(run)
native = run / 'standard/v2'
mapping = {'account':'portfolio_snapshots', 'positions':'positions',
           'orders':'orders', 'fills':'fills', 'margin':'margin',
           'costs':'costs', 'cash_ledger':'cash_ledger'}
for target, source in mapping.items():
    frame = pd.read_parquet(native / (source + '.parquet'), dtype_backend='pyarrow')
    with (view / (target + '.csv')).open(encoding='utf-8', newline='') as stream:
        projected = list(csv.DictReader(stream))
    assert len(projected) == len(frame)
    for record, row in zip(frame.to_dict(orient='records'), projected):
        for key, value in record.items():
            if not key.endswith('_units'):
                continue
            name = key.removesuffix('_units')
            if pd.isna(value):
                assert row[name] == ''
            else:
                scale = name + '_scale'
                if name in ['initial_margin', 'maintenance_margin']:
                    scale = 'margin_scale'
                assert Decimal(row[name]) * 10 ** int(record[scale]) == int(value)
snapshots = pd.read_parquet(native / 'portfolio_snapshots.parquet')
rows = [[row.event_time.isoformat(),
         str(Decimal(int(row.nav_units)).scaleb(-int(row.nav_scale)))]
        for row in snapshots.itertuples()]
print(json.dumps({'artifacts':len(manifest.artifacts),
                  'currency':manifest.base_currency, 'nav':rows}))
"""


@pytest.mark.parametrize(
    "name,source,liquidity",
    [
        ("futures-spread-fixture", None, None),
        ("crypto-basis-fixture", "binance", "maker"),
        ("crypto-basis-fixture", "binance", "taker"),
        ("crypto-basis-fixture", "okx", "maker"),
        ("crypto-basis-fixture", "okx", "taker"),
    ],
)
def test_native_fixture_cli(name, source, liquidity, tmp_path, monkeypatch):
    repo = Path(os.environ["QUANT_UPSTREAM_REPO"]).resolve()
    template = load_template(name)
    assert repo.name == template.workspace_repo
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(repo.parent))
    python = configured_python(template.workspace_repo) or sys.executable
    fixtures = repo / (
        "src/quant_crypto_basis/fixtures"
        if source
        else "data/local_sample/certified_v1"
    )
    before = fingerprint(fixtures)
    settings = {"initial_cash": "250000.12345678"}
    if source:
        settings.update(source=source, liquidity=liquidity, seed=19)
    checked = preflight(template, settings, runs_root=tmp_path / "checks")
    assert checked.status == "checked", checked.as_json()
    assert checked.evidence_kind == "synthetic"
    assert not (checked.run_dir / "strategy-output").exists()
    result = run(template, settings, execute=True, runs_root=tmp_path / "runs")
    assert result.status == "succeeded", (
        result.message,
        (result.run_dir / "stderr.txt").read_text(encoding="utf-8"),
    )
    assert result.evidence_kind == "synthetic"
    config = "config." + template.config_format
    assert (checked.run_dir / config).read_bytes() == (
        result.run_dir / config
    ).read_bytes()
    native = result.run_dir / "strategy-output" / template.standard_view["run"]
    view = result.run_dir / "strategy-output/studio-view"
    native_before = fingerprint(native / "standard/v2")
    verified = subprocess.run(
        [python, "-I", "-c", VERIFY_NATIVE, str(native), str(view)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert verified.returncode == 0, verified.stderr
    facts = json.loads(verified.stdout)
    assert facts["artifacts"] == 12
    nav = parse_nav_csv(result.run_dir / "nav.csv")
    assert nav.rows == [(date, Decimal(value)) for date, value in facts["nav"]]
    assert len(nav.rows) == (16 if source else 23)
    assert nav.initial_nav == Decimal("250000.12345678")
    assert nav.period_return == nav.ending / nav.initial_nav - 1
    if source:
        assert ".001000+00:00" in nav.rows[0][0]
        assert len({date[:10] for date, _ in nav.rows}) == 1
    with (result.run_dir / "fills.csv").open(encoding="utf-8") as stream:
        fills = list(csv.DictReader(stream))
    assert len(fills) == (2 if source else 8)
    if source:
        assert {row["liquidity_role"] for row in fills} == {liquidity}
    page = render_run(result.run_dir)
    assert "合成数据" in page and facts["currency"] + "账户金额" in page
    assert (
        "事件级原生账本净值" in page and "保证金" in page and "下载完整成交CSV" in page
    )
    assert "未提供基准净值" in page
    receipt = json.loads((view / "view.json").read_text(encoding="utf-8"))
    assert receipt["source_files"] == {
        key: value[0] for key, value in native_before.items()
    }
    assert fingerprint(fixtures) == before

    # A new read-only projection preserves the source and refuses corrupted copies.
    command = json.loads(
        (result.run_dir / "view-command.json").read_text(encoding="utf-8")
    )["argv"]
    assert command[0] == result.argv[0] == checked.argv[0] == python
    assert result.argv[1:2] == ["-m"]

    def project(native_path, output):
        args = command[:]
        args[args.index("--run") + 1] = str(native_path)
        args[args.index("--output") + 1] = str(output)
        return subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", timeout=60
        )

    repeated = project(native, tmp_path / "repeated-view")
    assert repeated.returncode == 0, repeated.stderr
    assert fingerprint(native / "standard/v2") == native_before
    assert (tmp_path / "repeated-view/nav.csv").read_bytes() == (
        view / "nav.csv"
    ).read_bytes()
    copy = tmp_path / "corrupted-native"
    shutil.copytree(native / "standard", copy / "standard")
    (copy / "standard/v2/portfolio_snapshots.parquet").write_bytes(b"corrupt")
    rejected = project(copy, tmp_path / "rejected-view")
    assert rejected.returncode != 0 and not (tmp_path / "rejected-view").exists()
    overlap = project(native, native / "standard/v2/view")
    assert overlap.returncode != 0 and "separate" in overlap.stderr
    assert fingerprint(native / "standard/v2") == native_before
    assert fingerprint(fixtures) == before

    failed = preflight(template, {"initial_cash": "0"}, runs_root=tmp_path / "checks")
    assert (
        failed.status == "check_failed"
        and not (failed.run_dir / "strategy-output").exists()
    )
