from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path

from quant_studio import QuantStudioError

DATE_COLUMNS = ("date", "trade_date", "datetime", "dt")
VALUE_COLUMNS = ("nav", "equity", "capital", "net_value", "value")
TABLE_FILES = {
    "positions.csv",
    "holdings.csv",
    "orders.csv",
    "fills.csv",
    "trades.csv",
}
NAV_FILENAMES = ("nav.csv", "capital_curves.csv", "cumulative_returns.csv")

_CHART_CSS = """
.chart-card { color: #101828; font-family: "Segoe UI", "PingFang SC", sans-serif; }
.chart-card h2 { margin: 8px 12px 0; font-size: 20px; }
.stats { display: flex; gap: 12px; margin: 12px; }
.stat {
  min-width: 120px;
  padding: 12px 14px;
  border-radius: 12px;
  background: #f5f8fc;
}
.stat b { display: block; font-size: 20px; }
.stat span { color: #667085; font-size: 12px; }
.chart-card svg { width: 100%; height: auto; }
.chart-card line { stroke: #e6ebf2; }
.chart-card text { fill: #98a2b3; font-size: 12px; }
"""


@dataclass(frozen=True)
class NavSeries:
    rows: list[tuple[str, Decimal]]

    @property
    def ending(self) -> Decimal:
        return self.rows[-1][1]

    @property
    def period_return(self) -> Decimal:
        start = self.rows[0][1]
        if start == 0:
            return Decimal(0)
        return self.ending / start - 1

    @property
    def max_drawdown(self) -> Decimal:
        peak = self.rows[0][1]
        worst = Decimal(0)
        for _, value in self.rows:
            if value > peak:
                peak = value
            if peak > 0:
                worst = max(worst, (peak - value) / peak)
        return worst


def parse_nav_csv(path: Path) -> NavSeries | None:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])
        raw_rows = list(reader)
    if not fields or not raw_rows:
        return None
    date_key = _date_key(fields)
    value_key = _value_key(fields, path.name)
    if date_key is None or value_key is None:
        return None
    if "strategy" in fields and value_key == "nav":
        chosen = raw_rows[-1].get("strategy")
        raw_rows = [row for row in raw_rows if row.get("strategy") == chosen]
    parsed: list[tuple[str, Decimal]] = []
    for row in raw_rows:
        raw_date = (row.get(date_key) or "").strip()
        raw_value = (row.get(value_key) or "").strip()
        if not raw_date or not raw_value:
            continue
        try:
            value = Decimal(raw_value)
        except InvalidOperation:
            continue
        if not value.is_finite():
            continue
        parsed.append((raw_date[:10], value))
    if len(parsed) < 2:
        return None
    return NavSeries(parsed)


def write_nav_csv(path: Path, series: NavSeries) -> None:
    lines = ["date,nav"]
    lines.extend(f"{date},{value}" for date, value in series.rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")


def chart_fragment(series: NavSeries) -> str:
    values = [value for _, value in series.rows]
    low = min(values)
    high = max(values)
    pad = (high - low) * Decimal("0.18") or Decimal("1")
    y_min = low - pad
    y_max = high + pad
    span = y_max - y_min
    left, top, right, bottom = 78, 24, 900, 300
    plot_w = Decimal(right - left)
    plot_h = Decimal(bottom - top)
    step = Decimal(max(len(series.rows) - 1, 1))
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
            f'<text x="70" y="{y + 4:.1f}">{_level(level, span)}</text>'
        )
    dates = [date for date, _ in series.rows]
    axis = _date_axis(dates, left, right)
    ending = series.ending
    change = series.period_return * Decimal(100)
    drawdown = series.max_drawdown * Decimal(100)
    return f"""<section class="chart-card"><style>{_CHART_CSS}</style>
<h2>净值曲线</h2>
<div class="stats">
<div class="stat"><span>期末净值</span><b>{ending:.2f}</b></div>
<div class="stat"><span>区间涨跌</span><b>{change:+.2f}%</b></div>
<div class="stat"><span>最大回撤</span><b>{drawdown:.2f}%</b></div>
</div>
<svg viewBox="0 0 940 360" role="img" aria-label="净值曲线">
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
{axis}
</svg>
</section>"""


def collect_outputs(
    cwd: Path,
    run_dir: Path,
    output_dirs: list[str],
) -> None:
    roots = _output_roots(cwd, run_dir, output_dirs)
    files: list[Path] = []
    for root in roots:
        files.extend(_walk(root))
    files = sorted(
        {path.resolve() for path in files if _allowed(path.resolve(), cwd, run_dir)}
    )
    nav_source = _best_nav(files)
    # A result is one bundle. Never combine a curve with another run's tables/report.
    parents = {path.parent for path in files}
    if len(parents) > 1:
        raise QuantStudioError("输出目录包含多个结果集合，无法确定本次结果")
    if nav_source is not None:
        series = parse_nav_csv(nav_source)
        if series is not None:
            write_nav_csv(run_dir / "nav.csv", series)
    report = _best_report(files, nav_source)
    destination = (run_dir / "report.html").resolve()
    if report is None or report.resolve() == destination:
        pass
    elif _allowed(report.resolve(), cwd, run_dir):
        destination.write_bytes(report.read_bytes())
    for path in files:
        if path.name not in TABLE_FILES:
            continue
        target = (run_dir / path.name).resolve()
        if path.resolve() == target or not _allowed(path.resolve(), cwd, run_dir):
            continue
        target.write_bytes(path.read_bytes())


def _output_roots(cwd: Path, run_dir: Path, output_dirs: list[str]) -> list[Path]:
    roots: list[Path] = []
    for raw in output_dirs:
        if raw == "{output}":
            path = run_dir / "strategy-output"
        else:
            path = (cwd / raw).resolve()
        if path.exists() and _allowed(path.resolve(), cwd, run_dir):
            roots.append(path.resolve())
    return roots


def _allowed(path: Path, cwd: Path, run_dir: Path) -> bool:
    for root in (run_dir.resolve(),):
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _walk(root: Path) -> list[Path]:
    found: list[Path] = []
    count = 0
    for directory, dirnames, filenames in os.walk(root):
        current = Path(directory)
        try:
            depth = len(current.resolve().relative_to(root.resolve()).parts)
        except ValueError:
            dirnames.clear()
            continue
        if depth > 4:
            dirnames.clear()
            continue
        dirnames[:] = [
            name for name in dirnames if name not in {".git", ".venv", "__pycache__"}
        ]
        for name in filenames:
            count += 1
            if count > 300:
                return found
            if name == "report.html" or name in NAV_FILENAMES or name in TABLE_FILES:
                found.append(current / name)
    return found


def _best_nav(files: list[Path]) -> Path | None:
    ranked = [path for path in files if path.name in NAV_FILENAMES]
    if not ranked:
        return None
    ranked.sort(key=lambda path: (NAV_FILENAMES.index(path.name), len(path.parts)))
    return ranked[0]


def _best_report(files: list[Path], nav_source: Path | None) -> Path | None:
    reports = [path for path in files if path.name == "report.html"]
    if not reports:
        return None
    if nav_source is not None:
        beside = nav_source.parent / "report.html"
        for report in reports:
            if report.resolve() == beside.resolve():
                return report
    reports.sort(key=lambda path: len(path.parts))
    return reports[0]


def _date_key(fields: list[str]) -> str | None:
    for name in DATE_COLUMNS:
        if name in fields:
            return name
    first = fields[0]
    if first in {"", "index", "Unnamed: 0"}:
        return first
    return None


def _value_key(fields: list[str], filename: str) -> str | None:
    for name in VALUE_COLUMNS:
        if name in fields:
            return name
    if filename == "cumulative_returns.csv":
        for name in reversed(fields):
            if name not in DATE_COLUMNS and name not in {"", "index", "strategy"}:
                return name
    return None


def _level(level: Decimal, span: Decimal) -> str:
    if span < 50:
        return f"{level:.2f}"
    return f"{level:.0f}"


def _date_axis(dates: list[str], left: int, right: int) -> str:
    picks = [0, len(dates) // 2, len(dates) - 1]
    seen: set[int] = set()
    labels = []
    for index in picks:
        if index in seen:
            continue
        seen.add(index)
        if len(dates) == 1 or index == 0:
            x = left
            anchor = "start"
        elif index == len(dates) - 1:
            x = right
            anchor = "end"
        else:
            x = (left + right) // 2
            anchor = "middle"
        labels.append(
            f'<text class="axis" x="{x}" y="336" text-anchor="{anchor}">'
            f"{escape(dates[index])}</text>"
        )
    return "".join(labels)


def drawdown_fragment(series: NavSeries) -> str:
    peak = series.rows[0][1]
    points: list[tuple[str, Decimal]] = []
    for date, value in series.rows:
        if value > peak:
            peak = value
        drop = (value / peak - 1) if peak else Decimal(0)
        points.append((date, drop * Decimal(100)))
    low = min(value for _, value in points)
    high = Decimal(0)
    span = high - low or Decimal(1)
    left, top, right, bottom = 78, 16, 900, 140
    plot_w = Decimal(right - left)
    plot_h = Decimal(bottom - top)
    step = Decimal(max(len(points) - 1, 1))
    coords = []
    for index, (_, value) in enumerate(points):
        x = Decimal(left) + plot_w * Decimal(index) / step
        y = Decimal(top) + (high - value) * plot_h / span
        coords.append((x, y))
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    worst = min(value for _, value in points)
    return f"""<section class="chart-card">
<h2>回撤</h2>
<p class="muted">最大回撤 {series.max_drawdown * Decimal(100):.2f}%，
曲线最低 {worst:.2f}%</p>
<svg viewBox="0 0 940 180" role="img" aria-label="回撤曲线">
<polyline points="{line}" fill="none" stroke="#d92d20" stroke-width="2.5"/>
</svg>
</section>"""
