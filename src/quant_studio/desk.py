from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.runner import safe_run_file

DATA_ROOTS = (
    ("A股", "a-share-multifactor", "data"),
    ("港股", "quant-hk-equity", "data"),
    ("美股", "quant-us-equity", "data"),
    ("基金", "quant-fund", "data"),
    ("共享行情", "quant-data-kit", "data"),
)

FETCH_COMMANDS = (
    (
        "A股",
        "python -m a_share_multifactor.fetch_data --config configs/default.yaml",
    ),
    (
        "港股",
        "quant-hk fetch --config configs/baseline.json --snapshot data/hk-snapshot",
    ),
)

FETCH_BY_REPO = {
    "a-share-multifactor": FETCH_COMMANDS[0][1],
    "quant-hk-equity": FETCH_COMMANDS[1][1],
}


@dataclass(frozen=True)
class Dataset:
    market: str
    path: Path
    files: int
    newest: str


def workspace_root() -> Path | None:
    raw = os.environ.get("QUANT_WORKSPACE_ROOT")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_dir():
        return None
    return path


def scan_datasets(root: Path | None = None) -> list[Dataset]:
    root = workspace_root() if root is None else root
    if root is None or not root.is_dir():
        return []
    found: list[Dataset] = []
    for market, repo, folder in DATA_ROOTS:
        path = root / repo / folder
        if not path.is_dir():
            continue
        count = 0
        newest_stamp = 0.0
        for directory, dirnames, filenames in os.walk(path):
            dirnames[:] = [
                name
                for name in dirnames
                if name not in {".git", ".venv", "__pycache__"}
            ]
            for name in filenames:
                if not name.endswith((".csv", ".parquet", ".json")):
                    continue
                count += 1
                if count > 200:
                    break
                stamp = (Path(directory) / name).stat().st_mtime
                if stamp > newest_stamp:
                    newest_stamp = stamp
            if count > 200:
                break
        if count:
            found.append(Dataset(market, path, count, _format_time(newest_stamp)))
    return found


def list_runs(runs_root: Path) -> list[dict[str, str]]:
    if not runs_root.is_dir():
        return []
    records = []
    for directory in runs_root.iterdir():
        result_path = directory / "result.json"
        if not directory.is_dir() or not result_path.is_file():
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            request = json.loads(
                (directory / "request.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            continue
        report_name = str(result.get("report") or "")
        try:
            has_report = (
                bool(report_name)
                and safe_run_file(directory, report_name, {".html"}).is_file()
            )
        except QuantStudioError:
            has_report = False
        stamp = result_path.stat().st_mtime
        records.append(
            {
                "run_id": directory.name,
                "template_id": str(request.get("template_id", "")),
                "status": str(result.get("status", "")),
                "has_nav": "1" if (directory / "nav.csv").is_file() else "0",
                "has_report": "1" if has_report else "0",
                "when": _format_time(stamp),
                "mtime": str(int(stamp)),
            }
        )
    records.sort(key=lambda item: item["mtime"], reverse=True)
    return records[:40]


def _format_time(stamp: float) -> str:
    return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")


def html_table(path: Path, title: str, limit: int = 12) -> str:
    import csv

    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        rows = []
        for index, row in enumerate(reader):
            if index > limit:
                break
            rows.append(row)
    if not rows:
        return ""
    head = "".join(f"<th>{escape(cell)}</th>" for cell in rows[0])
    body = []
    for row in rows[1:]:
        cells = "".join(f"<td>{escape(cell)}</td>" for cell in row)
        body.append(f"<tr>{cells}</tr>")
    return (
        f'<section class="panel"><h2>{escape(title)}</h2>'
        '<div class="table-scroll" tabindex="0">'
        f"<table><tr>{head}</tr>{''.join(body)}</table></div></section>"
    )
