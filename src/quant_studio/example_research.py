"""Minimal offline module contract; synthetic data for engineering acceptance."""

import argparse
import csv
import json
import math
from datetime import date, timedelta
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    try:
        capital = float(config["initial_capital"])
        start = date.fromisoformat(config["start_date"])
        returns = config["daily_returns"]
        if not math.isfinite(capital) or capital <= 0:
            raise ValueError("initial_capital must be positive")
        if not isinstance(returns, list) or not 1 <= len(returns) <= 3650:
            raise ValueError("daily_returns must contain 1–3650 synthetic returns")
        if not all(
            isinstance(r, (int, float)) and math.isfinite(r) and r > -1 for r in returns
        ):
            raise ValueError("synthetic returns must be finite and greater than -1")
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    if args.preflight:
        print(
            json.dumps(
                {
                    "schema_version": "quant-studio.custom-preflight/v1",
                    "software_preflight": "pass",
                    "read_only": True,
                    "investable": False,
                    "rows": len(returns),
                }
            )
        )
        return
    if args.output is None:
        parser.error("--output is required for execution")
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "nav.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["date", "nav"])
        for i, value in enumerate(returns):
            capital *= 1 + value
            writer.writerow([(start + timedelta(days=i)).isoformat(), capital])
    (args.output / "report.html").write_text(
        '<html lang="zh-CN"><meta charset="utf-8"><h1>自定义模块接入完成</h1>'
        "<p>合成数据用于工作台工程验收。</p></html>",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
