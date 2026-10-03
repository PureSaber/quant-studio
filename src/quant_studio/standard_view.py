"""Read-only fixture views, executed with the producing application's Python.

This file is a standalone entry point so Studio itself needs neither pandas nor
QLab. All balances come from the validated native ledger; no returns are chained.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path

CONTRACTS = {
    "quant-futures-spread": ("fixture-certified", "certification", "CNY"),
    "quant-crypto-basis": ("offline-fixture", "environment", "USDT"),
}
TABLES = {
    "account": "portfolio_snapshots",
    "positions": "positions",
    "orders": "orders",
    "fills": "fills",
    "margin": "margin",
    "costs": "costs",
    "cash_ledger": "cash_ledger",
}


def fingerprint(root: Path) -> dict:
    return {
        path.relative_to(root).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def fixed_decimal(units, scale) -> Decimal:
    # Constructing the decimal tuple avoids both float conversion and context
    # rounding for high-precision native balances.
    integer, places = int(str(units)), int(str(scale))
    if places < 0:
        raise ValueError("Negative fixed-point scale")
    digits = tuple(int(char) for char in str(abs(integer)))
    return Decimal((int(integer < 0), digits, -places))


def project_table(frame):
    import pandas as pd

    columns = [name for name in frame.columns if not name.endswith("_scale")]
    fields = [name.removesuffix("_units") for name in columns]
    rows = []
    for record in frame.to_dict(orient="records"):
        row = []
        for column in columns:
            value = record[column]
            if pd.isna(value):
                row.append("")
            elif column.endswith("_units"):
                scale = column.removesuffix("_units") + "_scale"
                if column in {"initial_margin_units", "maintenance_margin_units"}:
                    scale = "margin_scale"
                row.append(format(fixed_decimal(value, record[scale]), "f"))
            elif column.endswith("_time"):
                stamp = pd.Timestamp(value)
                if stamp.tzinfo is None:
                    raise ValueError("Native event time must include a timezone")
                row.append(stamp.tz_convert("UTC").isoformat())
            else:
                row.append(value)
        rows.append(dict(zip(fields, row, strict=True)))
    return fields, rows


def export_view(run: Path, output: Path, project: str) -> dict:
    import pandas as pd
    from quant_lab import load_and_validate_standard_run

    source = (run / "standard" / "v2").resolve()
    output = output.resolve()
    if output == source or source in output.parents or output in source.parents:
        raise ValueError("View output must be separate from native standard/v2")
    if output.exists():
        raise FileExistsError("View output already exists")
    before = fingerprint(source)
    manifest = load_and_validate_standard_run(run)
    capability, tag, currency = CONTRACTS[project]
    if (
        manifest.project != project
        or manifest.profile != "backtest-ledger"
        or capability not in manifest.capabilities
        or manifest.tags.get(tag) != capability
        or manifest.base_currency != currency
    ):
        raise ValueError(
            "Native run does not match the declared offline fixture contract"
        )
    artifacts = {item.name: item for item in manifest.artifacts}
    config = json.loads((source / artifacts["config"].path).read_text(encoding="utf-8"))
    account = config["account"] if project == "quant-futures-spread" else config
    opening = Decimal(str(account["initial_cash"]))
    if not opening.is_finite() or opening <= 0 or account["base_currency"] != currency:
        raise ValueError("Invalid native opening capital or currency")
    tables = {
        name: project_table(
            pd.read_parquet(source / artifacts[artifact].path, dtype_backend="pyarrow")
        )
        for name, artifact in TABLES.items()
    }
    snapshots = tables["account"][1]
    if not snapshots or len({row["account_id"] for row in snapshots}) != 1:
        raise ValueError("Expected one nonempty native account")
    if any(row["base_currency"] != currency for row in snapshots):
        raise ValueError("Native snapshot currency mismatch")
    dates = [row["event_time"] for row in snapshots]
    if len(set(dates)) != len(dates) or dates != sorted(dates):
        raise ValueError("Native snapshot event times must be unique and ordered")
    if any(Decimal(row["nav"]) < 0 for row in snapshots):
        raise ValueError("Negative native NAV cannot be displayed by this template")
    nav_rows = [
        {
            "date": row["event_time"],
            "nav": row["nav"],
            "initial_nav": format(opening, "f"),
        }
        for row in snapshots
    ]
    # Verify the files read by pandas still match the checked native contract.
    load_and_validate_standard_run(run)
    if before != fingerprint(source):
        raise ValueError("Native evidence changed while projecting the view")
    evidence = {
        "schema_version": "quant-studio.standard-view/v1",
        "project": project,
        "native_run_id": manifest.run_id,
        "evidence_kind": "synthetic",
        "base_currency": currency,
        "initial_nav": format(opening, "f"),
        "observations": len(nav_rows),
        "time_basis": "UTC event snapshots; no daily resampling",
        "manifest_sha256": before["run_manifest.json"][0],
        "verified_artifacts": len(manifest.artifacts),
        "source_files": {name: value[0] for name, value in before.items()},
        "table_rows": {name: len(rows) for name, (_, rows) in tables.items()},
    }
    output.mkdir(parents=True)
    for name, (fields, rows) in {
        "nav": (["date", "nav", "initial_nav"], nav_rows),
        **tables,
    }.items():
        with (output / f"{name}.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    (output / "view.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project", choices=sorted(CONTRACTS), required=True)
    args = parser.parse_args()
    print(json.dumps(export_view(args.run, args.output, args.project)))


if __name__ == "__main__":
    main()
