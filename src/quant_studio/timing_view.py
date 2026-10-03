"""Project native timing evidence with its own Python, without rerunning research."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export_view(run: Path, output: Path, project: str) -> dict:
    import pandas as pd
    from quant_timing.contract import json_sha256, validate_standard_run
    from quant_timing.export import exit_code

    run, output = run.resolve(), output.resolve()
    if project != "quant-timing" or output != run / "studio-view":
        raise ValueError("Timing view requires its isolated native output directory")
    if output.exists():
        raise FileExistsError("Timing view already exists")
    standard = run / "standard"
    raw_manifest = json.loads(
        (standard / "run_manifest.json").read_text(encoding="utf-8")
    )
    expected = {
        name: f"{name}.csv"
        for name in ("returns", "positions", "orders", "costs", "exposures")
    }
    expected["metrics"] = "metrics.json"
    artifacts = raw_manifest["artifacts"]
    if (
        len(artifacts) != len(expected)
        or {a["name"]: a["path"] for a in artifacts} != expected
    ):
        raise ValueError("Unexpected timing artifact paths")
    paths = [
        standard / "run_manifest.json",
        *(standard / name for name in expected.values()),
        run / "validation/fold_metrics.csv",
        run / "run_context.json",
        run / "decision.json",
    ]
    for path in paths:
        if not path.resolve().is_relative_to(run):
            raise ValueError("Timing evidence escaped the native run")
    identities = {path.relative_to(run).as_posix(): digest(path) for path in paths}
    manifest = validate_standard_run(run)
    if manifest.project != project or manifest.tags.get("cost_unit") != "currency":
        raise ValueError("Native timing identity or accounting semantics mismatch")
    metrics = json.loads((standard / "metrics.json").read_text(encoding="utf-8"))
    if metrics.get("research_view_schema") != "quant-timing.research-view/v1":
        raise ValueError(
            "Timing upstream does not provide verified research view evidence"
        )
    context = metrics["input_context"]
    if context != json.loads((run / "run_context.json").read_text(encoding="utf-8")):
        raise ValueError("Timing input context mismatch")
    checks = context["input_checks"]
    if (
        checks["effective_config_sha256"] != manifest.config_sha256
        or json_sha256(checks["effective_config"]) != manifest.config_sha256
    ):
        raise ValueError("Timing effective configuration mismatch")
    kind = context["evidence_kind"]
    if kind not in {"synthetic", "retrospective", "user_provided", "unspecified"}:
        raise ValueError("Unsupported timing evidence kind")
    source = metrics["measurement_basis"]["source"] or {}
    if kind != source.get("evidence_kind", "unspecified"):
        raise ValueError("Timing source declaration mismatch")
    decision = metrics["publication"]
    if decision != json.loads((run / "decision.json").read_text(encoding="utf-8")):
        raise ValueError("Timing publication decision mismatch")
    if (
        decision["action"] != metrics["export_action"]
        or decision["position_scale"] != metrics["export_position_scale"]
    ):
        raise ValueError("Timing publication summary mismatch")
    if decision["action"] not in {"use", "hold_previous", "blocked"}:
        raise ValueError("Unsupported timing publication action")
    scale_file = run / "position_scale.json"
    if decision["action"] == "use":
        if not scale_file.resolve().is_relative_to(run):
            raise ValueError("Published scale escaped the run")
        scale = json.loads(scale_file.read_text(encoding="utf-8"))
        if (
            scale["position_scale"] != decision["position_scale"]
            or scale["as_of"] != decision["as_of"]
        ):
            raise ValueError("Published scale mismatch")
        identities["position_scale.json"] = digest(scale_file)
    elif scale_file.exists():
        raise ValueError("Blocked or held decision must not publish a new scale")
    if identities["validation/fold_metrics.csv"] != metrics["fold_metrics_sha256"]:
        raise ValueError("Walk-forward table mutated")
    folds = metrics["walk_forward_folds"]
    if (
        len(folds) != metrics["fold_count"]
        or sum(row["n_decisions"] > 0 for row in folds) != metrics["scored_folds"]
    ):
        raise ValueError("Walk-forward counts mismatch")
    native_code = exit_code(metrics, decision)
    returns = pd.read_csv(standard / "returns.csv")
    if returns.empty or returns["strategy"].unique().tolist() != ["timing"]:
        raise ValueError("Expected one nonempty timing return series")
    if (
        returns["date"].duplicated().any()
        or not returns["date"].is_monotonic_increasing
    ):
        raise ValueError("Timing dates must be unique and ordered")
    benchmark = (1 + returns["benchmark_return"]).cumprod()
    for actual, key in (
        (float(returns["nav"].iloc[-1]) - 1, "descriptive_full_sample_net"),
        (float(benchmark.iloc[-1]) - 1, "descriptive_full_sample_benchmark"),
    ):
        if not math.isfinite(actual) or not math.isclose(
            actual, metrics[key], abs_tol=1e-12, rel_tol=1e-12
        ):
            raise ValueError("Timing descriptive curve does not match native summary")
    validate_standard_run(run)
    if any(digest(run / name) != value for name, value in identities.items()):
        raise ValueError("Timing evidence changed during projection")
    evidence = {
        "schema_version": "quant-studio.timing-view/v1",
        "project": project,
        "evidence_kind": kind,
        "native_exit_code": native_code,
        "publication": decision,
        "fold_count": metrics["fold_count"],
        "scored_folds": metrics["scored_folds"],
        "leakage_passed": metrics["leakage_passed"],
        "mean_excess_return": metrics["mean_excess_return"],
        "mean_matched_excess_return": metrics["mean_matched_excess_return"],
        "measurement_basis": metrics["measurement_basis"],
        "effective_config": checks["effective_config"],
        "effective_config_sha256": manifest.config_sha256,
        "verified_artifacts": len(manifest.artifacts),
        "source_files": identities,
    }
    output.mkdir()
    (output / "folds.csv").write_bytes(
        (run / "validation/fold_metrics.csv").read_bytes()
    )
    if native_code == 0:
        returns[["date", "nav"]].assign(initial_nav=1).to_csv(
            output / "nav.csv", index=False
        )
        pd.DataFrame(
            {"date": returns["date"], "nav": benchmark, "initial_nav": 1}
        ).to_csv(output / "benchmark_nav.csv", index=False)
        for name in ("positions", "orders", "costs"):
            (output / f"{name}.csv").write_bytes(
                (standard / f"{name}.csv").read_bytes()
            )
    evidence["view_files"] = {path.name: digest(path) for path in output.iterdir()}
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
