from decimal import Decimal

import pytest

from quant_studio import QuantStudioError
from quant_studio.nav import (
    chart_fragment,
    drawdown_fragment,
    parse_nav_csv,
    write_nav_csv,
)


@pytest.mark.parametrize(
    "first,ending,expected_return,expected_drawdown",
    [
        ("50", "101", "0.01", "0.5"),
        ("110", "105", "0.05", str(Decimal(5) / 110)),
        ("99", "99", "-0.01", "0.01"),
        ("0", "0", "-1", "1"),
    ],
)
def test_opening_balance_includes_first_session_and_survives_csv(
    tmp_path, first, ending, expected_return, expected_drawdown
):
    source = tmp_path / "source.csv"
    source.write_text(f"date,nav\n2026-09-28,{first}\n2026-09-29,{ending}\n")
    series = parse_nav_csv(source, initial_nav="100")
    target = tmp_path / "nav.csv"
    write_nav_csv(target, series)
    restored = parse_nav_csv(target)
    assert restored == series
    assert series.period_return == Decimal(expected_return)
    assert series.max_drawdown == Decimal(expected_drawdown)
    assert series.plot_rows[0] == ("期初", Decimal(100))
    assert len(series.rows) == 2  # Never invent a dated NAV observation.
    assert "期初" in chart_fragment(series)
    assert f"{series.max_drawdown * 100:.2f}%" in drawdown_fragment(series)


def test_one_close_only_has_performance_when_opening_balance_is_known(tmp_path):
    source = tmp_path / "nav.csv"
    source.write_text("date,nav\n2026-09-28,99\n")
    snapshot = parse_nav_csv(source)
    assert snapshot.period_return is None and snapshot.max_drawdown is None
    assert "单次观测" in chart_fragment(snapshot)
    measured = parse_nav_csv(source, initial_nav="100")
    assert measured.period_return == Decimal("-0.01")
    assert measured.max_drawdown == Decimal("0.01")
    assert "-1.00%" in chart_fragment(measured)


@pytest.mark.parametrize("opening", ["NaN", "inf", "0", "-1", "", "broken"])
def test_invalid_opening_is_rejected(tmp_path, opening):
    source = tmp_path / "nav.csv"
    source.write_text(f"date,nav,initial_nav\n2026-09-28,99,{opening}\n")
    with pytest.raises(QuantStudioError, match="期初"):
        parse_nav_csv(source)


def test_opening_cannot_change_between_rows_or_disagree_with_config(tmp_path):
    source = tmp_path / "nav.csv"
    source.write_text("date,nav,initial_nav\n2026-09-28,99,100\n2026-09-29,98,200\n")
    with pytest.raises(QuantStudioError, match="不一致"):
        parse_nav_csv(source)
    source.write_text("date,nav,initial_nav\n2026-09-28,99,100\n")
    with pytest.raises(QuantStudioError, match="不一致"):
        parse_nav_csv(source, initial_nav="200")
