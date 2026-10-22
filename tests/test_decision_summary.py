import hashlib
import json

import pytest

from quant_studio.decision_summary import evidence_summary, gate_a_summary


@pytest.mark.parametrize("provisional", [True, False])
def test_native_point_estimate_never_grants_research_acceptance(tmp_path, provisional):
    path = tmp_path / "strategy-output/family/base/result.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "quant.research-result/v1",
                "candidate": "base",
                "provisional": provisional,
                "objective_evaluation": {"status": "meets_objective"},
            }
        )
    )
    record = {
        "status": "succeeded",
        "output_hashes": {
            path.relative_to(tmp_path).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        },
    }
    (tmp_path / "result.json").write_text(json.dumps(record))
    labels, objectives = evidence_summary(tmp_path)
    assert objectives == ["base：目标点估计满足"]
    assert ("provisional" in labels[0]) == provisional
    assert "不计入" in labels[0] if provisional else "另行评审" in labels[0]
    path.write_text(path.read_text() + " ")
    assert evidence_summary(tmp_path) == (["证据未核验；不计入验收"], [])


def test_unverified_success_does_not_become_validated_evidence(tmp_path):
    (tmp_path / "result.json").write_text(json.dumps({"status": "succeeded"}))
    assert evidence_summary(tmp_path) == (["证据未核验；不计入验收"], [])


def test_gate_a_is_bound_to_account_definition_and_native_counter():
    account = {"account_id": "old", "definition_sha256": "frozen"}
    native = {
        "account": dict(account),
        "checked_at": "2026-10-22T12:00:00+08:00",
        "operations": {"acceptance": {"longest_consecutive": 0}},
    }
    assert "0/6" in gate_a_summary(account, native)
    native["operations"]["acceptance"]["longest_consecutive"] = True
    assert "未核实" in gate_a_summary(account, native)
    native["account"]["definition_sha256"] = "replacement"
    assert "不一致" in gate_a_summary(account, native)
    assert "未核实" in gate_a_summary(account, None)
