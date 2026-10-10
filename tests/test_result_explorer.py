from decimal import Decimal

import pytest

from quant_studio import QuantStudioError
from quant_studio.nav import NavSeries
from quant_studio.result_explorer import comparison_explorer


def test_all_four_curves_preserve_initial_capital_and_escape_labels():
    rows = [
        {
            "title": "</script><script>alert(1)</script>" if i == 0 else f"实验{i}",
            "run_id": str(i),
            "series": NavSeries(
                [("2026-01-01", Decimal("90")), ("2026-01-02", Decimal("110"))],
                initial_nav=Decimal("100"),
            ),
        }
        for i in range(4)
    ]
    body = comparison_explorer(rows)
    assert body.count('class="curve-choice"') == 4
    assert "</script><script>alert(1)</script>" not in body
    assert '"-0.1"' in body


def test_incompatible_dates_are_rejected_before_charting():
    rows = [
        {
            "title": str(i),
            "run_id": str(i),
            "series": NavSeries(
                [(f"2026-01-0{i + 1}", Decimal("100"))], initial_nav=Decimal("100")
            ),
        }
        for i in range(2)
    ]
    with pytest.raises(QuantStudioError):
        comparison_explorer(rows)
