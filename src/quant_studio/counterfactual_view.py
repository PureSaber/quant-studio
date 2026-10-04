"""Verify a timing candidate family in its native Python before displaying it."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def source_snapshot(run: Path) -> dict[str, str]:
    """Bind the complete family, including failures and forbidden publication files."""
    run = run.resolve()
    identities = {}
    for path in sorted(run.rglob("*")):
        relative = path.relative_to(run)
        if relative.parts[0] == "studio-view":
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(run):
            raise ValueError("Counterfactual evidence must stay inside the native run")
        if path.name in {"position_scale.json", "portfolio_overlay.yaml"}:
            raise ValueError("Counterfactual candidates must not publish positions")
        if path.is_file():
            identities[relative.as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return identities


def export_view(run: Path, output: Path, project: str) -> dict:
    from quant_timing.counterfactual import validate_counterfactual

    run, output = run.resolve(), output.resolve()
    if project != "quant-timing" or output != run / "studio-view":
        raise ValueError("Counterfactual view requires its native output directory")
    if output.exists():
        raise FileExistsError("Counterfactual view already exists")
    identities = source_snapshot(run)
    receipt = validate_counterfactual(run)
    protocol = json.loads((run / "protocol.json").read_text(encoding="utf-8"))
    if receipt["status"] not in {"complete", "incomplete"}:
        raise ValueError("Unsupported counterfactual status")
    if receipt["evidence_kind"] not in {
        "synthetic",
        "retrospective",
        "user_provided",
        "unspecified",
    }:
        raise ValueError("Unsupported counterfactual evidence kind")
    evidence = {
        "schema_version": "quant-studio.timing-counterfactual-view/v1",
        "project": project,
        "evidence_kind": receipt["evidence_kind"],
        "native_exit_code": 0 if receipt["status"] == "complete" else 2,
        "receipt": receipt,
        "effective_config": protocol["inputs"]["effective_config"],
        "effective_config_sha256": protocol["inputs"]["effective_config_sha256"],
        "source_files": identities,
    }
    validate_counterfactual(run)
    if source_snapshot(run) != identities:
        raise ValueError("Counterfactual evidence changed during projection")
    output.mkdir()
    (output / "view.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project", choices=["quant-timing"], required=True)
    args = parser.parse_args()
    print(json.dumps(export_view(args.run, args.output, args.project)))
