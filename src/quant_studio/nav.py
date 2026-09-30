from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import UTC, datetime
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
    label: str | None = None
    initial_nav: Decimal | None = None

    @property
    def plot_rows(self) -> list[tuple[str, Decimal]]:
        # An explicit opening balance is not another closing observation/date.
        if self.initial_nav is not None:
            return [("期初", self.initial_nav), *self.rows]
        return self.rows

    @property
    def ending(self) -> Decimal:
        return self.rows[-1][1]

    @property
    def period_return(self) -> Decimal | None:
        if len(self.plot_rows) < 2:
            return None
        start = self.plot_rows[0][1]
        if start == 0:
            return Decimal(0)
        return self.ending / start - 1

    @property
    def max_drawdown(self) -> Decimal | None:
        if len(self.plot_rows) < 2:
            return None
        peak = self.plot_rows[0][1]
        worst = Decimal(0)
        for _, value in self.rows:
            if value > peak:
                peak = value
            if peak > 0:
                worst = max(worst, (peak - value) / peak)
        return worst


def parse_nav_csv(
    path: Path,
    *,
    strategy: str | None = None,
    value_column: str | None = None,
    initial_nav: Decimal | str | None = None,
) -> NavSeries | None:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])
        raw_rows = list(reader)
    if not fields or not raw_rows:
        return None
    date_key = _date_key(fields)
    value_key = value_column or _value_key(fields, path.name)
    if date_key is None or value_key is None:
        return None
    if value_key not in fields:
        raise QuantStudioError(f"净值列不存在: {value_key}")
    label = value_key if value_key not in VALUE_COLUMNS else None
    if "strategy" in fields and value_key == "nav":
        strategies = {row.get("strategy") for row in raw_rows}
        if strategy is None and len(strategies) != 1:
            raise QuantStudioError("净值文件包含多个策略，请显式选择策略")
        chosen = strategy if strategy is not None else next(iter(strategies))
        if chosen not in strategies or not chosen:
            raise QuantStudioError(f"净值策略不存在: {chosen}")
        raw_rows = [row for row in raw_rows if row.get("strategy") == chosen]
        label = chosen
    elif strategy is not None:
        raise QuantStudioError("净值文件没有策略列")
    opening = _opening_value(initial_nav) if initial_nav is not None else None
    if "initial_nav" in fields:
        openings = {_opening_value(row.get("initial_nav")) for row in raw_rows}
        if len(openings) != 1 or (opening is not None and opening not in openings):
            raise QuantStudioError("期初净值不一致")
        opening = openings.pop()
    parsed: dict[datetime, tuple[str, Decimal]] = {}
    for row in raw_rows:
        raw_date = (row.get(date_key) or "").strip()
        raw_value = (row.get(value_key) or "").strip()
        try:
            value = Decimal(raw_value)
            day = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        except (InvalidOperation, ValueError) as exc:
            raise QuantStudioError("净值日期或数值无效") from exc
        if not value.is_finite() or value < 0:
            raise QuantStudioError("净值必须是有限的非负数值")
        day = day.replace(tzinfo=UTC) if day.tzinfo is None else day.astimezone(UTC)
        if day in parsed:
            raise QuantStudioError(f"净值日期重复: {raw_date}")
        parsed[day] = (raw_date, value)
    rows = [parsed[day] for day in sorted(parsed)]
    if not rows or (opening is None and rows[0][1] <= 0):
        raise QuantStudioError("初始净值必须大于零")
    return NavSeries(rows, label, opening)


def _opening_value(raw: object) -> Decimal:
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise QuantStudioError("期初净值无效") from exc
    if not value.is_finite() or value <= 0:
        raise QuantStudioError("期初净值必须是有限正数")
    return value


def write_nav_csv(path: Path, series: NavSeries) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        fields = ["date", "nav"]
        if series.label:
            fields.append("strategy")
        if series.initial_nav is not None:
            fields.append("initial_nav")
        writer.writerow(fields)
        for date, value in series.rows:
            row = [date, value]
            if series.label:
                row.append(series.label)
            if series.initial_nav is not None:
                row.append(series.initial_nav)
            writer.writerow(row)


def chart_fragment(series: NavSeries) -> str:
    if len(series.plot_rows) == 1:
        date, value = series.rows[0]
        return (
            '<section class="chart-card"><h2>净值快照</h2>'
            f"<p>{escape(date)} · {value}</p>"
            "<p>单次观测，尚无区间收益或最大回撤。</p></section>"
        )
    values = [value for _, value in series.plot_rows]
    low = min(values)
    high = max(values)
    pad = (high - low) * Decimal("0.18") or Decimal("1")
    y_min = low - pad
    y_max = high + pad
    span = y_max - y_min
    left, top, right, bottom = 78, 24, 900, 300
    plot_w = Decimal(right - left)
    plot_h = Decimal(bottom - top)
    step = Decimal(max(len(series.plot_rows) - 1, 1))
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
    dates = [date for date, _ in series.plot_rows]
    axis = _date_axis(dates, left, right)
    ending = series.ending
    change = series.period_return * Decimal(100)
    drawdown = series.max_drawdown * Decimal(100)
    return f"""<section class="chart-card"><style>{_CHART_CSS}</style>
<h2>净值曲线{f" · {escape(series.label)}" if series.label else ""}</h2>
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
    *,
    result_files: dict[str, str] | None = None,
    nav_column: str | None = None,
    nav_strategy: str | None = None,
    initial_nav: Decimal | str | None = None,
) -> str | None:
    if result_files is not None:
        return _collect_declared(
            run_dir, result_files, nav_column, nav_strategy, initial_nav
        )
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
        series = parse_nav_csv(
            nav_source,
            value_column=nav_column,
            strategy=nav_strategy,
            initial_nav=initial_nav,
        )
        if series is not None:
            write_nav_csv(run_dir / "nav.csv", series)
    report = _best_report(files, nav_source)
    for path in files:
        if path.name not in TABLE_FILES:
            continue
        target = (run_dir / path.name).resolve()
        if path.resolve() == target or not _allowed(path.resolve(), cwd, run_dir):
            continue
        target.write_bytes(path.read_bytes())
    # Keep the report beside its evidence so every relative link retains its base.
    return report.relative_to(run_dir.resolve()).as_posix() if report else None


def _collect_declared(run_dir, result_files, nav_column, nav_strategy, initial_nav):
    root = (run_dir / "strategy-output").resolve()
    if not _allowed(root, run_dir, run_dir):
        raise QuantStudioError("结果目录逃出本次运行")
    sources = {}
    for target, relative in result_files.items():
        source = (root / relative).resolve()
        if target not in {"nav.csv", "report.html", *TABLE_FILES}:
            raise QuantStudioError(f"未知结果类型: {target}")
        if root not in source.parents or not source.is_file():
            raise QuantStudioError(f"本次运行结果缺失或路径非法: {relative}")
        sources[target] = source
    series = None
    if "nav.csv" in sources:
        series = parse_nav_csv(
            sources["nav.csv"],
            value_column=nav_column,
            strategy=nav_strategy,
            initial_nav=initial_nav,
        )
        if series is None:
            raise QuantStudioError("本次运行没有可识别的净值")
    for target, source in sources.items():
        if target == "nav.csv":
            write_nav_csv(run_dir / target, series)
        elif target != "report.html":
            (run_dir / target).write_bytes(source.read_bytes())
    report = sources.get("report.html")
    return report.relative_to(run_dir.resolve()).as_posix() if report else None


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
        values = [
            name
            for name in fields
            if name not in DATE_COLUMNS
            and name not in {"", "index", "strategy", "initial_nav"}
        ]
        if len(values) > 1:
            raise QuantStudioError("净值文件包含多个组合，请显式选择净值列")
        if values:
            return values[0]
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
    if len(series.plot_rows) < 2:
        return ""
    peak = series.plot_rows[0][1]
    points: list[tuple[str, Decimal]] = []
    for date, value in series.plot_rows:
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
