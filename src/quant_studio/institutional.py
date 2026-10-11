"""Read-only preparation and local report bridge for institutional research."""

import argparse
import json
from pathlib import Path


def inside(root, name):
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(root) or not candidate.exists():
        raise ValueError("research input missing or outside selected snapshot")
    return candidate


def evaluate(workflow, snapshot):
    root = Path(snapshot).resolve()
    if workflow == "inventory-market-making":
        from quant_execution.market_making_cli import evaluate as simulate

        raw = json.loads(inside(root, "market-making.json").read_text("utf-8-sig"))
        report = simulate(raw)
        return (
            report,
            {"fills.csv": report["fills"]},
            [raw["instrument"]["instrument_id"]],
        )
    if workflow == "merger-event-research":
        from quant_us_equity.merger_replay import replay

        raw = json.loads(inside(root, "merger-replay.json").read_text("utf-8-sig"))
        report, tables = replay(
            inside(root, "events.sqlite"), inside(root, "merger-prices"), raw
        )
        ids = sorted(
            {
                r[key]
                for r in report["study"]["events"]
                for key in ("target", "acquirer")
            }
        )
    elif workflow == "options-hedged-portfolio":
        from quant_options.portfolio_replay import replay

        raw = json.loads(inside(root, "options-replay.json").read_text("utf-8-sig"))
        for name in raw["bundles"]:
            inside(root, name)
        report, tables = replay(raw, config_dir=root)
        ids = sorted(raw["positions"])
    else:
        raise ValueError("unknown institutional research workflow")
    # Studio already supports lifecycle.csv. Keep heterogeneous event rows intact.
    decisions = tables.pop("decisions.csv")
    columns = list(dict.fromkeys(key for row in decisions for key in row))
    tables["lifecycle.csv"] = [
        {key: row.get(key) for key in columns} for row in decisions
    ]
    return report, tables, ids


def main(argv=None):
    parser = argparse.ArgumentParser(description="机构研究输入预检及离线报告")
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args(argv)
    if json.loads(Path(args.config).read_text("utf-8-sig")) != {}:
        raise ValueError(
            "this template uses the explicit configuration "
            "inside the selected input package"
        )
    report, tables, ids = evaluate(args.workflow, args.snapshot)
    from quant_execution.research_account import ledger_rows

    tables["ledger.csv"] = ledger_rows(report)
    if args.preflight:
        result = {
            "schema_version": "quant.institutional.preflight/1",
            "software_preflight": "pass",
            "read_only": True,
            "investable": False,
            "rows": max(1, sum(len(v) for v in tables.values())),
            "instrument_ids": ids,
            "evidence_kind": report["evidence_kind"],
            "input_sha256": report["input_sha256"],
        }
    else:
        from quant_execution.derivative_replay import write_artifacts

        if not args.output:
            raise ValueError("--output required")
        write_artifacts(args.output, report, tables)
        result = {
            "output": args.output,
            "evidence_kind": report["evidence_kind"],
            "sends_live_orders": False,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
