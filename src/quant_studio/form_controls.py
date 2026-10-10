"""Friendly editors that retain the exact native JSON contract on submission."""

import json
from html import escape
from importlib.resources import files

ADVANCED = {
    "schema",
    "universe_scope",
    "instrument_rules_scope",
    "settlement_calendar_scope",
    "master_source",
    "master_checked_on",
    "source",
    "valid_from",
    "valid_to",
}

LABELS = {
    "sfc_rate": "证监会交易征费率",
    "afrc_rate": "会财局交易征费率",
    "trading_rate": "交易费率",
    "settlement_rate": "结算费率",
    "settlement_minimum": "最低结算费",
    "settlement_maximum": "最高结算费",
    "valid_from": "费用规则开始日期",
    "valid_to": "费用规则结束日期",
    "quantiles": "分组数量",
    "benchmark": "基准",
    "commission": "佣金费率",
    "slippage": "滑点比例",
    "min_commission": "最低佣金",
    "stamp_rate": "印花税率",
    "cost_multiplier": "成本倍数",
    "gross_budget_scale": "总预算倍数",
    "formation_bars": "形成窗口（交易日）",
    "trade_bars": "交易窗口（交易日）",
    "step_bars": "滚动步长（交易日）",
    "embargo_bars": "隔离期（交易日）",
    "min_obs": "最少观测数",
    "min_corr": "最低相关系数",
    "max_pairs": "最多配对数",
    "entry_z": "入场阈值",
    "exit_z": "退出阈值",
    "stop_z": "止损阈值",
    "commission_rate": "佣金费率",
    "minimum_commission": "最低佣金",
    "slippage_rate": "滑点比例",
    "invested_fraction": "投入资金比例",
    "min_price": "最低股价",
    "min_median_turnover_hkd": "最低成交额中位数（港元）",
    "candidates": "候选方法",
    "platform_fee": "平台费",
    "min_list_days": "最短上市时间（交易日）",
    "top_n": "持仓数量",
    "cash_buffer": "现金预留比例",
    "max_weight": "单一标的权重上限",
}


def help_text(key):
    if key.endswith("_rate") or key in {
        "commission",
        "slippage",
        "invested_fraction",
        "max_weight",
        "cash_buffer",
    }:
        return "使用小数比例：0.0003 = 0.03%，0.2 = 20%。"
    if key.endswith("multiplier") or key.endswith("scale"):
        return "1 表示原值，0.5 表示一半，2 表示两倍。"
    if key in {"symbols", "factors", "candidates"}:
        return "每行一个，或用英文逗号分隔。代码保留前导零。"
    if key in {"minimum_commission", "min_commission", "platform_fee", "initial_cash"}:
        return "使用本次研究市场的计价币种；港股为港元。"
    return ""


def special_control(name, key, value):
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        return f'''<textarea
name="{name}" rows="4">{escape(chr(10).join(value))}</textarea>'''
    if (
        key == "instruments"
        and isinstance(value, list)
        and value
        and all(isinstance(v, dict) for v in value)
    ):
        raw = escape(json.dumps(value, ensure_ascii=False), quote=True)
        return f'''<div
class="instrument-editor">
<p
class="note">搜索已有名单中的代码或名称；取消勾选可移出本次研究。新增证券请填写真实整手数量。</p>
<input
type="search"
class="instrument-search"
placeholder="搜索代码或名称"
aria-label="搜索股票">
<div
class="instrument-rows">
</div>
<button
type="button"
class="btn add-instrument">添加证券</button>
<details>
<summary>高级：原始名单</summary>
<textarea
class="instrument-json"
name="{name}" rows="6">{raw}</textarea>
</details>
</div>'''
    return None


EDITOR_SCRIPT = (
    files("quant_studio")
    .joinpath("assets/research-editor.html")
    .read_text(encoding="utf-8")
)
