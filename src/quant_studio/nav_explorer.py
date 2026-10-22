"""Interactive display over the same verified NAV observations as the static view."""

import json
from datetime import datetime
from decimal import Decimal
from html import escape
from importlib.resources import files

from quant_studio.nav import validate_comparison


def explorer(series, *, baseline=None, currency="", source="", static=""):
    if baseline is not None:
        validate_comparison(series, baseline)
    opening = series.initial_nav or series.rows[0][1]
    peak = opening
    drawdowns = []
    for _, value in series.plot_rows:
        peak = max(peak, value)
        drawdowns.append(str((value / peak - 1) * 100))
    dates = [day for day, _ in series.plot_rows]
    payload = {
        "dates": dates,
        "values": [str(value) for _, value in series.plot_rows],
        "opening": str(opening),
        "baseline": [str(value) for _, value in baseline.plot_rows]
        if baseline
        else None,
        "drawdowns": drawdowns,
    }
    length = (
        datetime.fromisoformat(series.rows[-1][0].replace("Z", "+00:00")).date()
        - datetime.fromisoformat(series.rows[0][0].replace("Z", "+00:00")).date()
    )
    allow_log = (
        length.days > 365 * 3
        and all(value > Decimal(0) for _, value in series.plot_rows)
        and (baseline is None or all(value > 0 for _, value in baseline.plot_rows))
    )
    log = (
        '<label class="check"><input type="checkbox" id="nav-log">对数坐标</label>'
        if allow_log
        else ""
    )
    evidence = "有同口径基准" if baseline else "无基准"
    basis = (
        "已记录的期初" if series.initial_nav is not None else "首条观测（不含其前收益）"
    )
    data = (
        json.dumps(payload, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace("&", "\\u0026")
    )
    script = files("quant_studio").joinpath("assets/nav-explorer.js").read_text("utf-8")
    return f"""<section class="chart-card nav-explorer" id="nav-explorer" hidden>
<h2>净值与回撤</h2><span class="badge evidence">{evidence}</span>
<p>按{basis}归一化为 1.00；横轴保持原始观测顺序，不补交易日，不重新计算全区间指标。</p>
<div class="actions"><label>纵轴<select id="nav-unit">
<option value="normalized">归一化净值</option>
<option value="amount">原始金额 / 净值 {escape(currency or "")}</option>
</select></label>{log}</div>
<svg id="nav-svg" viewBox="0 0 940 350" role="img"
aria-label="策略净值和基准" tabindex="0"></svg>
<p id="nav-readout" aria-live="polite">
移动到曲线上查看观测；也可聚焦图表后按左右方向键。</p>
<svg id="nav-dd" viewBox="0 0 940 180" role="img"
aria-label="非正回撤，与净值共享横轴"></svg>
<div class="nav-brush"><svg id="nav-brush-svg" viewBox="0 0 940 60"
role="img" aria-label="拖选显示区间"></svg>
<div class="recipe-grid"><label>显示起点
<input type="range" id="nav-start" min="0" value="0"></label><label>显示终点
<input type="range" id="nav-end" min="0"></label></div></div>
<p id="nav-window"></p><p class="note">数据：{escape(source or "本次冻结输入")}；
曲线来自本次已核验的净值文件。灰色虚线为基准，回撤始终小于或等于零。</p>
</section><div id="nav-static">{static}</div>
<script type="application/json" id="nav-observations">{data}</script>
<script>{script}</script>"""
