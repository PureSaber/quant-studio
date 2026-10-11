"""Accept installed option/swap templates without starting or changing a server."""

import argparse
import json
import sys
from pathlib import Path

from quant_studio.runner import preflight, run
from quant_studio.settings import use_settings
from quant_studio.templates import load_template

TEMPLATES = (
    "options-american-chain",
    "options-lifecycle",
    "options-american-dividends",
    "options-surface-calibration",
    "options-strategy-margin",
    "swaps-market-curves",
    "swaps-eur-multicurve",
    "swaps-gbp-sonia",
    "swaps-counterparty-exposure",
    "swaps-clearing-reconciliation",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--runs", required=True)
    args = parser.parse_args()
    out = Path(args.runs).resolve()
    out.mkdir(parents=True, exist_ok=False)
    profile = {
        "environment": {"QUANT_WORKSPACE_ROOT": str(Path(args.workspace).resolve())},
        "python_by_repo": {
            "quant-options-market": sys.executable,
            "quant-swaps-market": sys.executable,
        },
    }
    results = []
    with use_settings(profile):
        for identifier in TEMPLATES:
            template = load_template(identifier)
            selected = args.bundle if template.requires_snapshot else None
            for operation in ("preflight", "run"):
                result = (
                    preflight(identifier, snapshot=selected, runs_root=out, timeout=120)
                    if operation == "preflight"
                    else run(
                        identifier,
                        execute=True,
                        snapshot=selected,
                        runs_root=out,
                        timeout=120,
                    )
                )
                record = {
                    "template": identifier,
                    "operation": operation,
                    **result.as_json(),
                }
                results.append(record)
                (out / "acceptance.json").write_text(
                    json.dumps(results, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                print(identifier, operation, result.status, flush=True)
                if result.status not in {"checked", "succeeded"}:
                    raise SystemExit(result.message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
