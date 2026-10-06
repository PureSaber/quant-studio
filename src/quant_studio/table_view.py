from __future__ import annotations

import csv
import re
from collections.abc import Collection, Mapping
from html import escape
from itertools import islice
from pathlib import Path


def _numeric_text(raw: str, fraction_digits: int | None = None) -> str:
    """Compact an explicit decimal field without rounding or float conversion."""
    if not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", raw):
        return raw
    whole, _, fraction = raw.partition(".")
    whole = re.sub(r"(?<=[0-9])(?=(?:[0-9]{3})+$)", ",", whole)
    fraction = fraction.rstrip("0")
    if fraction_digits is not None:
        # Preserve at least the first nonzero digit of a small ratio.
        first_nonzero = len(fraction) - len(fraction.lstrip("0")) + 1
        kept = max(fraction_digits, first_nonzero)
        if len(fraction) > kept:
            fraction = fraction[:kept] + "…"
    return whole + ("." + fraction if fraction else "")


def html_table(
    path: Path,
    title: str,
    limit: int = 12,
    *,
    column_labels: dict | None = None,
    numeric_columns: Collection[str] = (),
    fraction_digits: Mapping[str, int] | None = None,
) -> str:
    if limit < 1:
        raise ValueError("表格预览行数必须大于0")
    digits = fraction_digits or {}
    if any(value < 1 for value in digits.values()):
        raise ValueError("小数预览位数必须大于0")
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        columns = next(reader, None)
        if not columns:
            return ""
        rows = list(islice(reader, limit + 1))
    labels = column_labels or {}
    head = "".join(
        '<th scope="col"'
        + (' class="numeric"' if name in numeric_columns else "")
        + f' title="{escape(name)}">{escape(labels.get(name, name))}</th>'
        for name in columns
    )
    body = []
    for row in rows[:limit]:
        cells = []
        for index, cell in enumerate(row):
            numeric = index < len(columns) and columns[index] in numeric_columns
            cells.append(
                f'<td class="numeric" title="{escape(cell)}">'
                f"{escape(_numeric_text(cell, digits.get(columns[index])))}</td>"
                if numeric
                else f"<td>{escape(cell)}</td>"
            )
        body.append(f"<tr>{''.join(cells)}</tr>")
    note = (
        f"显示前{limit}行，更多记录请下载完整CSV。"
        if len(rows) > limit
        else f"共{len(rows)}行。"
    )
    if any(name in numeric_columns for name in columns):
        note += "数值添加千位分隔并省略小数尾零；悬停可看原值，CSV保留原始精度。"
    if any(name in digits and name in numeric_columns for name in columns):
        note += "权重长小数以…缩写，完整值可悬停查看或下载CSV。"
    return (
        f'<section class="panel"><h2>{escape(title)}</h2>'
        f'<p class="note">{note}</p>'
        f'<div class="table-scroll" tabindex="0" role="region"'
        f' aria-label="{escape(title)}">'
        f"<table><thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></div></section>"
    )
