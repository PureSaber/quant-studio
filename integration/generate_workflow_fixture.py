"""Build current-date SYNTHETIC browser fixtures using real native execution.

Run with the combined Studio/Pipeline integration interpreter after committing code.
The output directory must be new and isolated. No scheduler is created.
"""

import argparse
import json
import os
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml
from quant_lab.research import file_hash, load_recipe
from quant_pipeline.research_demo import create_demo

from quant_studio.projects import ProjectStore
from quant_studio.recipes import RecipeStore, load_input_config, recipe_template
from quant_studio.research_cards import FIELDS
from quant_studio.runner import run


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def generate(output, workspace, python):
    output, workspace = output.resolve(), workspace.resolve()
    if output.exists():
        raise FileExistsError("Use a new isolated fixture directory")
    output.mkdir(parents=True)
    seed = create_demo(output / "seed")
    recipe = load_recipe(seed)
    inputs = seed.parent / "inputs"
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    day = pd.Timestamp(now.date())
    closed = day if now.hour >= 15 else day - pd.Timedelta(days=1)
    closed = pd.bdate_range(end=closed, periods=1)[0]
    days = pd.bdate_range(end=closed, periods=360)
    original = pd.bdate_range("2023-01-02", periods=360)
    mapping = dict(zip(original, days, strict=True))
    symbols = {"159919": "510500", "512000": "588000"}
    manifest = json.loads((inputs / "manifest.json").read_text(encoding="utf-8"))
    for name in ("raw", "adjusted", "benchmark"):
        path = inputs / f"{name}.parquet"
        frame = pd.read_parquet(path)
        frame["date"] = pd.to_datetime(frame["date"]).map(mapping)
        if "symbol" in frame:
            frame["symbol"] = frame["symbol"].replace(symbols)
        frame.to_parquet(path, index=False)
        manifest["files"][name]["sha256"] = file_hash(path)
    future = pd.bdate_range(start=day + pd.Timedelta(days=1), periods=30)
    calendar = pd.DataFrame({"date": pd.bdate_range(days[0], future[-1])})
    calendar.to_parquet(inputs / "calendar.parquet", index=False)
    manifest["files"]["calendar"]["sha256"] = file_hash(inputs / "calendar.parquet")
    write(inputs / "manifest.json", manifest)
    catalog = pd.read_csv(inputs / "catalog.csv", dtype={"symbol": str})
    catalog["symbol"] = catalog["symbol"].replace(symbols)
    catalog.loc[catalog.symbol.isin(["510500", "588000"]), "venue"] = "SSE"
    catalog["effective_from"] = days[0].strftime("%Y-%m-%dT00:00:00Z")
    catalog["available_at"] = catalog["effective_from"]
    catalog["effective_to"] = (future[-1] + pd.Timedelta(days=1)).strftime(
        "%Y-%m-%dT00:00:00Z"
    )
    catalog.to_csv(inputs / "catalog.csv", index=False)
    recipe["interval"] = {"start": str(days[-65].date()), "end": str(closed.date())}
    recipe["diagnostics"] = {
        "single_factors": False,
        "ablations": False,
        "cost_multipliers": [],
        "signal_delays": [],
        "frequencies": [],
    }
    recipe["variants"] = []
    recipe["hypothesis"] = "SYNTHETIC 软件验收：测试登记流程；不是市场或策略有效性证据"
    seed.write_text(yaml.safe_dump(recipe, allow_unicode=True), encoding="utf-8")
    runs = output / "runs"
    runs.mkdir()
    runtime = output / "runtimes.json"
    write(
        runtime,
        {
            "schema_version": "quant-studio.runtimes/v1",
            "python_by_repo": {
                "quant-pipeline": str(python),
                "quant-pipeline-registration": str(python),
                "quant-pipeline-frozen": str(python),
            },
        },
    )
    registration = output / "registration.json"
    write(
        registration,
        {
            "schema": "quant.project-registration/v1",
            "runs_root": str(runs),
            "accounts_root": str(output / "accounts"),
            "state_root": str(output / "registration-state"),
            "preview_ttl_seconds": 900,
        },
    )
    from quant_data_kit.research_dataset import build_dataset

    dataset = output / "daily-data"
    build_dataset(
        dataset,
        symbols=["510300", "510500", "159915", "588000"],
        start=str(days[0].date()),
        end=str(closed.date()),
        captured_at=now.isoformat(),
        source_dir=inputs,
        source_uri="synthetic://workflow-acceptance",
        source_version="current-date-software-fixture/v1",
        license_note="Locally generated synthetic fixture; no market observation",
    )
    daily = output / "daily.json"
    write(
        daily,
        {
            "schema": "quant.etf-daily/v1",
            "dataset_root": str(dataset),
            "recipe": str(seed),
            "output_root": str(output / "daily-receipts"),
            "forward_config": None,
        },
    )
    environment = {
        "QUANT_WORKSPACE_ROOT": str(workspace),
        "QUANT_STUDIO_RUNTIMES": str(runtime),
        "QUANT_STUDIO_REGISTRATION": str(registration),
        "QUANT_STUDIO_ETF_DAILY": str(daily),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
    }
    os.environ.update(environment)
    recipes = RecipeStore(runs)
    saved = []
    for label in ("baseline", "changed-capital", "missing-input"):
        value = deepcopy(recipe)
        value["study_id"] = "synthetic-workflow-" + label
        if label == "changed-capital":
            value["costs"]["initial_capital"] *= 2
        if label == "missing-input":
            value["inputs"]["bundle"] = str(output / "intentionally-missing")
        path = output / (label + ".yaml")
        path.write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")
        saved.append(
            recipes.save(
                "SYNTHETIC · " + label,
                "etf-weekly-research",
                {},
                snapshot=str(path),
                input_config=load_input_config("etf-weekly-research", path),
            )
        )
    card = {key: "仅验证软件流程；人工生成行情，不评价策略" for key in FIELDS}
    card.update(sample_role="retrospective", **recipe["interval"])
    project = ProjectStore(runs).save(
        "SYNTHETIC 流程验收",
        "当前日期下的比较、决策、预览、登记和恢复是否有效？",
        recipes=[{"id": r["id"], "revision": r["revision"]} for r in saved],
        research_card=card,
        notes="合成交易日历不是交易所真实日历；全部记录不计自然交易日。",
    )
    results = []
    for name, record in zip(
        ("baseline-a", "baseline-b", "changed", "failed"),
        (saved[0], saved[0], saved[1], saved[2]),
        strict=True,
    ):
        template = recipe_template(record)
        template.metadata["research_project"] = {
            "id": project["id"],
            "revision": project["revision"],
        }
        result = run(
            template, {}, execute=True, runs_root=runs, run_id=name, timeout=600
        )
        results.append({"run_id": result.run_id, "status": result.status})
        print(json.dumps(results[-1]), flush=True)
        if name != "failed" and result.status != "succeeded":
            raise RuntimeError(f"Native fixture failed; inspect {result.run_dir}")
        if name == "failed" and result.status == "succeeded":
            raise RuntimeError("Missing-input fixture unexpectedly succeeded")
    value = {
        "scope": "SYNTHETIC software acceptance only; never a natural trading day",
        "project_id": project["id"],
        "project_url": f"/projects/{project['id']}",
        "registration_url": f"/projects/{project['id']}/registration",
        "start": str(future[0].date()),
        "end": str(future[-1].date()),
        "runs_root": str(runs),
        "results": results,
        "environment": environment,
        "python": str(python),
        "daily_profile": str(daily),
        "registration_config": str(registration),
    }
    write(output / "fixture.json", value)
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    print(
        json.dumps(
            generate(args.output, args.workspace, args.python), ensure_ascii=False
        )
    )
