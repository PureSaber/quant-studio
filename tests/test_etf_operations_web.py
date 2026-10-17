from copy import deepcopy

import pytest

from quant_studio import QuantStudioError, etf_daily, etf_daily_web
from quant_studio.etf_operations_web import LABELS, body
from quant_studio.settings import use_settings


def status(state="paused"):
    return {
        "data": {
            "covered_through": "2026-10-14",
            "captured_at": "2026-10-14T22:00:00+08:00",
            "fresh": False,
        },
        "checked_at": "2026-10-16T15:00:00+08:00",
        "latest": None,
        "schedule": {"slots": ["16:30", "17:00", "18:00", "21:00", "22:00"]},
        "execution": {"available": False},
        "legacy_latest": {"error": "old failure"},
        "operations": {
            "state": state,
            "scheduler": {
                "verified": True,
                "enabled": False,
                "name": "Quant-ETF-Daily",
            },
            "last_account_session": None,
            "next_attempt_at": None,
            "manual_action_required": True,
            "continuation": {"reason": "等待原生续跑核查"},
            "history": [],
            "acceptance": {"longest_consecutive": 0},
        },
    }


@pytest.mark.parametrize("state", list(LABELS))
def test_readable_states_and_no_unsafe_legacy_buttons(state):
    html = body(status(state), "csrf", None)
    assert LABELS[state] in html
    for question in [
        "今天完成了吗",
        "数据更新到了哪一天",
        "账户最后成功",
        "接下来什么时候",
        "现在需要我",
    ]:
        assert question in html
    assert 'value="refresh"' not in html and 'value="observe"' not in html
    assert "当前没有已核实的后续自动运行安排" in html
    assert "old failure" in html


def test_existing_research_connection_and_native_costs_remain_available():
    value = status()
    value["research_input"] = {
        "id": "a" * 32,
        "research_recipe": "frozen",
        "data": {"covered_through": "2026-10-16"},
    }
    value["execution"] = {
        "available": True,
        "tables": {},
        "cost_assumptions": {"commission": 0.001},
        "metrics": {"equity": 1000000},
    }
    value["schedule"]["missed_slots"] = ["16:30"]
    html = body(value, "csrf", None)
    assert 'value="connect-project"' in html
    assert "1000000" in html and "commission" in html
    assert "今天已错过的窗口" in html


def test_error_text_is_escaped_and_never_enables_action_without_preview():
    value = status("needs_attention")
    value["latest"] = {
        "id": "a" * 32,
        "reason_code": "historical_fill_blocked",
        "error": "<script>alert(1)</script>",
    }
    html = body(value, "csrf", None)
    assert "缺少过去开盘前" in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert 'value="recovery-preview"' in html
    assert 'value="recovery-confirm"' not in html


def test_new_binding_fails_closed_when_runtime_absent(tmp_path):
    path = tmp_path / "ops.json"
    path.write_text("{}")
    profile = {
        "schema_version": "quant-studio.settings/v1",
        "environment": {"QUANT_STUDIO_ETF_OPERATIONS": str(path)},
        "python_by_repo": {},
    }
    with use_settings(profile), pytest.raises(QuantStudioError, match="指定环境"):
        etf_daily.configuration()


def test_recovery_requires_preview_and_confirm(monkeypatch, tmp_path):
    from types import SimpleNamespace

    calls = []
    handler = SimpleNamespace(runs_root=tmp_path, _redirect=calls.append)
    monkeypatch.setattr(etf_daily, "preview_recovery", lambda root, operation: "b" * 32)
    etf_daily_web.today_post(
        handler, {"command": ["recovery-preview"], "operation": ["a" * 32]}
    )
    assert calls == ["/today?recovery=" + "b" * 32]
    with pytest.raises(QuantStudioError, match="明确确认"):
        etf_daily_web.today_post(
            handler, {"command": ["recovery-confirm"], "preview": ["b" * 32]}
        )


def test_main_page_dispatches_operations_view(monkeypatch):
    value = status()
    monkeypatch.setattr(etf_daily_web, "inspect_daily", lambda: deepcopy(value))
    html = etf_daily_web.today_body("csrf")
    assert "自动运行已停用" in html and "0 / 6" in html
