import csv

from quant_studio.runner import run


def test_synthetic_nav_is_byte_reproducible(tmp_path):
    first = run(
        "synthetic-demo",
        {"initial_capital": 25000},
        execute=True,
        runs_root=tmp_path / "first",
    )
    second = run(
        "synthetic-demo",
        {"initial_capital": 25000},
        execute=True,
        runs_root=tmp_path / "second",
    )

    assert first.status == "succeeded"
    assert first.report == "report.html"
    assert (first.run_dir / "nav.csv").read_bytes() == (
        second.run_dir / "nav.csv"
    ).read_bytes()


def test_synthetic_nav_compounds_fixed_returns_and_report_uses_it(tmp_path):
    result = run(
        "synthetic-demo",
        {"initial_capital": 10000},
        execute=True,
        runs_root=tmp_path,
    )

    with (result.run_dir / "nav.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    report = (result.run_dir / "report.html").read_text("utf-8")

    assert rows[0] == {"date": "2024-01-02", "nav": "10100.00"}
    assert len(rows) >= 5
    assert "合成样例，不是市场收益" in report
    assert "<svg" in report
    assert rows[-1]["nav"] in report
