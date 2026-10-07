import json
import os
from datetime import datetime
from decimal import localcontext
from pathlib import Path

from quant_studio.desk import html_table, list_runs, scan_datasets
from quant_studio.server import _artifact_tables, render_backtest


def test_recent_runs_keep_subsecond_file_order(tmp_path, monkeypatch):
    folders = []
    for name, stamp in (("older", 1791259000.1), ("newer", 1791259000.9)):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "request.json").write_text(
            json.dumps({"template_id": "synthetic-demo"}), encoding="utf-8"
        )
        result = folder / "result.json"
        result.write_text(json.dumps({"status": "preview"}), encoding="utf-8")
        os.utime(result, (stamp, stamp))
        folders.append(folder)
    original_iterdir = Path.iterdir
    monkeypatch.setattr(
        Path,
        "iterdir",
        lambda path: iter(folders) if path == tmp_path else original_iterdir(path),
    )

    records = list_runs(tmp_path)
    assert [row["run_id"] for row in records] == ["newer", "older"]
    assert float(records[0]["mtime"]) > float(records[1]["mtime"])


def test_file_times_have_an_explicit_offset_and_file_meaning(tmp_path):
    data = tmp_path / "quant-hk-equity" / "data"
    data.mkdir(parents=True)
    (data / "prices.csv").write_text("date,close\n2026-01-01,10\n")
    shown = scan_datasets(tmp_path)[0].newest
    assert datetime.fromisoformat(shown).utcoffset() is not None
    page = render_backtest(tmp_path)
    assert "文件更新" in page and "本地时区" in page
    assert "行情截止" in page and "成交时刻" in page


def test_table_formats_declared_amounts_without_rounding_or_rewriting(tmp_path):
    path = tmp_path / "cash_ledger.csv"
    original = (
        b"instrument_id,amount,quantity,event_time\n"
        b"000001,1234567890123456.1234567800,0.000000001,"
        b"2026-10-06T09:30:00+08:00\n"
        b"00123,-1000.01000000,-0.000000001,2026-10-06T01:30:00Z\n"
    )
    path.write_bytes(original)
    with localcontext() as context:
        context.prec = 2
        page = html_table(path, "现金账本", numeric_columns={"amount", "quantity"})
    assert "1,234,567,890,123,456.12345678" in page
    assert "-1,000.01" in page and "-0.000000001" in page
    assert "0.000000001" in page and "<td>000001</td>" in page
    assert "<td>00123</td>" in page
    assert 'title="1234567890123456.1234567800"' in page
    assert "2026-10-06T09:30:00+08:00" in page
    assert path.read_bytes() == original


def test_table_preview_explains_truncation_and_escapes_cells(tmp_path):
    path = tmp_path / "orders.csv"
    path.write_text("order_id,amount\n<script>,1.000\n00001,2.000\n", encoding="utf-8")
    page = html_table(path, "委托<记录>", limit=1, numeric_columns={"amount"})
    assert "&lt;script&gt;" in page and "<script>" not in page
    assert "委托&lt;记录&gt;" in page and "00001" not in page
    assert "显示前1行" in page and "下载完整CSV" in page
    assert '<th scope="col"' in page and "<tbody>" in page
    assert 'tabindex="0"' in page and 'aria-label="委托&lt;记录&gt;"' in page


def test_artifact_table_applies_display_to_money_and_keeps_csv_download(tmp_path):
    path = tmp_path / "cash_ledger.csv"
    path.write_text("order_id,amount\n00001,1000000.01000000\n", encoding="utf-8")
    original = path.read_bytes()
    page = _artifact_tables(tmp_path, native=True)
    assert "1,000,000.01" in page and "<td>00001</td>" in page
    assert "download" in page and "cash_ledger.csv" in page
    assert path.read_bytes() == original


def test_weight_preview_is_explicitly_abbreviated_and_small_weights_stay_nonzero(
    tmp_path,
):
    path = tmp_path / "positions.csv"
    path.write_text(
        "symbol,weight\n00005,0.29308943796327597\n00941,0.000000001234\n",
        encoding="utf-8",
    )
    original = path.read_bytes()
    page = _artifact_tables(tmp_path)
    assert "持仓权重（比例）" in page and "0.2930…" in page
    assert "0.000000001…" in page and "以…缩写" in page
    assert 'title="0.29308943796327597"' in page
    assert 'title="0.000000001234"' in page
    assert "<td>00005</td>" in page and path.read_bytes() == original
