"""Labels and units for display; native values and validation remain authoritative."""

from decimal import Decimal, InvalidOperation

GROUPS = ("区间与标的", "资金与交易", "因子与方法", "其他研究参数")


def numeric(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return False
    try:
        return Decimal(str(value)).is_finite()
    except InvalidOperation:
        return False


def group_for(key):
    if key in {"symbols", "instruments", "benchmark", "provider"} or any(
        word in key for word in ("date", "start", "end", "universe")
    ):
        return GROUPS[0]
    if any(
        word in key
        for word in (
            "capital",
            "cash",
            "cost",
            "fee",
            "commission",
            "slippage",
            "rate",
            "weight",
            "rebalance",
            "price",
            "turnover",
            "budget",
        )
    ) or key in {"top_n", "platform_fee"}:
        return GROUPS[1]
    if any(
        word in key
        for word in ("factor", "window", "bars", "corr", "quantile", "pairs", "_z")
    ):
        return GROUPS[2]
    return GROUPS[3]


def unit_for(key, *, currency=None, rate=False):
    if rate:
        return "bp"
    if key.endswith(("_bars", "_periods")):
        return "观测周期"
    if key.endswith("_sessions"):
        return "交易时段"
    if key.endswith("_days"):
        return "日"
    if key in {"top_n", "symbols_limit", "max_pairs", "quantiles", "min_obs"}:
        return "个" if key != "min_obs" else "次观测"
    if key.endswith(("multiplier", "scale")) or key.endswith("_z"):
        return "倍"
    if key.endswith(("_rate", "_weight", "_fraction", "_buffer", "_corr", "_error")):
        return "比例"
    if key in {
        "initial_cash",
        "initial_capital",
        "minimum_commission",
        "min_commission",
        "platform_fee",
        "min_price",
        "settlement_minimum",
        "settlement_maximum",
    }:
        return currency or "配置币种金额"
    if key == "min_median_turnover_hkd":
        return "HKD"
    return "原生数值（单位未声明）"


def rate_echo(value):
    return format(Decimal(str(value)) * 100, ".10f").rstrip("0").rstrip(".") + "%"
