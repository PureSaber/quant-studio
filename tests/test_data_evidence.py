import json

from quant_studio.data_evidence import evidence_from_preflight, latest_availability
from quant_studio.templates import Template, load_template


def _write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_only_declared_native_preflight_fields_become_availability_evidence(tmp_path):
    original = load_template("fund-fof")
    template = Template(
        dict(original.metadata), original.base_config, original.directory
    )
    template.metadata["availability_contract"] = {
        "observed_through": "coverage.end",
        "available_at": "coverage.available_at",
        "missing": "missing_inputs",
        "blockers": "blocking_reasons",
    }
    request = {"snapshot": str(tmp_path / "fund-data")}
    evidence = {
        "software_preflight": "pass",
        "read_only": True,
        "coverage": {
            "end": "2026-09-30",
            "available_at": "2026-10-08T06:00:00+08:00",
        },
        "missing_inputs": [],
        "blocking_reasons": [],
        "as_of": "2099-12-31",
    }

    record = evidence_from_preflight(template, request, {"status": "checked"}, evidence)

    assert record.source == str(tmp_path / "fund-data")
    assert record.observed_through == "2026-09-30"
    assert record.available_at == "2026-10-08T06:00:00+08:00"
    assert record.missing == () and record.blockers == ()
    assert "2099" not in repr(record)


def test_bad_or_undeclared_manifest_cannot_claim_market_cutoff(tmp_path):
    run = tmp_path / "one"
    run.mkdir()
    _write_json(
        run / "request.json", {"template_id": "fund-fof", "snapshot": "D:/fund"}
    )
    _write_json(run / "result.json", {"status": "checked", "argv": []})
    _write_json(
        run / "preflight.json",
        {
            "software_preflight": "pass",
            "read_only": True,
            "as_of": "2099-12-31",
            "available_at": "2099-12-31T00:00:00Z",
        },
    )
    _write_json(run / "manifest.json", {"observed_through": "2099-12-31"})

    records = latest_availability(tmp_path)

    fund = next(item for item in records if item.template_id == "fund-fof")
    assert fund.source == "D:/fund"
    assert fund.observed_through == "unknown"
    assert fund.available_at == "unknown"
    assert "manifest" not in fund.authority


def test_failed_check_preserves_failure_reason_without_inventing_dates(tmp_path):
    run = tmp_path / "failed"
    run.mkdir()
    _write_json(
        run / "request.json", {"template_id": "fund-fof", "snapshot": "D:/fund"}
    )
    _write_json(
        run / "result.json",
        {"status": "check_failed", "argv": [], "message": "缺少 calendar.csv"},
    )
    (run / "stderr.txt").write_text("calendar.csv missing", encoding="utf-8")

    fund = next(
        item for item in latest_availability(tmp_path) if item.template_id == "fund-fof"
    )

    assert fund.observed_through == "unknown" and fund.available_at == "unknown"
    assert fund.blockers == ("缺少 calendar.csv",)
    assert fund.status == "blocked"
