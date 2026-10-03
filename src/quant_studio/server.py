from __future__ import annotations

import json
import math
import os
import secrets
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse, urlsplit

import yaml

from quant_studio import QuantStudioError
from quant_studio.accounts import account_sources, inspect_source
from quant_studio.desk import (
    FETCH_BY_REPO,
    FETCH_COMMANDS,
    html_table,
    list_runs,
    scan_datasets,
    workspace_root,
)
from quant_studio.flow import compile_document, flow_steps
from quant_studio.nav import chart_fragment, drawdown_fragment, parse_nav_csv
from quant_studio.research_panel import load_timing_view, timing_panel
from quant_studio.runner import preflight, run, safe_run_file, template_readiness
from quant_studio.templates import load_template, template_ids


def validate_host(host: str) -> str:
    if host not in {"127.0.0.1", "localhost"}:
        raise QuantStudioError(f"host 必须是 127.0.0.1 或 localhost: {host}")
    return host


def render_home() -> str:
    cards = []
    for template_id in template_ids():
        template = load_template(template_id)
        card_kind = "synthetic" if template.kind == "synthetic" else "research"
        label = "合成样例" if card_kind == "synthetic" else "研究模板"
        ready = template_readiness(template)
        state = "ready" if ready.runnable else "preview-only"
        trail = " · ".join(title for _, title in flow_steps(template))
        cards.append(
            f"""<article class="template-card {card_kind}">
<span class="eyebrow">{label}</span>
<h2>{escape(template.title)}</h2>
<p>{escape(template.summary)}</p>
<p class="trail">{escape(trail)}</p>
<p class="badge {state}">{escape(ready.message)}</p>
<a class="open" href="/templates/{escape(template.id)}">打开积木</a>
</article>"""
        )
    body = f"""<header class="page-head">
<p class="kicker">模板库</p>
<h1>选择一个研究模板</h1>
<p class="lede">每个模板是一组固定积木。只改允许的参数，旁边显示将要生成的配置和命令。
研究模板确认后才执行；合成样例会画出净值，并标明不是市场收益。</p>
</header>
<main class="card-grid">{"".join(cards)}</main>"""
    return _layout("模板库", body, "strategy")


def render_overview(runs_root: Path) -> str:
    records = list_runs(runs_root)
    results = [r for r in records if r["has_nav"] == "1" or r["has_report"] == "1"]
    datasets = scan_datasets()
    templates = [load_template(key) for key in template_ids()]
    ready = [(template, template_readiness(template)) for template in templates]
    configured = sum(state.runnable for _, state in ready)
    account_error = ""
    try:
        accounts = account_sources()
    except QuantStudioError as exc:
        accounts = []
        account_error = f'<p class="banner">{escape(str(exc))}</p>'
    cards = []
    for label, value, description, href in (
        (
            "环境",
            f"{configured}/{len(templates)}",
            "模板环境就绪；数据另行核验",
            "/environment",
        ),
        (
            "本地数据",
            str(len(datasets)),
            "已发现的数据目录；文件时间不等于行情截止",
            "/data",
        ),
        (
            "研究记录",
            str(len(records)),
            "最近最多40条预览、运行及失败记录",
            "/backtest",
        ),
        (
            "前向账户",
            str(len(accounts)),
            "已配置入口；打开后只读核验保存证据",
            "/accounts",
        ),
        ("结果与报告", str(len(results)), "最近记录中带净值或报告的结果", "/results"),
    ):
        cards.append(
            f'<a class="overview-card" href="{href}"><span>{label}</span>'
            f"<strong>{value}</strong><small>{description}</small></a>"
        )
    blockers = "".join(
        f'<li><a href="/templates/{escape(template.id)}">{escape(template.title)}</a>'
        f'：{escape(state.message)}。<a href="/environment">查看环境配置</a></li>'
        for template, state in ready
        if not state.runnable
    )
    account_links = "".join(
        f'<a class="chip" href="/accounts/{escape(source.id)}">'
        f"{escape(source.label)}</a>"
        for source in accounts
    )
    body = f"""<header class="page-head"><p class="kicker">总览</p>
<h1>研究工作台</h1>
<p class="lede">从数据到研究结果，再查看已登记账户的前向观察。</p></header>
<section class="overview-grid">{"".join(cards)}</section>
{account_error}
<section class="panel"><h2>开始一项研究</h2>
<ol class="onboarding"><li><a href="/environment">检查运行环境</a>：
确认每个应用使用的Python。</li>
<li><a href="/data">查看本地数据</a>：核对输入来源，再进入模板。</li>
<li><a href="/strategy">选择模板</a>：调整参数、检查配置，显式运行。</li>
<li><a href="/results">查看结果</a>：核对净值、回撤、持仓和原始报告。</li></ol>
<a class="chip" href="/templates/synthetic-demo">先用合成样例熟悉流程</a>
<p class="note">合成样例仅验证软件。环境就绪不代表数据完整或策略有效。</p></section>
<section class="panel"><h2>需要处理</h2>
<ul>{blockers or "<li>环境检查通过；请继续核验数据和研究条件。</li>"}</ul></section>
<section class="panel"><h2>最近研究</h2><table>
<tr><th>模板</th><th>状态</th><th>时间</th><th>净值</th></tr>
{_run_rows(records[:6], "/runs/") or '<tr><td colspan="4">还没有研究记录</td></tr>'}
</table></section>
<section class="panel"><h2>前向账户</h2>
{account_links or '<p>尚未连接账户。<a href="/accounts">查看连接方法</a></p>'}
<p class="note">这里只读取明确配置的账户，不登记、观察或封存账户。</p></section>"""
    return _layout("总览", body, "overview")


def render_accounts(account_id: str | None = None) -> str:
    body = (
        '<header class="page-head"><p class="kicker">账户</p>'
        "<h1>前向观察账户</h1></header>"
    )
    try:
        sources = account_sources()
        if account_id is None:
            rows = "".join(
                f'<tr><td><a href="/accounts/{escape(source.id)}">'
                f"{escape(source.label)}</a></td><td>{escape(str(source.path))}</td>"
                "<td>打开后核验</td></tr>"
                for source in sources
            )
            body += (
                '<section class="panel"><p>打开账户时重新核验登记及已完成观测的产物。'
                "未连接账户不会自动扫描或创建。</p><table>"
                "<tr><th>账户</th><th>目录</th><th>状态</th></tr>"
                + (rows or '<tr><td colspan="3">尚未配置账户</td></tr>')
                + "</table></section>"
                '<section class="panel"><h2>连接已有账户</h2>'
                "<p>将QUANT_STUDIO_ACCOUNTS指向本地JSON配置。路径须为已有账户的绝对目录。</p>"
                '<pre>{"schema_version":"quant-studio.accounts/v1", "accounts":['
                '{"id":"etf-forward", "label":"ETF前向观察", '
                '"path":"账户绝对目录"}]}</pre>'
                "<p>核验使用QUANT_STUDIO_RUNTIMES中quant-pipeline对应的维护Python；"
                "未指定时使用Studio的Python。该环境须支持paper inspect命令。</p>"
                "<p>账户原执行环境保持冻结，核验不会改变其版本或登记。</p></section>"
            )
        else:
            source = next((s for s in sources if s.id == account_id), None)
            if source is None:
                raise QuantStudioError("账户未配置；请从账户列表选择已连接的账户。")
            result = inspect_source(source)
            body += _account_view(source.label, result)
    except (QuantStudioError, KeyError, TypeError, ValueError) as exc:
        body += (
            '<section class="panel"><h2>账户核验未完成</h2>'
            f"<pre>{escape(str(exc))}</pre>"
            '<a href="/accounts">返回账户配置</a></section>'
        )
    return _layout("账户", body, "accounts")


def _account_view(label: str, result: dict) -> str:
    states = {
        "pending": "尚未到观察起点",
        "awaiting_observation": "观察窗口内，尚无已完成观测",
        "observing": "观察中",
        "ended_unsealed": "观察窗口已结束，尚未封存",
        "sealed": "已有封存评价",
    }
    window = result["window"]
    latest = result.get("latest")
    metrics = '<p class="banner">尚无已完成观测，收益与回撤不可用。</p>'
    if latest is not None:
        values = latest["metrics"]
        cells = []
        for key, title, percentage in (
            ("total_return", "累计净收益", True),
            ("max_drawdown", "最大回撤", True),
            ("sessions", "交易日数", False),
        ):
            value = values.get(key)
            shown = "不可用"
            if isinstance(value, (int, float)) and math.isfinite(value):
                shown = f"{value:.2%}" if percentage else str(value)
            cells.append(
                f'<div class="overview-card"><span>{title}</span>'
                f"<strong>{shown}</strong></div>"
            )
        metrics = (
            f"<h3>最近观测：{escape(latest['as_of'])}</h3>"
            f'<div class="overview-grid">{"".join(cells)}</div>'
            '<p class="note">日常指标用于运行检查，不是终期评价。</p>'
            "<details><summary>原始指标与诊断</summary><pre>"
            + escape(json.dumps(latest, ensure_ascii=False, indent=2))
            + "</pre></details>"
        )
    attempts = "".join(
        f"<tr><td>{escape(str(row.get('as_of') or '未记录'))}</td>"
        f"<td>{escape(str(row['status']))}</td>"
        f"<td>{escape(str(row.get('error') or '—'))}</td></tr>"
        for row in result["attempts"]
    )
    evidence = {
        key: result.get(key)
        for key in (
            "definition_sha256",
            "created_at",
            "source_scope",
            "code_identity",
            "evaluation",
        )
    }
    return f"""<section class="panel"><h2>{escape(label)}</h2>
<p class="badge ready">已核验保存的登记与观测产物</p>
<p><b>{states[result["state"]]}</b> · {len(result["observations"])}次已完成观测</p>
<p>账户：{escape(result["account_id"])} ·
候选：{escape(str(result.get("candidate", "")))}</p>
<p>登记窗口：{escape(window["start"])}至{escape(window["end"])}</p>
<p class="note">核验时刻：{escape(str(result.get("checked_at", "")))}</p>
{metrics}</section>
<section class="panel"><h2>全部尝试</h2><table>
<tr><th>观测日期</th><th>状态</th><th>失败或中断原因</th></tr>
{attempts or '<tr><td colspan="3">没有运行尝试</td></tr>'}</table></section>
<details class="panel"><summary>冻结身份与核验证据</summary>
<pre>{escape(json.dumps(evidence, ensure_ascii=False, indent=2))}</pre>
<p>核验环境：{escape(result["inspection_python"])}</p></details>
<p class="note">此页不刷新输入或运行策略；
一致性核验不替代真实数据、观察完整性或投资有效性评价。</p>"""


def render_data() -> str:
    root = workspace_root()
    datasets = scan_datasets(root)
    cards = []
    for item in datasets:
        cards.append(
            f"""<article class="template-card research">
<span class="eyebrow">{escape(item.market)}</span>
<h2>{item.files} 个数据文件</h2>
<p>最近文件修改 {escape(item.newest)}（不代表行情截止日）</p>
<p>{escape(str(item.path))}</p>
</article>"""
        )
    if not cards:
        cards.append(
            """<article class="template-card research">
<span class="eyebrow">空状态</span>
<h2>还没有本地快照</h2>
<p>设置 QUANT_WORKSPACE_ROOT 后，数据页只读取已有目录，不会自动下载。</p>
</article>"""
        )
    commands = "".join(
        f"<li><b>{escape(name)}</b><pre>{escape(command)}</pre></li>"
        for name, command in FETCH_COMMANDS
    )
    checks = "".join(
        f'<li><a href="/templates/{escape(key)}">'
        f"{escape(load_template(key).title)}</a>："
        + (
            "可按所选参数运行上游数据预检"
            if load_template(key).metadata.get("preflight_argv")
            else "独立数据预检尚未接入；运行条件仍由上游校验"
        )
        + "</li>"
        for key in template_ids()
        if load_template(key).kind != "synthetic"
    )
    root_text = str(root) if root else "未设置"
    body = f"""<header class="page-head">
<p class="kicker">数据</p>
<h1>本机行情</h1>
<p class="lede">工作区：{escape(root_text)}</p>
</header>
<main class="card-grid">{"".join(cards)}</main>
<section class="panel"><h2>数据与配置预检</h2><ul>{checks}</ul>
<p class="note">进入模板设置参数后预检。
文件存在、环境就绪与业务数据通过分别展示。</p></section>
<section class="panel"><h2>拉取命令</h2>
<ul class="commands">{commands}</ul>
<p class="note"><a href="/environment">查看各模板是否可运行</a></p>
</section>"""
    return _layout("数据", body, "data")


def render_backtest(runs_root: Path) -> str:
    rows = _run_rows(list_runs(runs_root), "/runs/")
    body = f"""<header class="page-head">
<p class="kicker">回测</p>
<h1>运行任务</h1>
<p class="lede">从策略页提交。打开一条记录可看命令；失败时能看到输出。</p>
</header>
<section class="panel"><table>
<tr><th>模板</th><th>状态</th><th>时间</th><th>净值</th></tr>
{rows or "<tr><td colspan='4'>还没有运行</td></tr>"}
</table></section>"""
    return _layout("回测", body, "backtest")


def render_results(runs_root: Path) -> str:
    finished = [
        record
        for record in list_runs(runs_root)
        if record["has_nav"] == "1" or record["has_report"] == "1"
    ]
    rows = _run_rows(finished, "/runs/")
    body = f"""<header class="page-head">
<p class="kicker">结果</p>
<h1>研究记录</h1>
<p class="lede">这里只保留已经交出净值或报告的运行。
打开后可看曲线、回撤和持仓成交表。</p>
</header>
<section class="panel"><table>
<tr><th>模板</th><th>状态</th><th>时间</th><th>净值</th></tr>
{rows or "<tr><td colspan='4'>还没有带净值或报告的结果</td></tr>"}
</table></section>"""
    return _layout("结果", body, "results")


def _run_rows(records: list[dict[str, str]], prefix: str) -> str:
    lines = []
    for record in records:
        nav = "有" if record["has_nav"] == "1" else "无"
        status = _STATUS.get(record["status"], record["status"])
        lines.append(
            "<tr>"
            f'<td><a href="{prefix}{escape(record["run_id"])}">'
            f"{escape(record['template_id'])}</a></td>"
            f"<td>{escape(status)}</td>"
            f"<td>{escape(record['when'])}</td>"
            f"<td>{nav}</td></tr>"
        )
    return "".join(lines)


def render_template_page(
    template_id: str, preset_id: str | None = None, *, csrf_token: str = ""
) -> str:
    template = load_template(template_id)
    selected = _preset_values(template, preset_id)
    catalog = list(template.metadata.get("factor_catalog") or [])
    factors = [item["name"] for item in catalog] or None
    snapshot = (
        os.environ.get(template.metadata["input_source"]["environment"])
        if template.metadata.get("requires_snapshot")
        else None
    )
    document = compile_document(template, selected, factors, snapshot=snapshot)
    steps = []
    index = 1
    bodies = {
        "data": _data_step(template),
        "factors": _factor_fields(template),
        "trade": (
            f'<div class="fields">{"".join(_knob_fields(template, selected))}</div>'
        ),
        "run": _run_step(template),
    }
    summaries = {
        "data": "只读取已有输入，不在这里下载。",
        "factors": "股票池和因子实现都来自上游模板，页面不能新增因子。",
        "trade": "这些字段写入上游配置，或成为命令参数。",
        "run": "先生成配置和参数列表。确认后才执行。",
    }
    for key, title in flow_steps(template):
        if key == "code":
            continue
        steps.append(_step(index, key, title, summaries[key], bodies[key]))
        index += 1
    kind = "合成样例" if template.kind == "synthetic" else "研究模板"
    template_ref = escape(template.id)
    body = f"""<header class="page-head">
<p class="kicker">{kind}</p>
<h1>{escape(template.title)}</h1>
<p class="lede">{escape(template.summary)}</p>
<div class="presets">{_preset_links(template, preset_id)}</div>
</header>
<div class="studio-grid">
<form class="flow" method="post" action="/templates/{template_ref}"
 data-code="/templates/{template_ref}/code">
<input type="hidden" name="_csrf_token" value="{escape(csrf_token)}">
<ol class="steps">{"".join(steps)}</ol>
</form>
<aside class="code-card" id="code">
<p class="eyebrow">代码</p>
<h2>生成结果</h2>
<p class="step-copy">改参数后这里跟着更新。页面不执行手写代码。</p>
<pre id="compiled">{escape(document)}</pre>
</aside>
</div>
<script>{_CODE_SCRIPT}</script>"""
    return _layout(template.title, body, "strategy")


def render_run(run_dir: str | Path, *, csrf_token: str = "") -> str:
    directory = Path(run_dir)
    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    template = load_template(request["template_id"])
    timing_view = (
        load_timing_view(directory, result)
        if template.metadata.get("timing_view")
        else None
    )
    argv = " ".join(escape(str(item)) for item in result["argv"])
    report_html = ""
    if result.get("report"):
        report_name = result["report"]
        declared = (template.metadata.get("result_files") or {}).get("report.html")
        if report_name == "report.html" and declared:
            original = safe_run_file(
                directory, f"strategy-output/{declared}", {".html"}
            )
            if original.is_file():
                report_name = f"strategy-output/{declared}"
        report = safe_run_file(directory, report_name, {".html"})
        if report.is_file():
            report_html = (
                f'<iframe title="运行报告" src="/runs/{escape(directory.name)}'
                f'/files/{escape(report_name)}"></iframe>'
            )
    status = escape(str(result["status"]))
    chart = ""
    nav_path = directory / "nav.csv"
    series = None
    if nav_path.is_file() and not (
        template.metadata.get("timing_view") and result["status"] != "succeeded"
    ):
        opening_key = template.metadata.get("nav_initial_value_key")
        opening = template.metadata.get("nav_initial_value")
        if opening_key:
            config = yaml.safe_load(
                (directory / f"config.{template.config_format}").read_text(
                    encoding="utf-8"
                )
            )
            opening = config[opening_key]
        # Older runs lack the initial_nav CSV column; their frozen config still
        # establishes the basis. Reading it never rewrites historical evidence.
        series = parse_nav_csv(nav_path, initial_nav=opening)
        if series is not None:
            chart = chart_fragment(
                series,
                currency=template.metadata.get("nav_currency"),
                return_decimals=template.metadata.get("return_decimals", 2),
                title="策略净值 · 描述性全区间" if timing_view else "净值曲线",
            )
    notices = []
    if template.metadata.get("result_notice"):
        notices.append(
            f'<p class="note">{escape(template.metadata["result_notice"])}</p>'
        )
    if series is not None and template.metadata.get("standard_view"):
        notices.append(
            '<p class="note">事件级原生账本净值，时间为UTC；横轴按事件顺序等距排列。'
            f"共{len(series.rows)}次观测，期初资金{series.initial_nav}"
            f"{escape(template.metadata['nav_currency'])}。没有按日重采样。"
            "保证金、费用和成交均直接来自同一账本；以下表格预览前12行，"
            "可下载完整CSV。</p>"
        )
    if template.kind == "synthetic":
        notices.append('<p class="banner">合成样例，不是市场收益</p>')
    elif result.get("evidence_kind"):
        kind = str(result["evidence_kind"])
        label = {
            "synthetic": "合成数据，仅验证软件，不是市场收益",
            "retrospective": "回顾性历史数据，未获得历史PIT认证",
            "historical_pit": "上游声明历史时点数据，策略有效性仍需独立验证",
            "public_source": "公开来源数据，完整业务适用性尚未认证",
            "user_provided": "用户提供数据，来源与业务规则仍须核验",
            "unspecified": "来源性质未声明，不能据此判断为真实或合成数据",
        }.get(kind, kind)
        notices.append(f'<p class="banner">数据性质：{escape(label)}</p>')
    if result.get("message"):
        notices.append(f'<p class="banner">{escape(str(result["message"]))}</p>')
    elif result["status"] == "failed":
        stderr_path = directory / "stderr.txt"
        if stderr_path.is_file():
            detail = stderr_path.read_text(encoding="utf-8")[-800:]
            notices.append(f"<pre>{escape(detail)}</pre>")
    elif result["status"] == "succeeded" and not chart and not report_html:
        notices.append(
            '<p class="banner">命令已结束，但没有找到净值序列或 report.html</p>'
        )
    table_labels = dict(template.metadata.get("result_column_labels") or {})
    if timing_view and timing_view.get("return_attribution"):
        table_labels.update(slippage="模型基础成本", market_impact="模型冲击成本")
    tables = _artifact_tables(
        directory,
        native=bool(template.metadata.get("standard_view")),
        table_titles=template.metadata.get("result_table_titles"),
        column_labels=table_labels,
    )
    if template.metadata.get("timing_view") and result["status"] != "succeeded":
        tables = ""
    benchmark = '<p class="note">未提供基准净值。</p>' if chart else ""
    drawdown = ""
    if series is not None:
        drawdown = drawdown_fragment(
            series, return_decimals=template.metadata.get("return_decimals", 2)
        )
        if _has_benchmark(nav_path):
            benchmark = '<p class="note">净值文件包含基准列，可在原始数据中查看。</p>'
    benchmark_path = directory / "benchmark_nav.csv"
    if series is not None and benchmark_path.is_file():
        baseline = parse_nav_csv(benchmark_path)
        if baseline is not None and baseline.period_return is not None:
            benchmark = (
                '<p class="note">同区间基准：区间涨跌'
                f"{baseline.period_return * 100:+.2f}%，"
                f"最大回撤{baseline.max_drawdown * 100:.2f}%。"
                "完整比较见下方原始报告。</p>"
            )
            if timing_view:
                benchmark = (
                    '<p class="note">基准与策略使用相同日期和期初净值1，'
                    "不作为独立样本外结论。</p>"
                    + chart_fragment(baseline, title="基准净值 · 描述性全区间")
                )
    curves = chart + benchmark if timing_view else benchmark + chart
    evidence_open = "" if result["status"] == "succeeded" else " open"
    preflight_view = ""
    if result["status"] == "checked":
        checked = json.loads((directory / "preflight.json").read_text(encoding="utf-8"))
        fields = [("_csrf_token", csrf_token), ("action", "execute")]
        fields.extend(
            (str(key), str(value)) for key, value in request.get("knobs", {}).items()
        )
        if request.get("snapshot"):
            fields.append(("snapshot", request["snapshot"]))
        if request.get("factors") is not None:
            fields.append(("factor_form", "1"))
            fields.extend(("factor", factor) for factor in request["factors"])
        inputs = "".join(
            f'<input type="hidden" name="{escape(key)}" value="{escape(value)}">'
            for key, value in fields
        )
        corporate_actions = ""
        if "corporate_actions_complete" in checked:
            corporate_actions = (
                "公司行动证据："
                + (
                    "上游声明完整"
                    if checked["corporate_actions_complete"] is True
                    else "未确认完整"
                )
                + "；"
            )
        summary = template.metadata.get(
            "preflight_summary", "校验所选配置与已有快照的完整性。"
        )
        symbol_count = checked.get("symbols", "未提供")
        row_count = checked.get("rows", "未提供")
        if template.metadata.get("preflight_contract", {}).get(
            "instrument_ids_required"
        ):
            symbol_count = len(checked["instrument_ids"])
            row_count = checked["event_count"]
        preflight_view = (
            '<section class="panel"><h2>数据预检证据</h2>'
            f"<p>{escape(summary)}</p>"
            f"<p>标的数：{escape(str(symbol_count))} · "
            f"数据行数：{escape(str(row_count))}</p>"
            f"<p>{corporate_actions}投资适用性："
            + ("以上游证据为准" if checked.get("investable") is True else "尚未认证")
            + "</p><details><summary>上游原始证据</summary><pre>"
            + escape(json.dumps(checked, ensure_ascii=False, indent=2))
            + "</pre></details>"
            + f'<form method="post" action="/templates/{escape(template.id)}">'
            + inputs
            + '<button class="btn primary">使用相同参数运行</button></form>'
            '<p class="note">运行时上游会重新校验数据；预检不锁定输入。</p></section>'
        )
    elif result["status"] in {"check_failed", "blocked"}:
        next_step = (
            "核对初始资金等参数与上游样例版本；修正后返回模板重新预检。"
            "完整错误保留在运行记录。"
            if template.metadata.get("standard_view")
            else "核对输入路径、源文件及对应环境；保留失败记录，修正输入后重新检查。"
        )
        preflight_view = (
            '<section class="panel"><h2>下一步</h2>'
            f"<p>{next_step}</p>"
            f'<a href="/templates/{escape(template.id)}">返回模板</a></section>'
        )
    body = f"""<header class="page-head">
<p class="kicker">结果</p>
<h1>{escape(template.title)}</h1>
<p class="lede"><span class="status {status}">
{_STATUS.get(result["status"], status)}</span>
{_request_summary(request)}</p>
</header>
{"".join(notices)}
{preflight_view}
{timing_panel(directory, timing_view)}
{curves}
{drawdown}
{tables}
{report_html}
<details class="panel"{evidence_open}>
<summary>配置与执行命令</summary>
{_saved_config(directory)}
<section class="panel command">
<h2>参数列表</h2>
<pre>{argv or "进程内合成样例，无外部命令"}</pre>
</section>
</details>"""
    return _layout("运行结果", body, "results")


def render_environment() -> str:
    root = os.environ.get("QUANT_WORKSPACE_ROOT") or "未设置"
    rows = []
    for template_id in template_ids():
        template = load_template(template_id)
        ready = template_readiness(template)
        executable = ready.executable or ("内置样例" if ready.runnable else "未就绪")
        rows.append(
            "<tr>"
            f"<td>{escape(template.title)}</td>"
            f"<td>{escape(ready.message)}</td>"
            f"<td>{escape(executable)}</td>"
            "</tr>"
        )
    body = f"""<header class="page-head">
<p class="kicker">环境</p>
<h1>工作区检查</h1>
<p class="lede">QUANT_WORKSPACE_ROOT：{escape(root)}</p>
</header>
<section class="panel">
<table class="environment-table">
<colgroup><col style="width:22%"><col style="width:28%">
<col style="width:50%"></colgroup>
<tr><th>模板</th><th>状态</th><th>执行环境</th></tr>
{"".join(rows)}
</table>
<p>可为不同研究仓指定独立Python环境。设置QUANT_STUDIO_RUNTIMES指向本地运行环境配置；
环境配置只影响新运行，已保存的命令和结果保留原样。</p>
</section>"""
    return _layout("环境", body, "environment")


def resolve_run_asset(runs_root: str | Path, run_id: str, relative_path: str) -> Path:
    root = Path(runs_root).resolve()
    run_dir = (root / run_id).resolve()
    try:
        run_dir.relative_to(root)
    except ValueError as exc:
        raise QuantStudioError(f"非法运行 id: {run_id}") from exc
    path = safe_run_file(run_dir, relative_path, {".html", ".csv", ".json"})
    if path.suffix.lower() == ".json" and not path.is_relative_to(
        run_dir / "strategy-output"
    ):
        raise QuantStudioError("仅允许读取上游结果包内的 JSON 证据")
    if not path.is_file():
        raise QuantStudioError(f"运行文件不存在: {relative_path}")
    result_path = safe_run_file(run_dir, "result.json")
    if result_path.is_file():
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("research_view_sha256"):
            load_timing_view(run_dir, result)
    return path


def serve(
    host: str = "127.0.0.1",
    port: int = 8770,
    *,
    runs_root: str | Path | None = None,
) -> None:
    validate_host(host)
    root = (
        Path(runs_root)
        if runs_root is not None
        else Path(__file__).resolve().parents[2] / "runs"
    )
    root.mkdir(parents=True, exist_ok=True)

    class Handler(_Handler):
        pass

    Handler.runs_root = root
    Handler.csrf_token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


class _Handler(BaseHTTPRequestHandler):
    runs_root = Path("runs")
    csrf_token = ""

    def do_GET(self) -> None:
        self._frame_policy = "DENY"
        if not self._allow_request():
            return
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/":
                self._send_html(render_overview(self.runs_root))
            elif path == "/strategy":
                self._send_html(render_home())
            elif path == "/accounts":
                self._send_html(render_accounts())
            elif path.startswith("/accounts/"):
                self._send_html(render_accounts(path.removeprefix("/accounts/")))
            elif path == "/data":
                self._send_html(render_data())
            elif path == "/backtest":
                self._send_html(render_backtest(self.runs_root))
            elif path == "/results":
                self._send_html(render_results(self.runs_root))
            elif path == "/environment":
                self._send_html(render_environment())
            elif path.startswith("/templates/"):
                preset = parse_qs(urlparse(self.path).query).get("preset", [None])[0]
                template_id = path.removeprefix("/templates/")
                self._send_html(
                    render_template_page(
                        template_id, preset, csrf_token=self.csrf_token
                    )
                )
            elif path.startswith("/runs/") and "/files/" in path:
                remainder = path.removeprefix("/runs/")
                run_id, relative = remainder.split("/files/", 1)
                self._send_asset(resolve_run_asset(self.runs_root, run_id, relative))
            elif path.startswith("/runs/"):
                run_id = path.removeprefix("/runs/")
                run_dir = safe_run_file(self.runs_root, run_id)
                self._send_html(render_run(run_dir, csrf_token=self.csrf_token))
            else:
                self.send_error(404)
        except (QuantStudioError, FileNotFoundError, KeyError, json.JSONDecodeError):
            self.send_error(404)

    def do_POST(self) -> None:
        self._frame_policy = "DENY"
        if not self._allow_request(require_origin=True):
            return
        path = unquote(urlparse(self.path).path)
        code_view = False
        if not path.startswith("/templates/"):
            self.send_error(404)
            return
        try:
            content_types = self.headers.get_all("Content-Type") or []
            lengths = self.headers.get_all("Content-Length") or []
            if (
                len(content_types) != 1
                or len(lengths) != 1
                or self.headers.get_all("Transfer-Encoding")
            ):
                raise QuantStudioError("请求编码不明确")
            content_type = content_types[0].partition(";")[0].strip()
            if content_type.lower() != "application/x-www-form-urlencoded":
                raise QuantStudioError("请求类型不受支持")
            length = int(lengths[0])
            if length < 0 or length > 64_000:
                raise QuantStudioError("请求过大")
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            submitted_token = data.pop("_csrf_token", [])
            if not self._valid_csrf_token(submitted_token):
                self.send_error(403)
                return
            code_view = path.endswith("/code")
            template_id = path.removeprefix("/templates/")
            if code_view:
                template_id = template_id.removesuffix("/code")
                self._send_text(compiled_from_form(template_id, data))
                return
            action = data.pop("action", ["preview"])[0]
            if action not in {"preview", "check", "execute"}:
                raise QuantStudioError("未知模板操作")
            factor_form = data.pop("factor_form", [None])[0]
            chosen = data.pop("factor", None)
            snapshot = data.pop("snapshot", [None])[0] or None
            factors = list(chosen) if factor_form else None
            knobs = _parse_form_knobs(template_id, data)
            options = dict(factors=factors, runs_root=self.runs_root, snapshot=snapshot)
            if action == "check":
                result = preflight(template_id, knobs, **options)
            else:
                result = run(template_id, knobs, execute=action == "execute", **options)
            self.send_response(303)
            self.send_header("Location", f"/runs/{result.run_id}")
            self.end_headers()
        except (QuantStudioError, UnicodeDecodeError, ValueError) as exc:
            if code_view:
                self._send_text(f"错误: {exc}", status=400)
                return
            self.send_error(400, str(exc))

    def _allow_request(self, *, require_origin: bool = False) -> bool:
        try:
            hostname, port = self._loopback_authority()
            if require_origin:
                origins = self.headers.get_all("Origin") or []
                if len(origins) != 1:
                    raise QuantStudioError("需要唯一 Origin")
                origin = origins[0]
                parsed = urlsplit(origin)
                try:
                    origin_port = parsed.port
                except ValueError as exc:
                    raise QuantStudioError("非法 Origin") from exc
                if origin_port is None:
                    origin_port = 80 if parsed.scheme == "http" else -1
                if (
                    parsed.scheme != "http"
                    or parsed.hostname is None
                    or parsed.hostname.lower() != hostname
                    or origin_port != port
                    or parsed.username is not None
                    or parsed.password is not None
                    or parsed.path
                    or parsed.query
                    or parsed.fragment
                ):
                    raise QuantStudioError("Origin 与本机服务不匹配")
        except (QuantStudioError, ValueError):
            self.send_error(403)
            return False
        return True

    def _valid_csrf_token(self, submitted: list[str]) -> bool:
        if len(submitted) != 1 or not self.csrf_token:
            return False
        try:
            supplied = submitted[0].encode("ascii")
            expected = self.csrf_token.encode("ascii")
        except UnicodeEncodeError:
            return False
        return secrets.compare_digest(supplied, expected)

    def _loopback_authority(self) -> tuple[str, int]:
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1:
            raise QuantStudioError("需要唯一 Host")
        raw = hosts[0]
        if not raw or any(ord(char) < 33 or ord(char) == 127 for char in raw):
            raise QuantStudioError("缺少合法 Host")
        parsed = urlsplit(f"//{raw}")
        try:
            port = parsed.port
        except ValueError as exc:
            raise QuantStudioError("非法 Host") from exc
        hostname = parsed.hostname.lower() if parsed.hostname else ""
        if (
            hostname not in {"127.0.0.1", "localhost"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise QuantStudioError("Host 不是本机回环地址")
        port = 80 if port is None else port
        if port != self.server.server_port:
            raise QuantStudioError("Host 端口与服务不匹配")
        return hostname, port

    def end_headers(self) -> None:
        policy = getattr(self, "_frame_policy", "DENY")
        ancestors = "'self'" if policy == "SAMEORIGIN" else "'none'"
        self.send_header("Content-Security-Policy", f"frame-ancestors {ancestors}")
        self.send_header("X-Frame-Options", policy)
        super().end_headers()

    def _send_html(self, content: str) -> None:
        self._send_text(content, content_type="text/html; charset=utf-8")

    def _send_text(
        self,
        content: str,
        *,
        status: int = 200,
        content_type: str = "text/plain; charset=utf-8",
    ) -> None:
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_asset(self, path: Path) -> None:
        body = path.read_bytes()
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".csv": "text/csv; charset=utf-8",
            ".json": "application/json; charset=utf-8",
        }[path.suffix.lower()]
        if path.suffix.lower() == ".html":
            self._frame_policy = "SAMEORIGIN"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def _parse_form_knobs(
    template_id: str, form: dict[str, list[str]]
) -> dict[str, object]:
    template = load_template(template_id)
    knobs = {}
    declarations = {item["name"]: item for item in template.knobs}
    for name, values in form.items():
        if name not in declarations:
            raise QuantStudioError(f"未知参数 {name}")
        raw = values[0]
        kind = declarations[name]["type"]
        if kind == "int":
            knobs[name] = int(raw)
        elif kind == "number":
            knobs[name] = float(raw) if "." in raw else int(raw)
        else:
            knobs[name] = raw
    return knobs


_LABELS = {
    "rebalance_freq": "调仓频率",
    "initial_capital": "初始资金",
    "symbols_limit": "股票数量",
    "commission": "佣金率",
    "slippage": "滑点",
    "initial_cash": "初始资金（港币）",
    "rebalance_sessions": "调仓间隔",
}

_CHOICES = {
    "rebalance_freq": {"daily": "每个交易日", "weekly": "每周", "monthly": "每月"},
    "rebalance_sessions": {"1": "1 个交易日", "5": "5 个交易日"},
}

_STATUS = {
    "checked": "预检通过",
    "check_failed": "预检未通过",
    "previewed": "仅预览",
    "succeeded": "已完成",
    "failed": "失败",
    "blocked": "未执行",
}

_CODE_SCRIPT = """
const form = document.querySelector("form.flow");
const compiled = document.querySelector("#compiled");
let pending = 0;
async function refreshCode() {
  const ticket = ++pending;
  const body = new URLSearchParams(new FormData(form));
  const response = await fetch(form.dataset.code, {
    method: "POST",
    headers: {"Content-Type": "application/x-www-form-urlencoded"},
    body
  });
  const text = await response.text();
  if (ticket === pending) compiled.textContent = text;
}
form.addEventListener("input", refreshCode);
form.addEventListener("change", refreshCode);
"""


def _request_summary(request: dict[str, object]) -> str:
    knobs = request.get("knobs")
    factors = request.get("factors")
    parts = []
    if isinstance(knobs, dict) and knobs:
        text = "，".join(f"{key}={value}" for key, value in knobs.items())
        parts.append(f"参数：{text}")
    else:
        parts.append("参数：模板默认")
    if isinstance(factors, list) and factors:
        names = "、".join(str(name) for name in factors)
        parts.append(f"因子：{names}")
    return f'<span class="meta">{escape(" · ".join(parts))}</span>'


def compiled_from_form(template_id: str, form: dict[str, list[str]]) -> str:
    data = dict(form)
    data.pop("_csrf_token", None)
    factor_form = data.pop("factor_form", [None])[0]
    chosen = data.pop("factor", None)
    data.pop("action", None)
    snapshot = data.pop("snapshot", [None])[0] or None
    factors = list(chosen) if factor_form else None
    knobs = _parse_form_knobs(template_id, data)
    return compile_document(
        load_template(template_id), knobs, factors, snapshot=snapshot
    )


def _knob_fields(template: object, selected: dict[str, object]) -> list[str]:
    fields = []
    for knob in template.knobs:
        name = escape(knob["name"])
        current = selected.get(knob["name"], knob["default"])
        shown = escape(str(current))
        label = escape(knob.get("label", _LABELS.get(knob["name"], knob["name"])))
        if knob["type"] == "enum" or "choices" in knob:
            labels = {
                str(choice): knob.get("choice_labels", {}).get(
                    str(choice), _choice_label(knob["name"], choice)
                )
                for choice in knob["choices"]
            }
            options = "".join(
                f'<option value="{escape(str(choice))}"'
                + (" selected" if choice == current else "")
                + f">{escape(labels[str(choice)])}</option>"
                for choice in knob["choices"]
            )
            control = f'<select name="{name}">{options}</select>'
        else:
            input_type = (
                "date"
                if knob["type"] == "date"
                else "text"
                if knob["type"] in {"decimal-string", "text"}
                else "number"
            )
            step = ' step="any"' if knob["type"] == "number" else ""
            control = f'<input type="{input_type}" name="{name}" value="{shown}"{step}>'
        fields.append(
            f"<label><span>{label}</span><small>{name}</small>{control}</label>"
        )
    return fields


def _data_step(template: object) -> str:
    if template.kind == "synthetic":
        return "<p>使用仓库内固定收益率，不读取行情。</p>"
    repo = str(template.workspace_repo)
    dataset = next(
        (item for item in scan_datasets() if repo in item.path.parts),
        None,
    )
    parts = [f"<p>{escape(template_readiness(template).message)}</p>"]
    if template.metadata.get("standard_view"):
        parts.append(f"<p>{escape(template.metadata['data_help'])}</p>")
        return "".join(parts)
    if template.metadata.get("requires_snapshot"):
        source = template.metadata["input_source"]
        selected = escape(os.environ.get(source["environment"], ""))
        parts.append(
            f"<label><span>已有{escape(source['label'])}</span>"
            f'<input type="text" name="snapshot" value="{selected}"'
            ' placeholder="填写已有输入路径或使用已配置来源"></label>'
        )
        if template.metadata.get("data_help"):
            parts.append(f"<p>{escape(template.metadata['data_help'])}</p>")
        if source.get("kind") == "file":
            parts.append("<p>原生预检会按所选配置检查其引用的输入文件。</p>")
            return "".join(parts)
    if dataset:
        parts.append(
            f"<p>{dataset.files}个数据文件，最近文件修改{escape(dataset.newest)}</p>"
            f'<p class="path">{escape(str(dataset.path))}</p>'
        )
    else:
        parts.append(
            "<p>默认数据目录未发现数据；已指定的外部快照由研究工具单独验证。</p>"
        )
    command = FETCH_BY_REPO.get(repo)
    if command:
        parts.append(f"<pre>{escape(command)}</pre>")
    data = template.base_config.get("data")
    if isinstance(data, dict):
        rows = "".join(
            f"<li><b>{escape(str(key))}</b> {escape(str(value))}</li>"
            for key, value in data.items()
        )
        parts.append(f'<ul class="data-keys">{rows}</ul>')
    return "".join(parts)


def _run_step(template: object) -> str:
    ready = template_readiness(template)
    disabled = "" if ready.runnable or ready.needs_input else " disabled"
    hint = "" if ready.runnable else f'<p class="banner">{escape(ready.message)}</p>'
    check = (
        '<button class="btn" name="action" value="check">数据预检</button>'
        if template.metadata.get("preflight_argv")
        else ""
    )
    return (
        f"{hint}"
        '<div class="actions">'
        '<button class="btn" name="action" value="preview">仅预览</button>'
        f"{check}"
        '<button class="btn primary" name="action" value="execute"'
        f"{disabled}>运行</button>"
        "</div>"
        f'<p class="note">{escape(template.disclaimer)}</p>'
    )


def _step(index: int, key: str, title: str, summary: str, body: str) -> str:
    return (
        f'<li class="step" id="{key}">'
        f'<div class="step-index">{index}</div>'
        '<section class="step-card">'
        f"<h2>{escape(title)}</h2>"
        f'<p class="step-copy">{escape(summary)}</p>'
        f"{body}</section></li>"
    )


def _factor_fields(template: object) -> str:
    catalog = list(template.metadata.get("factor_catalog") or [])
    if not catalog:
        return ""
    boxes = ['<input type="hidden" name="factor_form" value="1">']
    if template.id == "a-share-four-factor":
        boxes.append(
            '<p class="note">股票池固定为沪深300。'
            "因子只能勾选这个模板已经实现的项。</p>"
        )
    group = None
    for item in catalog:
        if item["group"] != group:
            if group is not None:
                boxes.append("</div>")
            group = item["group"]
            boxes.append(
                f'<p class="eyebrow">{escape(str(group))}</p><div class="factor-grid">'
            )
        boxes.append(
            '<label class="check"><input type="checkbox" name="factor" '
            f'value="{escape(item["name"])}" checked>'
            f"<span>{escape(item['label'])}</span></label>"
        )
    boxes.append("</div>")
    return "".join(boxes)


def _saved_config(directory: Path) -> str:
    for name in ("config.yaml", "config.json"):
        path = directory / name
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            return (
                '<section class="panel command"><h2>生成的配置</h2>'
                f"<pre>{escape(text)}</pre></section>"
            )
    return ""


def _artifact_tables(
    directory: Path,
    *,
    native: bool = False,
    table_titles: dict | None = None,
    column_labels: dict | None = None,
) -> str:
    titles = {
        "account.csv": "账户快照",
        "positions.csv": "持仓",
        "holdings.csv": "持仓",
        "orders.csv": "委托",
        "fills.csv": "成交",
        "trades.csv": "成交",
        "margin.csv": "保证金",
        "costs.csv": "费用与资金费",
        "cash_ledger.csv": "现金账本",
    }
    labels = {
        "event_time": "事件时间（UTC）",
        "account_id": "账户",
        "base_currency": "基础币种",
        "currency": "币种",
        "nav": "账户净值",
        "cash_value": "现金",
        "market_value": "市值",
        "unrealized_pnl": "未实现损益",
        "realized_pnl": "已实现损益",
        "margin_used": "已用保证金",
        "instrument_id": "标的",
        "strategy_id": "策略",
        "quantity": "数量",
        "mark_price": "估值价格",
        "price": "成交价",
        "initial_margin": "初始保证金",
        "maintenance_margin": "维持保证金",
        "order_id": "委托ID",
        "fill_id": "成交ID",
        "side": "方向",
        "liquidity_role": "成交方式",
        "amount": "金额",
        "cost_type": "费用类型",
        "order_type": "委托类型",
        "limit_price": "限价",
        "stop_price": "触发价",
        "filled_quantity": "已成交数量",
        "status": "状态",
        "reduce_only": "仅减仓",
        "event_type": "事件类型",
        "ledger_account": "账本科目",
        "quantity_delta": "数量变动",
    }
    labels = (labels if native else {}) | (column_labels or {})
    parts = []
    for name, title in titles.items():
        title = (table_titles or {}).get(name, title)
        path = directory / name
        if path.is_file():
            parts.append(html_table(path, title, column_labels=labels))
            parts.append(
                f'<p><a href="/runs/{escape(directory.name)}/files/{name}" download>'
                f"下载完整{escape(title)}CSV</a></p>"
            )
    return "".join(parts)


def _has_benchmark(path: Path) -> bool:
    header = path.read_text(encoding="utf-8").splitlines()[:1]
    return bool(header) and "benchmark" in header[0]


def _preset_values(template: object, preset_id: str | None) -> dict[str, object]:
    if not preset_id:
        return {}
    for preset in getattr(template, "presets", []) or []:
        if preset.get("id") == preset_id:
            return dict(preset.get("values", {}))
    return {}


def _preset_links(template: object, preset_id: str | None) -> str:
    default_on = "" if preset_id else " on"
    links = [f'<a class="chip{default_on}" href="?">当前默认</a>']
    for preset in getattr(template, "presets", []) or []:
        active = " on" if preset.get("id") == preset_id else ""
        links.append(
            f'<a class="chip{active}" href="?preset={escape(str(preset["id"]))}">'
            f"{escape(str(preset['label']))}</a>"
        )
    return "".join(links)


def _choice_label(name: str, choice: object) -> str:
    return _CHOICES.get(name, {}).get(str(choice), str(choice))


def _layout(title: str, body: str, active: str = "strategy") -> str:
    links = []
    for key, label, href in (
        ("overview", "总览", "/"),
        ("environment", "环境", "/environment"),
        ("data", "数据", "/data"),
        ("strategy", "策略", "/strategy"),
        ("backtest", "回测", "/backtest"),
        ("results", "结果", "/results"),
        ("accounts", "账户", "/accounts"),
    ):
        mark = " on" if key == active else ""
        links.append(f'<a class="nav{mark}" href="{href}">{label}</a>')
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} · Quant Studio</title>
<style>{_CSS}</style></head>
<body><div class="shell">
<aside class="side">
<a class="brand" href="/"><b>Quant Studio</b><span>本地研究台</span></a>
<nav>{"".join(links)}</nav>
<p class="side-note">不下真实订单。曲线只来自合成样例，或上游已经交出的净值。</p>
</aside>
<div class="stage">{body}</div>
</div></body></html>"""


_CSS = """
:root {
  --ink: #101828;
  --muted: #667085;
  --line: #e6ebf2;
  --blue: #1677ff;
  --bg: #f3f6fb;
  --card: #ffffff;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  color: var(--ink);
  background: var(--bg);
  font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
}
.shell { display: flex; min-height: 100vh; }
.overview-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(155px, 1fr)); gap: 14px;
}
.overview-card {
  display: flex; flex-direction: column; padding: 20px;
  border: 1px solid var(--line); border-radius: 14px;
  background: white; color: var(--ink); text-decoration: none;
}
.overview-card strong { font-size: 32px; margin: 12px 0; }
.overview-card small { color: var(--muted); line-height: 1.6; }
.onboarding li { margin: 12px 0; }
.side {
  width: 232px;
  flex: none;
  padding: 28px 20px;
  color: #d7e0ee;
  background: #0b1220;
}
.brand {
  display: block;
  color: inherit;
  text-decoration: none;
}
.brand b { display: block; font-size: 18px; letter-spacing: 0.02em; }
.brand span { color: #8ea0b8; font-size: 12px; }
nav { margin-top: 32px; }
nav a {
  display: block;
  margin-top: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  color: white;
  background: #172033;
  text-decoration: none;
}
nav a.on { background: #24406e; }
label.check { display: flex; align-items: center; gap: 8px; }
label.check input { width: auto; margin: 0; }
ul.commands { padding-left: 18px; }
.badge {
  margin: 12px 0 0;
  color: var(--muted);
  font-size: 13px;
}
.badge.ready { color: #067647; }
.presets { margin-bottom: 16px; }
.chip {
  display: inline-block;
  margin: 0 8px 8px 0;
  padding: 6px 12px;
  border: 1px solid #d0d7e2;
  border-radius: 999px;
  color: inherit;
  text-decoration: none;
}
.chip.on { border-color: var(--blue); background: #e8f1ff; color: #175cd3; }
button:disabled { opacity: 0.45; cursor: not-allowed; }
table { width: 100%; border-collapse: collapse; }
.table-scroll { max-width: 100%; overflow-x: auto; }
.table-scroll td, .table-scroll th { padding-right: 16px; white-space: nowrap; }
.environment-table { table-layout: fixed; }
.environment-table td, .environment-table th {
  overflow-wrap: anywhere; padding: 12px 16px 12px 0; vertical-align: top;
}
.environment-table td:last-child { font: 12px/1.7 ui-monospace, monospace; }
summary { cursor: pointer; font-weight: 600; }
td, th {
  padding: 10px 0;
  border-bottom: 1px solid var(--line);
  text-align: left;
}
.chart-card {
  max-width: 960px;
  margin-top: 16px;
  padding: 8px 8px 0;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: white;
}
.stats { display: flex; flex-wrap: wrap; gap: 12px; margin: 12px 0 4px; }
.stat {
  min-width: 140px;
  padding: 12px 14px;
  border-radius: 12px;
  background: #f5f8fc;
}
.stat b { display: block; font-size: 20px; }
.stat span { color: var(--muted); font-size: 12px; }
.chart-card svg { width: 100%; height: auto; }
.chart-card line { stroke: #e6ebf2; }
.chart-card text { fill: #98a2b3; font-size: 12px; }
.side-note { margin-top: 28px; color: #8ea0b8; font-size: 12px; line-height: 1.6; }
.stage { flex: 1; min-width: 0; padding: 32px 28px 48px; }
.page-head { max-width: 880px; margin-bottom: 24px; }
.kicker {
  margin: 0 0 8px;
  color: var(--blue);
  font-size: 13px;
  font-weight: 650;
}
h1 { margin: 0; font-size: 32px; letter-spacing: -0.03em; }
.lede { max-width: 640px; color: var(--muted); line-height: 1.6; }
.meta { display: block; margin-top: 8px; }
.note a { color: inherit; }
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
  max-width: 1080px;
}
.trail {
  margin-top: 12px;
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.02em;
}
.template-card {
  display: flex;
  flex-direction: column;
  min-height: 220px;
  padding: 20px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: var(--card);
}
.template-card.synthetic { background: #f7f3ff; border-color: #ddd0f5; }
.eyebrow { color: var(--muted); font-size: 12px; }
.template-card h2 { margin: 10px 0 8px; font-size: 20px; }
.template-card p { margin: 0; color: #475467; line-height: 1.55; }
.open { margin-top: auto; padding-top: 18px; color: var(--blue); font-weight: 650; }
.panel {
  max-width: 880px;
  padding: 24px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: var(--card);
}
.studio-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 20px;
  align-items: start;
  max-width: 1120px;
}
.code-card {
  position: sticky;
  top: 20px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: var(--card);
}
.code-card h2 { margin: 0 0 8px; font-size: 16px; }
.code-card pre { max-height: 70vh; }
.steps { list-style: none; margin: 0; padding: 0; }
.step {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr);
  gap: 12px;
  position: relative;
  margin-bottom: 14px;
}
.step:not(:last-child)::before {
  content: "";
  position: absolute;
  left: 13px;
  top: 28px;
  bottom: -14px;
  width: 1px;
  background: var(--line);
}
.fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
  column-gap: 12px;
}
.factor-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 12px;
  margin-bottom: 12px;
}
.factor-grid .check { margin: 0; }
.step-index {
  width: 28px;
  height: 28px;
  border-radius: 999px;
  background: #0b1220;
  color: white;
  font-size: 13px;
  line-height: 28px;
  text-align: center;
}
.step-card {
  padding: 16px 18px 4px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: white;
}
.step-card h2 { margin: 0; font-size: 16px; }
.step-copy { margin: 6px 0 14px; color: var(--muted); font-size: 13px; }
.path, .data-keys { color: var(--muted); font-size: 13px; }
.data-keys { padding-left: 18px; }
label { display: block; margin: 0 0 16px; }
label span { display: block; font-weight: 650; }
label small { color: var(--muted); }
input, select {
  width: 100%;
  margin-top: 8px;
  padding: 11px 12px;
  border: 1px solid #d0d7e2;
  border-radius: 10px;
  background: #fff;
  color: var(--ink);
  font: inherit;
}
.actions { display: flex; gap: 10px; }
.btn {
  padding: 10px 16px;
  border: 1px solid #d0d7e2;
  border-radius: 10px;
  background: white;
  font: inherit;
  cursor: pointer;
}
.btn.primary { border-color: var(--blue); background: var(--blue); color: white; }
.note, .banner {
  margin: 16px 0 0;
  padding: 12px 14px;
  border-radius: 10px;
  background: #fff7e8;
  color: #8a5a00;
}
.banner.quiet { background: #f2f4f7; color: var(--muted); }
.status {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 999px;
  background: #e8f1ff;
  color: #175cd3;
  font-size: 13px;
}
.status.failed, .status.blocked { background: #fdecec; color: #b42318; }
.command { margin-bottom: 16px; }
.command h2 { margin: 0 0 10px; font-size: 16px; }
pre {
  margin: 0;
  padding: 14px;
  overflow: auto;
  border-radius: 10px;
  background: #0f172a;
  color: #e5eefc;
  white-space: pre-wrap;
}
iframe {
  width: min(960px, 100%);
  height: 560px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: white;
}
@media (max-width: 1100px) {
  .shell { flex-direction: column; }
  .side { width: auto; padding: 16px 20px 8px; }
  nav { margin-top: 12px; }
  .side-note { display: none; }
  .stage { padding: 24px 20px 40px; }
  .studio-grid { grid-template-columns: 1fr; }
  .code-card { position: static; }
  .fields, .factor-grid { grid-template-columns: 1fr; }
}
"""
