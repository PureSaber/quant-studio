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
    validate_shape(profile)
    receipts = []
    with use_settings(profile):
        for line, knobs in [
            ("fx", {"instrument": "spot"}),
            ("fx", {"instrument": "forward"}),
            ("rates", {}),
            ("credit", {}),
        ]:
            template = f"macro-{line}-research"
            check = preflight(
                template, knobs, snapshot=bundle, runs_root=output / "preflights"
            )
            if check.status != "checked":
                raise RuntimeError(f"preflight failed: {check.as_json()}")
            result = run(
                template,
                knobs,
                execute=True,
                snapshot=bundle,
                runs_root=output / "runs",
            )
            if result.status != "succeeded":
                raise RuntimeError(f"study failed: {result.as_json()}")
            verified = subprocess.run(
                [
                    str(python),
                    "-I",
                    "-m",
                    "quant_macro.cli",
                    "verify",
                    "--output",
                    str(result.run_dir / "strategy-output"),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
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
    print(json.dumps({"passed": len(receipts), "evidence_kind": "synthetic"}))


if __name__ == "__main__":
    main()
