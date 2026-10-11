import math

import pytest

from quant_studio import QuantStudioError
from quant_studio.notebook_metrics import validate_metrics


def metric(value=1, **kwargs):
    return {
        "value": value,
        "unit": "行",
        "definition": "实际参与统计的记录数",
        **kwargs,
    }


@pytest.mark.parametrize(
    "value", [math.nan, math.inf, -math.inf, True, "1", [], {}, 10**400]
)
def test_metrics_reject_nonfinite_or_nonnumeric(value):
    with pytest.raises(QuantStudioError):
        validate_metrics({"样本数": metric(value)})


def test_missing_metrics_need_reason_and_keep_zero():
    with pytest.raises(QuantStudioError):
        validate_metrics({"样本数": metric(None)})
    metrics = {"样本数": metric(0), "相关系数": metric(None, reason="有效样本不足")}
    assert validate_metrics(metrics) == metrics


def test_metric_definition_and_unit_required():
    with pytest.raises(QuantStudioError):
        validate_metrics({"样本数": {"value": 1}})
