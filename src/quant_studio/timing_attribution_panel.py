"""Display native, reconciled return contributions without recomputing research."""

from html import escape


def attribution_panel(run_id, view):
    if view["native_exit_code"] != 0:
        return '<p class="note">本次研究未通过发布门禁，不展示收益归因。</p>'
    evidence = view.get("return_attribution")
    if evidence is None:
        return '<p class="note">此历史运行未导出收益归因，原结果保留。</p>'
    periods = evidence["periods"]
    body = _period_table(periods[0], "描述性全区间贡献")
    for period in periods[1:]:
        number = period["id"].removeprefix("fold-")
        body += (
            '<details class="attribution-fold">'
            f"<summary>测试折{escape(number)} · "
            f"{escape(period['test_start'])}至{escape(period['test_end'])} · "
            f"{period['n_decisions']}个计分决策</summary>"
            + _period_table(period, f"测试折{number}贡献")
            + "</details>"
        )
    return (
        '<section class="panel timing-attribution"><h2>收益来源与成本分解</h2>'
        '<p class="note">贡献单位为百分点，按每日期初净资产连接，'
        "合计等于对应区间净收益。成本为模型值，不是真实成交滑点；"
        "贡献差不单独证明因果效应。</p>"
        '<details class="attribution-method"><summary>计算口径与测试折说明</summary><p>'
        "现金、保证金、期货盈亏与两项模型成本分别记账；现金收益沿用原配置的利率或价格代理。"
        "匹配敞口对照使用原研究权重与成本路径。</p><p>"
        "每折归因期初为1，沿用原计分决策及次日收益；"
        "未重新建仓，各折不拼接，重叠日期不增加独立样本。</p></details>"
        "<style>.attribution-fold{margin-top:14px;"
        "border-top:1px solid #dce4ee;padding-top:12px}"
        ".attribution-fold summary{cursor:pointer;font-weight:600}"
        ".timing-attribution th,.timing-attribution td{white-space:nowrap}"
        ".contribution-bar{height:5px;border-radius:3px;margin-top:4px;background:#2563eb}"
        ".contribution-bar.negative{background:#be3455}"
        ".contribution-total{font-weight:700;border-top:2px solid #cbd5e1}</style>"
        + body
        + f'<p><a href="/runs/{escape(run_id)}/files/'
        'strategy-output/studio-view/attribution_daily.csv" download>'
        "下载完整逐日收益贡献CSV</a></p></section>"
    )


def _pct(value):
    return "不可用" if value is None else f"{value * 100:+.4f}%"


def _period_table(period, title):
    heading = f"<h3>{escape(title)}</h3>"
    if period["n_decisions"] == 0:
        return heading + "<p>没有可计分收益，贡献不可用。</p>"
    maximum = max(
        abs(row[book])
        for row in period["components"]
        for book in ("strategy", "matched")
    )

    def cell(value):
        width = 0 if maximum == 0 else abs(value) / maximum * 100
        negative = " negative" if value < 0 else ""
        return (
            f"<td>{value * 100:+.4f}"
            f'<div aria-hidden="true" class="contribution-bar{negative}" '
            f'style="width:{width:.2f}%"></div></td>'
        )

    kinds = {
        "asset": "资产",
        "cash": "现金",
        "margin": "保证金",
        "futures": "期货盈亏",
        "modeled_cost": "模型成本",
    }
    rows = []
    for row in period["components"]:
        label = row["component"]
        if row["kind"] == "modeled_cost":
            label = {"base_cost": "基础成本", "impact_cost": "冲击成本"}[label]
        rows.append(
            f"<tr><td>{escape(label)}</td><td>{escape(kinds[row['kind']])}</td>"
            + cell(row["strategy"])
            + cell(row["matched"])
            + f"<td>{row['difference'] * 100:+.4f}</td></tr>"
        )
    totals = (
        '<tr class="contribution-total"><td colspan="2">净收益合计</td>'
        f"<td>{period['net_return'] * 100:+.4f}</td>"
        f"<td>{period['matched_net_return'] * 100:+.4f}</td>"
        f"<td>{period['matched_excess_return'] * 100:+.4f}</td></tr>"
    )
    residual = max(abs(period["strategy_residual"]), abs(period["matched_residual"]))
    return (
        heading + f"<p>收益日期：{escape(period['first_return_date'])}至"
        f"{escape(period['last_return_date'])}；市场基准{_pct(period['benchmark_return'])}；"
        f"策略减市场基准{period['excess_return'] * 100:+.4f}个百分点。</p>"
        '<div class="table-scroll" tabindex="0"><table><thead><tr>'
        "<th>来源</th><th>类别</th><th>策略贡献<br>百分点</th>"
        "<th>匹配敞口贡献<br>百分点</th><th>贡献差<br>百分点</th>"
        "</tr></thead><tbody>" + "".join(rows) + totals + "</tbody></table></div>"
        f'<p class="note">净收益对账残差：{residual:.2e}。'
        "条形长度表示贡献绝对值，正负方向以数字符号为准。</p>"
    )
