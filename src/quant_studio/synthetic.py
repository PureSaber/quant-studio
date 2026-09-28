from __future__ import annotations

import csv
import io
from decimal import Decimal
from pathlib import Path

from quant_studio.nav import NavSeries, chart_fragment
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
    chart = chart_fragment(NavSeries(rows))
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
.tag {{ margin: 8px 12px; color: #8a5a00; }}
</style></head>
<body>
<p class="tag">合成样例，不是市场收益</p>
{chart}
</body></html>
"""
