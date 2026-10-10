import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from quant_studio import QuantStudioError
from quant_studio.datasets import DatasetStore, file_hash
from quant_studio.intake_tools import IntakeWorkspace
from quant_studio.notebooks import NotebookStore
from quant_studio.projects import ProjectStore
from quant_studio.recipes import _atomic


def _configure_notebook(tmp_path, monkeypatch, python=None):
    path = tmp_path / "notebook-config.json"
    path.write_text(
        json.dumps(
            {
                "python": python or sys.executable,
                "lab_url": "http://127.0.0.1:8890/",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("QUANT_STUDIO_NOTEBOOK_CONFIG", str(path))


def _integration_value(name, legacy):
    return os.environ.get(name) or os.environ.get(legacy)


def _check(version, purpose, columns):
    return {
        "allowed": True,
        "status": "ready",
        "purpose": purpose,
        "version_id": version,
        "scope": {
            "columns": columns,
            "symbols": ["000001"],
            "start": "2026-01-02",
            "end": "2026-01-05",
            "full_scan": False,
            "total_rows": 2,
            "matched_rows": 2,
        },
        "integrity": {"valid": True},
        "issues": [],
        "market_certified": False,
        "certification": "contract-and-data-checks-only",
    }


def _intake_dataset(root, *, purpose="daily_bars_research", columns=None, check=None):
    identifier = "1" * 32
    version = "2" * 64
    columns = columns or ["symbol", "date", "close"]
    snapshot = root / "snapshot"
    snapshot.mkdir(parents=True)
    rows = [
        {"symbol": "000001", "date": "2026-01-02", "close": 10.0},
        {"symbol": "000001", "date": "2026-01-05", "close": 11.0},
    ]
    (snapshot / "manifest.json").write_text(
        json.dumps({"id": version, "name": "bars"}), encoding="utf-8"
    )
    (snapshot / "rows.json").write_text(json.dumps(rows), encoding="utf-8")
    selected_check = check or _check(version, purpose, columns)
    (snapshot / "check.json").write_text(
        json.dumps(selected_check, ensure_ascii=False), encoding="utf-8"
    )
    hashes = {
        item.relative_to(snapshot).as_posix(): file_hash(item)
        for item in snapshot.iterdir()
    }
    scope = {
        "columns": columns,
        "symbols": ["000001"],
        "start": "2026-01-02",
        "end": "2026-01-05",
    }
    record = {
        "schema": "quant-studio.dataset/v1",
        "id": identifier,
        "name": "固定日线",
        "template_id": "research-intake",
        "kind": "intake",
        "parent": None,
        "path": str(snapshot),
        "hashes": hashes,
        "registered_at": "2026-10-10T00:00:00+00:00",
        "provider": "fixture",
        "symbols": scope["symbols"],
        "start": scope["start"],
        "end": scope["end"],
        "evidence": "fixture",
        "limits": "fixture",
        "intake": {
            "name": "bars",
            "version": version,
            "purpose": purpose,
            "scope": scope,
            "check": selected_check,
        },
        "identity": hashlib.sha256(
            json.dumps(hashes, sort_keys=True).encode()
        ).hexdigest(),
    }
    store = DatasetStore(root)
    _atomic(
        store.root / f"{identifier}.json",
        json.dumps(record, ensure_ascii=False),
    )
    return record


def _fake_qdk_source(tmp_path, *, mutate=False):
    source = tmp_path / ("qdk-mutating" if mutate else "qdk")
    script = source / "src/quant_data_kit/research_intake.py"
    script.parent.mkdir(parents=True)
    mutation = "(snapshot / 'rows.json').write_text('changed')" if mutate else ""
    script.write_text(
        "import argparse,json\nfrom pathlib import Path\n"
        "p=argparse.ArgumentParser(); p.add_argument('action'); "
        "p.add_argument('--snapshot'); p.add_argument('--purpose'); "
        "p.add_argument('--columns',nargs='+'); p.add_argument('--symbols',nargs='+'); "
        "p.add_argument('--start'); p.add_argument('--end'); "
        "p.add_argument('--limit')\n"
        "a=p.parse_args(); snapshot=Path(a.snapshot)\n"
        "check=json.loads((snapshot/'check.json').read_text()); "
        "rows=json.loads((snapshot/'rows.json').read_text())\n"
        "assert a.action=='read-snapshot' and a.limit=='0'\n"
        "assert a.purpose==check['purpose']\n"
        "assert a.columns is None or a.columns==check['scope']['columns']\n"
        "assert a.symbols is None or a.symbols==check['scope']['symbols']\n"
        "assert a.start is None or a.start==check['scope']['start']\n"
        "assert a.end is None or a.end==check['scope']['end']\n"
        f"{mutation}\n"
        "data={'version_id':check['version_id'],'check':check,'rows':rows,"
        "'matched_rows':len(rows),'returned_rows':len(rows),'complete':True,"
        "'truncated':False,'max_rows':None}\n"
        "print(json.dumps({'schema_version':'qdk.research-intake-response/v1',"
        "'ok':True,'action':'read-snapshot','data':data}))\n",
        encoding="utf-8",
    )
    return source


def test_non_intake_initialize_does_not_require_qdk_configuration(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("QUANT_STUDIO_INTAKE_CONFIG", raising=False)
    project = ProjectStore(tmp_path).save("普通研究", "输入分布如何")
    draft = NotebookStore(tmp_path).initialize(project["id"])
    context = json.loads((draft.parent / "inputs.json").read_text(encoding="utf-8"))
    assert context["datasets"] == []
    assert "intake_runtime" not in context


def test_intake_context_preserves_registered_purpose_scope_and_hashes(tmp_path):
    dataset = _intake_dataset(tmp_path)
    project = ProjectStore(tmp_path).save(
        "日线研究", "收益和缺失是否稳定", datasets=[dataset["id"]]
    )
    context = NotebookStore(tmp_path)._context(project)
    linked = context["datasets"][0]
    assert linked["kind"] == "intake"
    assert linked["intake"] == dataset["intake"]
    assert linked["hashes"] == dataset["hashes"]


def test_problem_templates_are_concrete_and_never_overwrite_existing_draft(
    tmp_path, monkeypatch
):
    dataset = _intake_dataset(tmp_path)
    project = ProjectStore(tmp_path).save(
        "收益研究", "收益是否被缺失值驱动", datasets=[dataset["id"]]
    )
    source = _fake_qdk_source(tmp_path)
    monkeypatch.setattr(
        "quant_studio.intake_tools.IntakeWorkspace.runtime",
        lambda self: (sys.executable, source),
    )
    store = NotebookStore(tmp_path)
    draft = store.initialize(project["id"], template="price-return-missingness")
    notebook = json.loads(draft.read_text(encoding="utf-8"))
    cell_text = "\n".join(
        "".join(cell["source"]) if isinstance(cell["source"], list) else cell["source"]
        for cell in notebook["cells"]
    )
    assert "收益" in cell_text and "缺失" in cell_text
    assert "read_intake_dataset" in cell_text
    assert "自动检查" in cell_text and "人工判断" in cell_text
    before = draft.read_bytes()
    store.initialize(project["id"], template="data-quality-exploration")
    assert draft.read_bytes() == before


def test_historical_template_blocks_when_fixed_intake_lacks_pit_admission(
    tmp_path, monkeypatch
):
    blocked = _check("2" * 64, "historical_financial_factor_backtest", ["period_end"])
    blocked["allowed"] = False
    blocked["status"] = "blocked"
    blocked["issues"] = [{"rule": "point_in_time_availability", "severity": "error"}]
    dataset = _intake_dataset(
        tmp_path,
        purpose="historical_financial_factor_backtest",
        columns=["period_end"],
        check=blocked,
    )
    project = ProjectStore(tmp_path).save(
        "财务研究", "发布时点是否可用", datasets=[dataset["id"]]
    )
    source = _fake_qdk_source(tmp_path)
    monkeypatch.setattr(
        "quant_studio.intake_tools.IntakeWorkspace.runtime",
        lambda self: (sys.executable, source),
    )
    with pytest.raises(QuantStudioError, match="时点|PIT|准入"):
        NotebookStore(tmp_path).initialize(
            project["id"], template="historical-financial-availability"
        )


def test_frozen_reader_executes_fixed_scope_and_detects_source_change(tmp_path):
    from quant_studio.notebook_data_reader import (
        probe_python_environment,
        read_intake_dataset,
    )

    dataset = _intake_dataset(tmp_path)
    source = _fake_qdk_source(tmp_path)
    script = source / "src/quant_data_kit/research_intake.py"
    runtime = {
        "python": sys.executable,
        "script": str(script),
        "script_sha256": file_hash(script),
        "environment": probe_python_environment(sys.executable),
    }
    result = read_intake_dataset(dataset, runtime)
    assert result["complete"] is True
    assert result["rows"][0]["symbol"] == "000001"
    assert result["check"] == dataset["intake"]["check"]

    changed_source = _fake_qdk_source(tmp_path, mutate=True)
    changed_script = changed_source / "src/quant_data_kit/research_intake.py"
    changed_runtime = {
        **runtime,
        "script": str(changed_script),
        "script_sha256": file_hash(changed_script),
    }
    with pytest.raises(RuntimeError, match="读取期间|完整性"):
        read_intake_dataset(dataset, changed_runtime)


def test_prepare_freezes_reader_qdk_script_environment_and_context(
    tmp_path, monkeypatch
):
    _configure_notebook(tmp_path, monkeypatch)
    dataset = _intake_dataset(tmp_path)
    project = ProjectStore(tmp_path).save(
        "冻结研究", "固定输入能否复现", datasets=[dataset["id"]]
    )
    source = _fake_qdk_source(tmp_path)
    monkeypatch.setattr(
        "quant_studio.intake_tools.IntakeWorkspace.runtime",
        lambda self: (sys.executable, source),
    )
    store = NotebookStore(tmp_path)
    store.initialize(project["id"], template="price-return-missingness")
    record = store.prepare(project["id"], project["revision"])
    directory = store.directory(project["id"], record["id"])
    context = json.loads((directory / "inputs.json").read_text(encoding="utf-8"))
    runtime = context["intake_runtime"]
    assert runtime["script"] == "qdk_research_intake.py"
    assert runtime["reader"] == "notebook_data_reader.py"
    assert runtime["source_repo"] == str(source.resolve())
    assert {
        "qdk_research_intake.py",
        "notebook_data_reader.py",
        "qdk-environment.json",
    } <= record["hashes"].keys()
    assert context["datasets"][0]["path"] == f"inputs/{dataset['id']}"
    assert context["datasets"][0]["intake"] == dataset["intake"]
    (directory / "qdk_research_intake.py").write_text("changed", encoding="utf-8")
    with pytest.raises(QuantStudioError, match="完整性"):
        store.verify_inputs(record)


def test_registered_web_default_scope_initializes_and_runs_template(
    tmp_path, monkeypatch
):
    notebook_python = os.environ.get("QUANT_TEST_NOTEBOOK_PYTHON")
    _configure_notebook(tmp_path, monkeypatch, notebook_python)
    version = "2" * 64
    purpose = "daily_bars_research"
    columns = ["symbol", "date", "close"]
    check = _check(version, purpose, columns)
    workspace = IntakeWorkspace(tmp_path)
    snapshot = workspace.root / "catalog" / "versions" / version
    snapshot.mkdir(parents=True)
    (snapshot / "manifest.json").write_text(
        json.dumps({"id": version, "name": "bars"}), encoding="utf-8"
    )
    (snapshot / "check.json").write_text(json.dumps(check), encoding="utf-8")
    (snapshot / "rows.json").write_text(
        json.dumps(
            [
                {"symbol": "000001", "date": "2026-01-02", "close": 10.0},
                {"symbol": "000001", "date": "2026-01-05", "close": 11.0},
            ]
        ),
        encoding="utf-8",
    )

    def backend(self, action, arguments=(), **kwargs):
        if action == "check":
            return {
                "schema_version": "qdk.research-intake-response/v1",
                "ok": True,
                "action": action,
                "data": check,
            }
        assert action == "show"
        return {
            "schema_version": "qdk.research-intake-response/v1",
            "ok": True,
            "action": action,
            "data": {
                "version": {"id": version},
                "contract": {"metadata": {"provider": "fixture"}},
            },
        }

    monkeypatch.setattr(IntakeWorkspace, "backend", backend)
    record = workspace.register(
        "bars",
        version,
        purpose=purpose,
        scope={"columns": [], "symbols": [], "start": "", "end": ""},
    )
    assert record["intake"]["scope"] == {}

    source = _fake_qdk_source(tmp_path)
    monkeypatch.setattr(
        IntakeWorkspace,
        "runtime",
        lambda self: (sys.executable, source),
    )
    project = ProjectStore(tmp_path).save(
        "网页默认范围", "全量日线的收益是否受缺失驱动", datasets=[record["id"]]
    )
    store = NotebookStore(tmp_path)
    store.initialize(project["id"], template="price-return-missingness")
    prepared = store.prepare(project["id"], project["revision"])
    context = json.loads(
        (store.directory(project["id"], prepared["id"]) / "inputs.json").read_text(
            encoding="utf-8"
        )
    )
    assert context["datasets"][0]["intake"]["scope"] == {}
    if notebook_python:
        outcome = store.execute(
            {"project_id": project["id"], "id": prepared["id"]}, None
        )
        assert outcome["status"] == "succeeded"


@pytest.mark.skipif(
    not os.environ.get("QUANT_TEST_NOTEBOOK_PYTHON"),
    reason="independent notebook environment",
)
def test_price_template_runs_in_independent_notebook_environment(tmp_path, monkeypatch):
    _configure_notebook(tmp_path, monkeypatch, os.environ["QUANT_TEST_NOTEBOOK_PYTHON"])
    dataset = _intake_dataset(tmp_path)
    project = ProjectStore(tmp_path).save(
        "实际收益研究", "收益是否被价格缺失驱动", datasets=[dataset["id"]]
    )
    source = _fake_qdk_source(tmp_path)
    monkeypatch.setattr(
        "quant_studio.intake_tools.IntakeWorkspace.runtime",
        lambda self: (sys.executable, source),
    )
    store = NotebookStore(tmp_path)
    store.initialize(project["id"], template="price-return-missingness")
    record = store.prepare(project["id"], project["revision"])
    outcome = store.execute({"project_id": project["id"], "id": record["id"]}, None)
    assert outcome["status"] == "succeeded"
    directory = store.directory(project["id"], record["id"])
    report = (directory / "report.html").read_text(encoding="utf-8")
    assert "duplicate_symbol_date" in report
    assert "return_summary" in report


@pytest.mark.skipif(
    not _integration_value("QUANT_TEST_INTAKE_SOURCE", "QUANT_TEST_QDK_SOURCE")
    or not _integration_value("QUANT_TEST_INTAKE_PYTHON", "QUANT_TEST_QDK_PYTHON"),
    reason="independent intake runtime",
)
def test_real_intake_register_default_scope_and_notebook_run(tmp_path, monkeypatch):
    source_repo = Path(
        _integration_value("QUANT_TEST_INTAKE_SOURCE", "QUANT_TEST_QDK_SOURCE")
    )
    python = _integration_value("QUANT_TEST_INTAKE_PYTHON", "QUANT_TEST_QDK_PYTHON")
    import_python = (
        _integration_value(
            "QUANT_TEST_INTAKE_IMPORT_PYTHON", "QUANT_TEST_QDK_IMPORT_PYTHON"
        )
        or python
    )
    config_path = tmp_path / "intake-config.json"
    config_path.write_text(
        json.dumps({"python": import_python, "source": str(source_repo)}),
        encoding="utf-8",
    )
    monkeypatch.setenv("QUANT_STUDIO_INTAKE_CONFIG", str(config_path))
    source = tmp_path / "bars.csv"
    source.write_text(
        "ticker,trading_day,open_px,high_px,low_px,close_px\n"
        "000001,2026-01-02,9,11,8,10\n"
        "000001,2026-01-05,10,12,9,11\n",
        encoding="utf-8",
    )
    contract = {
        "schema_version": "qdk.research-intake-contract/v1",
        "kind": "daily_bars",
        "input": {"format": "csv", "encoding": "utf-8", "delimiter": ","},
        "mapping": {
            "symbol": "ticker",
            "date": "trading_day",
            "open": "open_px",
            "high": "high_px",
            "low": "low_px",
            "close": "close_px",
        },
        "types": {
            "symbol": "string",
            "date": "date",
            "open": "number",
            "high": "number",
            "low": "number",
            "close": "number",
        },
        "formats": {"date": "%Y-%m-%d"},
        "primary_key": ["symbol", "date"],
        "metadata": {
            "source": "fixture",
            "provider": "fixture",
            "timezone": "Asia/Shanghai",
            "adjustment": "raw",
            "units": {
                "open": "CNY",
                "high": "CNY",
                "low": "CNY",
                "close": "CNY",
            },
        },
        "limits": {"max_bytes": 1000000, "max_rows": 1000},
    }
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    workspace = IntakeWorkspace(tmp_path)
    imported = workspace.backend(
        "import",
        [
            "--source",
            source,
            "--name",
            "bars",
            "--contract",
            contract_path,
        ],
    )
    version = imported["data"]["version"]["id"]
    dataset = workspace.register(
        "bars",
        version,
        purpose="daily_bars_research",
        scope={"columns": [], "symbols": [], "start": "", "end": ""},
    )
    assert dataset["intake"]["scope"] == {}

    config_path.write_text(
        json.dumps({"python": python, "source": str(source_repo)}),
        encoding="utf-8",
    )
    notebook_python = os.environ.get("QUANT_TEST_NOTEBOOK_PYTHON", sys.executable)
    _configure_notebook(tmp_path, monkeypatch, notebook_python)
    project = ProjectStore(tmp_path).save(
        "真实固定日线", "收益是否受缺失和日期间隔驱动", datasets=[dataset["id"]]
    )
    store = NotebookStore(tmp_path)
    store.initialize(project["id"], template="price-return-missingness")
    prepared = store.prepare(project["id"], project["revision"])
    outcome = store.execute({"project_id": project["id"], "id": prepared["id"]}, None)
    assert outcome["status"] == "succeeded"
    directory = store.directory(project["id"], prepared["id"])
    context = json.loads((directory / "inputs.json").read_text(encoding="utf-8"))
    assert context["datasets"][0]["intake"] == dataset["intake"]
    assert context["intake_runtime"]["python"] == str(Path(python).resolve())
    report = (directory / "report.html").read_text(encoding="utf-8")
    assert "return_summary" in report
