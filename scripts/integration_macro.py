"""Exercise macro modules through the Studio dispatcher in a new output directory.

Run with the Studio interpreter. --python selects an independently installed
quant-macro interpreter; both applications must already be installed.
"""

import argparse
import json
import subprocess
from pathlib import Path

from quant_studio.runner import preflight, run
from quant_studio.settings import use_settings
from quant_studio.setup import empty_profile, validate_shape


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--public-root", type=Path)
    parser.add_argument("--futures", action="store_true")
    args = parser.parse_args()
    workspace, python, output = (
        args.workspace.resolve(),
        args.python.resolve(),
        args.output.resolve(),
    )
    output.mkdir(parents=True, exist_ok=False)
    bundle = output / "synthetic-bundle"
    subprocess.run(
        [str(python), "-I", "-m", "quant_macro.cli", "demo", "--output", str(bundle)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    profile = empty_profile()
    profile["environment"]["QUANT_WORKSPACE_ROOT"] = str(workspace)
    profile["python_by_repo"]["quant-macro"] = str(python)
    profile["python_by_repo"]["quant-macro-futures"] = str(python)
    validate_shape(profile)
    receipts = []
    cases = [
        ("macro-fx-research", {"instrument": "spot"}, bundle, "quant_macro.cli"),
        ("macro-fx-research", {"instrument": "forward"}, bundle, "quant_macro.cli"),
        ("macro-rates-research", {}, bundle, "quant_macro.cli"),
        ("macro-credit-research", {}, bundle, "quant_macro.cli"),
        ("macro-fx-portfolio", {}, bundle, "quant_macro.cli"),
        ("macro-bond-replay", {}, bundle, "quant_macro.cli"),
        ("macro-etf-relative-value", {}, bundle, "quant_macro.cli"),
    ]
    if args.public_root:
        for provider in ["ecb", "fed"]:
            cases.append(
                (
                    "macro-reference-history",
                    {},
                    args.public_root.resolve() / f"{provider}-bundle",
                    "quant_macro.cli",
                )
            )
    if args.futures:
        for product in ["6E", "ZN", "SR3", "TS", "TF", "T", "TL"]:
            location = output / f"synthetic-{product}"
            subprocess.run(
                [
                    str(python),
                    "-I",
                    "-c",
                    "from quant_macro.futures_demo import create; import sys; "
                    "create(sys.argv[1],sys.argv[2])",
                    str(location),
                    product,
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            template = (
                "fx-futures-research" if product == "6E" else "rates-futures-research"
            )
            cases.append(
                (template, {"product": product}, location, "quant_macro.futures_cli")
            )
    with use_settings(profile):
        for template, knobs, snapshot, module in cases:
            check = preflight(
                template, knobs, snapshot=snapshot, runs_root=output / "preflights"
            )
            if check.status != "checked":
                raise RuntimeError(f"preflight failed: {check.as_json()}")
            result = run(
                template,
                knobs,
                execute=True,
                snapshot=snapshot,
                runs_root=output / "runs",
            )
            if result.status != "succeeded":
                raise RuntimeError(f"study failed: {result.as_json()}")
            verified = subprocess.run(
                [
                    str(python),
                    "-I",
                    "-m",
                    module,
                    "verify",
                    "--output",
                    str(result.run_dir / "strategy-output"),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            receipts.append(
                {
                    "template": template,
                    "knobs": knobs,
                    "preflight": check.as_json(),
                    "run": result.as_json(),
                    "verification": json.loads(verified.stdout),
                }
            )
    (output / "receipts.json").write_text(
        json.dumps(receipts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "passed": len(receipts),
                "evidence_kinds": sorted({r["run"]["evidence_kind"] for r in receipts}),
            }
        )
    )


if __name__ == "__main__":
    main()
