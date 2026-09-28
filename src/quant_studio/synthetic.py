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
    span = high - low or Decimal("1")
    width = Decimal("720")
    height = Decimal("260")
    denominator = max(len(rows) - 1, 1)
    points = []
    for index, (_, value) in enumerate(rows):
        x = width * Decimal(index) / Decimal(denominator)
        y = height - (value - low) * height / span
        points.append(f"{x:.2f},{y:.2f}")
    last_date, last_nav = rows[-1]
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>合成净值样例</title>
<style>
body{{font-family:sans-serif;max-width:800px;margin:2rem auto}}
svg{{border:1px solid #ddd}}
</style>
</head>
<body>
<h1>合成净值样例</h1>
<p><strong>合成样例，不是市场收益</strong></p>
<svg viewBox="0 0 720 260" role="img" aria-label="合成净值折线">
<polyline fill="none" stroke="#2457d6" stroke-width="3" points="{" ".join(points)}"/>
</svg>
<p>最后日期：{escape(last_date)}；净值：{last_nav:.2f}</p>
</body>
</html>
"""
