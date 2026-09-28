from __future__ import annotations

import csv
import io
from decimal import Decimal
from html import escape
from pathlib import Path

from quant_studio.templates import RenderedTemplate, Template


def execute_synthetic(
    template: Template, rendered: RenderedTemplate, run_dir: Path
) -> None:
    initial = Decimal(str(rendered.values["initial_capital"]))
    nav = initial
    rows: list[tuple[str, Decimal]] = []
    with template.directory.joinpath("returns.csv").open(
        encoding="utf-8", newline=""
    ) as stream:
        for row in csv.DictReader(stream):
            nav *= Decimal("1") + Decimal(row["return"])
            rows.append((row["date"], nav))

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["date", "nav"])
    for date, value in rows:
        writer.writerow([date, f"{value:.2f}"])
    nav_text = output.getvalue()
    (run_dir / "nav.csv").write_text(nav_text, encoding="utf-8", newline="")
    (run_dir / template.report_name).write_text(
        _report_html(rows), encoding="utf-8", newline=""
    )


def _report_html(rows: list[tuple[str, Decimal]]) -> str:
    values = [value for _, value in rows]
    low = min(values)
    high = max(values)
    pad = (high - low) * Decimal("0.18") or Decimal("1")
    y_min = low - pad
    y_max = high + pad
    span = y_max - y_min
    left, top, right, bottom = 72, 28, 900, 320
    plot_w = Decimal(right - left)
    plot_h = Decimal(bottom - top)
    step = Decimal(max(len(rows) - 1, 1))
    coords = []
    for index, value in enumerate(values):
        x = Decimal(left) + plot_w * Decimal(index) / step
        y = Decimal(top) + (y_max - value) * plot_h / span
        coords.append((x, y))
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"{coords[0][0]:.1f},{bottom} {line} {coords[-1][0]:.1f},{bottom}"
    grids = []
    for mark in range(5):
        ratio = Decimal(mark) / Decimal(4)
        y = Decimal(top) + plot_h * ratio
        level = y_max - span * ratio
        grids.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}"/>'
            f'<text x="64" y="{y + 4:.1f}">{level:.0f}</text>'
        )
    first_date, last_date = rows[0][0], rows[-1][0]
    last_nav = values[-1]
    change = (last_nav / values[0] - 1) * Decimal(100)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>合成净值样例</title>
<style>
body {{
  margin: 0;
  color: #101828;
  background: #fff;
  font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
}}
.chart {{ padding: 8px 8px 0; }}
.head {{ display: flex; justify-content: space-between; gap: 16px; }}
h1 {{ margin: 0; font-size: 22px; }}
.tag {{
  margin: 8px 0 0;
  color: #8a5a00;
  font-size: 13px;
}}
.stats {{ display: flex; gap: 12px; margin: 16px 0; }}
.stat {{
  min-width: 140px;
  padding: 12px 14px;
  border-radius: 12px;
  background: #f5f8fc;
}}
.stat b {{ display: block; font-size: 20px; }}
.stat span {{ color: #667085; font-size: 12px; }}
svg {{ width: 100%; height: auto; }}
line {{ stroke: #e6ebf2; }}
text {{ fill: #98a2b3; font-size: 12px; }}
.axis {{ fill: #98a2b3; font-size: 12px; }}
</style></head>
<body><section class="chart">
<div class="head">
<div>
<h1>净值曲线</h1>
<p class="tag">合成样例，不是市场收益</p>
</div>
</div>
<div class="stats">
<div class="stat"><span>期末净值</span><b>{last_nav:.2f}</b></div>
<div class="stat"><span>区间涨跌</span><b>{change:+.2f}%</b></div>
<div class="stat"><span>最高 / 最低</span><b>{high:.0f} / {low:.0f}</b></div>
</div>
<svg viewBox="0 0 940 380" role="img" aria-label="合成净值折线">
<defs>
<linearGradient id="area" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="#1677ff" stop-opacity="0.28"/>
<stop offset="100%" stop-color="#1677ff" stop-opacity="0.02"/>
</linearGradient>
</defs>
{"".join(grids)}
<polygon points="{area}" fill="url(#area)"/>
<polyline points="{line}" fill="none" stroke="#1677ff" stroke-width="3"
 stroke-linejoin="round" stroke-linecap="round"/>
<text class="axis" x="{left}" y="352">{escape(first_date)}</text>
<text class="axis" x="{right}" y="352" text-anchor="end">{escape(last_date)}</text>
</svg>
</section></body></html>
"""
