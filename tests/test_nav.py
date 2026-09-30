from decimal import Decimal

import pytest

from quant_studio import QuantStudioError
from quant_studio.nav import NavSeries, collect_outputs, parse_nav_csv, write_nav_csv


def test_nav_roundtrip_preserves_small_returns_and_drawdowns(tmp_path):
    series = NavSeries(
        [
            ("2026-01-01", Decimal("1.0040")),
            ("2026-01-02", Decimal("1.0049")),
            ("2026-01-03", Decimal("1.0041")),
        ]
    )
    path = tmp_path / "nav.csv"
    write_nav_csv(path, series)
    restored = parse_nav_csv(path)
    assert restored.rows == series.rows
    assert restored.period_return == series.period_return
    assert restored.max_drawdown == series.max_drawdown


def test_collector_rejects_ambiguous_result_sets(tmp_path):
    run_dir = tmp_path / "run"
    for name in ("one", "two"):
        output = run_dir / "strategy-output" / name
        output.mkdir(parents=True)
        (output / "nav.csv").write_text("date,nav\n2026-01-01,1\n2026-01-02,2\n")
    with pytest.raises(QuantStudioError, match="多个"):
        collect_outputs(tmp_path, run_dir, ["{output}"])
    assert not (run_dir / "nav.csv").exists()
