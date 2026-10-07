import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

from quant_studio import QuantStudioError
from quant_studio.__main__ import main
from quant_studio.runner import preview, run
from quant_studio.server import (
    _Handler,
    compiled_from_form,
    render_backtest,
    render_data,
    render_home,
    render_results,
    render_run,
    render_template_page,
    resolve_run_asset,
    validate_host,
)


def _start_http_server(tmp_path):
    class Handler(_Handler):
        pass

    Handler.runs_root = tmp_path
    Handler.csrf_token = "test-only-csrf-token"
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _request(server, method, path, *, host, origin=None, fields=None):
    body = urlencode(fields or {})
    headers = [("Host", host)]
    if method == "POST":
        headers.extend(
            [
                ("Content-Type", "application/x-www-form-urlencoded"),
                ("Content-Length", str(len(body.encode("utf-8")))),
            ]
        )
    if origin is not None:
        headers.append(("Origin", origin))
    return _raw_request(
        server, method, path, headers=headers, body=body if method == "POST" else ""
    )


def _raw_request(server, method, path, *, headers, body=""):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for name, value in headers:
        connection.putheader(name, value)
    connection.endheaders(body.encode("utf-8"))
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    return response.status, dict(response.getheaders()), payload


def test_home_has_ten_research_cards_and_one_synthetic_card():
    page = render_home()

    assert page.count('class="template-card research"') == 10
    assert "指数与风格择时研究" in page
    assert page.count('class="template-card synthetic"') == 1
    assert "A 股四类因子研究" in page
    assert "港股日频研究" in page
    assert "模拟盘步进研究" in page
    assert "基金与FOF研究" in page
    assert "美股与ETF研究" in page
    assert "合成净值样例" in page


def test_template_page_contains_knob_form():
    page = render_template_page("hk-equity-daily")

    assert "<form" in page
    assert 'name="initial_cash"' in page
    assert 'name="rebalance_sessions"' in page


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10"])
def test_non_loopback_host_is_rejected(host):
    with pytest.raises(QuantStudioError, match="host"):
        validate_host(host)


def test_http_rejects_dns_rebinding_host(tmp_path):
    server, thread = _start_http_server(tmp_path)
    try:
        status, _, body = _request(
            server,
            "GET",
            "/templates/synthetic-demo",
            host=f"attacker.example:{server.server_port}",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == 403
    assert b"test-only-csrf-token" not in body


def test_http_execute_requires_same_origin_and_csrf_token(tmp_path):
    server, thread = _start_http_server(tmp_path)
    authority = f"localhost:{server.server_port}"
    origin = f"http://{authority}"
    try:
        status, headers, page = _request(
            server, "GET", "/templates/synthetic-demo", host=authority
        )
        assert status == 200
        assert b"test-only-csrf-token" in page
        assert headers["Content-Security-Policy"] == "frame-ancestors 'none'"
        assert headers["X-Frame-Options"] == "DENY"

        status, _, _ = _request(
            server,
            "POST",
            "/templates/synthetic-demo",
            host=authority,
            origin="http://attacker.example",
            fields={"_csrf_token": "test-only-csrf-token", "action": "execute"},
        )
        assert status == 403
        assert not list(tmp_path.iterdir())

        status, _, _ = _request(
            server,
            "POST",
            "/templates/synthetic-demo",
            host=authority,
            origin=origin,
            fields={"action": "execute"},
        )
        assert status == 403
        assert not list(tmp_path.iterdir())

        status, code_headers, compiled = _request(
            server,
            "POST",
            "/templates/synthetic-demo/code",
            host=authority,
            origin=origin,
            fields={"_csrf_token": "test-only-csrf-token"},
        )
        assert status == 200
        assert b"initial_capital: 10000" in compiled
        assert code_headers["X-Frame-Options"] == "DENY"
        assert not list(tmp_path.iterdir())

        status, headers, _ = _request(
            server,
            "POST",
            "/templates/synthetic-demo",
            host=authority,
            origin=origin,
            fields={"_csrf_token": "test-only-csrf-token", "action": "execute"},
        )
        assert status == 303
        assert headers["Location"].startswith("/runs/")
        assert headers["X-Frame-Options"] == "DENY"
        assert len(list(tmp_path.iterdir())) == 1

        status, report_headers, _ = _request(
            server,
            "GET",
            f"{headers['Location']}/files/report.html",
            host=authority,
        )
        assert status == 200
        assert report_headers["Content-Security-Policy"] == "frame-ancestors 'self'"
        assert report_headers["X-Frame-Options"] == "SAMEORIGIN"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    ("headers", "body", "expected"),
    [
        (
            [
                ("Host", "{authority}"),
                ("Content-Type", "application/x-www-form-urlencoded"),
            ],
            "_csrf_token=test-only-csrf-token&action=execute",
            403,
        ),
        (
            [
                ("Host", "{authority}"),
                ("Origin", "{origin}"),
                ("Origin", "{origin}"),
                ("Content-Type", "application/x-www-form-urlencoded"),
            ],
            "_csrf_token=test-only-csrf-token&action=execute",
            403,
        ),
        (
            [
                ("Host", "{authority}"),
                ("Host", "{authority}"),
                ("Origin", "{origin}"),
                ("Content-Type", "application/x-www-form-urlencoded"),
            ],
            "_csrf_token=test-only-csrf-token&action=execute",
            403,
        ),
        (
            [
                ("Host", "{authority}"),
                ("Origin", "{origin}"),
                ("Content-Type", "application/x-www-form-urlencoded"),
            ],
            "_csrf_token=wrong&action=execute",
            403,
        ),
        (
            [
                ("Host", "{authority}"),
                ("Origin", "{origin}"),
                ("Content-Type", "application/x-www-form-urlencoded"),
            ],
            "_csrf_token=%E4%BB%A4%E7%89%8C&action=execute",
            403,
        ),
        (
            [
                ("Host", "{authority}"),
                ("Origin", "{origin}"),
                ("Content-Type", "application/x-www-form-urlencoded"),
            ],
            "_csrf_token=test-only-csrf-token&_csrf_token=duplicate&action=execute",
            403,
        ),
        (
            [
                ("Host", "{authority}"),
                ("Origin", "{origin}"),
                ("Content-Type", "application/x-www-form-urlencoded"),
                ("Content-Length", "not-a-number"),
            ],
            "_csrf_token=test-only-csrf-token&action=execute",
            400,
        ),
    ],
)
def test_http_rejects_ambiguous_or_malformed_action_requests(
    tmp_path, headers, body, expected
):
    server, thread = _start_http_server(tmp_path)
    authority = f"localhost:{server.server_port}"
    origin = f"http://{authority}"
    prepared = [
        (name, value.format(authority=authority, origin=origin))
        for name, value in headers
    ]
    if not any(name.lower() == "content-length" for name, _ in prepared):
        prepared.append(("Content-Length", str(len(body.encode("utf-8")))))
    try:
        status, response_headers, _ = _raw_request(
            server,
            "POST",
            "/templates/synthetic-demo",
            headers=prepared,
            body=body,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert status == expected
    assert response_headers["X-Frame-Options"] == "DENY"
    assert not list(tmp_path.iterdir())


def test_preview_page_shows_argv_without_nav_chart(tmp_path):
    result = preview(
        "a-share-four-factor",
        {"symbols_limit": 20},
        runs_root=tmp_path,
    )

    page = render_run(result.run_dir)

    assert "a_share_multifactor.backtest" in page
    assert "--symbols-limit" in page
    assert "生成的配置" in page
    assert "<svg" not in page
    assert "<iframe" not in page


def test_synthetic_result_embeds_report_and_shows_disclaimer(tmp_path):
    result = run("synthetic-demo", {}, execute=True, runs_root=tmp_path)

    page = render_run(result.run_dir)

    assert "合成样例，不是市场收益" in page
    assert f"/runs/{result.run_id}/files/report.html" in page
    assert (
        f'<a href="/runs/{result.run_id}/files/report.html" '
        'target="_blank" rel="noopener">独立打开完整报告</a>'
    ) in page


def test_legacy_hk_results_use_frozen_opening_and_original_report_without_rewriting(
    tmp_path,
):
    result = preview("hk-equity-daily", runs_root=tmp_path)
    directory = result.run_dir
    output = directory / "strategy-output"
    output.mkdir()
    (output / "report.html").write_text('<a href="summary.json">evidence</a>')
    (directory / "report.html").write_text("old copied report")
    (directory / "nav.csv").write_text(
        "date,nav\n2026-09-28,500000\n2026-09-29,1010000\n"
    )
    payload = result.as_json()
    payload.update(status="succeeded", report="report.html")
    (directory / "result.json").write_text(json.dumps(payload), encoding="utf-8")
    before = {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    page = render_run(directory)
    assert "+1.00%" in page and "50.00%" in page
    assert f"/runs/{result.run_id}/files/strategy-output/report.html" in page
    assert (
        f'<a href="/runs/{result.run_id}/files/strategy-output/report.html" '
        'target="_blank" rel="noopener">独立打开完整报告</a>'
    ) in page
    assert before == {p: p.read_bytes() for p in directory.rglob("*") if p.is_file()}


def test_only_run_html_csv_and_upstream_json_evidence_can_be_served(tmp_path):
    run_dir = tmp_path / "safe-run"
    run_dir.mkdir()
    (run_dir / "report.html").write_text("ok", encoding="utf-8")
    (run_dir / "result.json").write_text(json.dumps({}), encoding="utf-8")
    evidence = run_dir / "strategy-output" / "holdout" / "ledger.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("{}", encoding="utf-8")

    assert resolve_run_asset(tmp_path, "safe-run", "report.html").is_file()
    assert (
        resolve_run_asset(tmp_path, "safe-run", "strategy-output/holdout/ledger.json")
        == evidence
    )
    with pytest.raises(QuantStudioError):
        resolve_run_asset(tmp_path, "safe-run", "../outside.html")
    with pytest.raises(QuantStudioError):
        resolve_run_asset(tmp_path, "safe-run", "result.json")


def test_cli_preview_parses_declared_knob_types(monkeypatch, capsys, tmp_path):
    captured = {}

    def fake_run(template_id, knobs, **kwargs):
        captured.update(template_id=template_id, knobs=knobs, kwargs=kwargs)
        return preview(template_id, knobs, runs_root=tmp_path)

    monkeypatch.setattr("quant_studio.__main__.run", fake_run)

    assert (
        main(
            [
                "preview",
                "a-share-four-factor",
                "--set",
                "symbols_limit=8",
                "--set",
                "rebalance_freq=weekly",
            ]
        )
        == 0
    )
    assert captured["knobs"] == {"symbols_limit": 8, "rebalance_freq": "weekly"}
    assert captured["kwargs"]["execute"] is False
    assert '"status": "previewed"' in capsys.readouterr().out


def test_missing_workspace_is_preview_only(monkeypatch):
    monkeypatch.delenv("QUANT_WORKSPACE_ROOT", raising=False)

    home = render_home()
    form = render_template_page("a-share-four-factor")

    assert "可运行" in home
    assert "a-share-multifactor 只能预览" in home
    assert 'value="execute" disabled' in form


def test_debug_preset_fills_declared_fields(monkeypatch):
    monkeypatch.delenv("QUANT_WORKSPACE_ROOT", raising=False)

    page = render_template_page("a-share-four-factor", "debug")

    assert "调试" in page
    assert 'value="weekly" selected' in page
    assert 'name="symbols_limit" value="10"' in page
    assert 'name="initial_capital" value="10000"' in page


def test_imported_nav_is_charted_with_drawdown(tmp_path, monkeypatch):
    from tests.test_runner import test_current_run_outputs_are_isolated_and_collected

    test_current_run_outputs_are_isolated_and_collected(tmp_path, monkeypatch)
    run_dir = next((tmp_path / "runs").iterdir())
    page = render_run(run_dir)

    assert "<svg" in page
    assert "最大回撤" in page
    assert "期末净值" in page
    assert "+0.09%" in page
    assert "持仓" in page
    assert "回撤" in page
    assert "未提供基准净值" in page


def test_data_page_sees_local_snapshot_and_fetch_command(monkeypatch, tmp_path):
    folder = tmp_path / "a-share-multifactor" / "data"
    folder.mkdir(parents=True)
    (folder / "prices.csv").write_text("date,close\n", encoding="utf-8")
    monkeypatch.setenv("QUANT_WORKSPACE_ROOT", str(tmp_path))

    page = render_data()

    assert "1 个数据文件" in page
    assert "最近文件修改" in page
    assert "不代表行情截止日" in page
    assert 'href="/environment"' in page
    assert "a_share_multifactor.fetch_data" in page


def test_template_flows_compile_declared_config():
    share = render_template_page("a-share-four-factor", "debug")
    hong_kong = render_template_page("hk-equity-daily")
    paper = render_template_page("paper-sim")
    synthetic = render_template_page("synthetic-demo")

    assert 'id="factors"' in share
    assert "rebalance_freq: weekly" in share
    assert "--symbols-limit\n10" in share
    assert 'id="factors"' not in hong_kong
    assert "quant-hk" in hong_kong
    assert "quant-paper" in paper
    assert "固定收益率" in synthetic
    assert "进程内合成样例，无外部命令" in synthetic
    for page in (share, hong_kong, paper, synthetic):
        assert 'id="data"' in page
        assert 'id="trade"' in page
        assert 'id="run"' in page
        assert 'id="code"' in page
        assert "手写代码" in page


def test_hk_compiled_form_preserves_selected_snapshot_without_running(monkeypatch):
    def unexpected_run(*args, **kwargs):
        raise AssertionError("editing the code preview must not run a template")

    monkeypatch.setattr("quant_studio.server.run", unexpected_run)
    snapshot = "C:/research snapshots/hong kong"
    text = compiled_from_form("hk-equity-daily", {"snapshot": [snapshot]})

    assert f"--snapshot\n{snapshot}\n" in text
    assert "<本次运行>/snapshot" not in text


def test_compiled_form_keeps_only_submitted_factors():
    text = compiled_from_form(
        "a-share-four-factor",
        {
            "factor_form": ["1"],
            "factor": ["pe_ratio"],
            "rebalance_freq": ["weekly"],
            "initial_capital": ["20000"],
            "symbols_limit": ["12"],
            "commission": ["0.0003"],
            "slippage": ["0.001"],
        },
    )

    assert "pe_ratio" in text
    assert "momentum_20d" not in text
    assert "--symbols-limit\n12" in text
    assert "<本次运行>/config.yaml" in text


def test_backtest_lists_previews_and_results_keep_finished_runs(tmp_path):
    previewed = preview(
        "a-share-four-factor",
        {"symbols_limit": 20},
        runs_root=tmp_path,
    )
    finished = run("synthetic-demo", {}, execute=True, runs_root=tmp_path)

    backtest = render_backtest(tmp_path)
    results = render_results(tmp_path)
    detail = render_run(previewed.run_dir)
    assert "已完成" in render_run(finished.run_dir)

    assert backtest.count("synthetic-demo") == 1
    assert "a-share-four-factor" in backtest
    assert "仅预览" in backtest
    assert "已完成" in results
    assert "a-share-four-factor" not in results
    assert "symbols_limit=20" in detail
    assert "参数：模板默认" in render_run(
        preview("synthetic-demo", {}, runs_root=tmp_path).run_dir
    )
