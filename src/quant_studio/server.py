from __future__ import annotations

import json
import os
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import yaml

from quant_studio import QuantStudioError
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
from quant_studio.runner import run, safe_run_file, template_readiness
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


def render_data() -> str:
    root = workspace_root()
    datasets = scan_datasets(root)
    cards = []
    for item in datasets:
        cards.append(
            f"""<article class="template-card research">
<span class="eyebrow">{escape(item.market)}</span>
<h2>{item.files} 个数据文件</h2>
<p>最近更新 {escape(item.newest)}</p>
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
    root_text = str(root) if root else "未设置"
    body = f"""<header class="page-head">
<p class="kicker">数据</p>
<h1>本机行情</h1>
<p class="lede">工作区：{escape(root_text)}</p>
</header>
<main class="card-grid">{"".join(cards)}</main>
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


def render_template_page(template_id: str, preset_id: str | None = None) -> str:
    template = load_template(template_id)
    selected = _preset_values(template, preset_id)
    catalog = list(template.metadata.get("factor_catalog") or [])
    factors = [item["name"] for item in catalog] or None
    document = compile_document(template, selected, factors)
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
        "data": "只读取工作区里已经存在的快照，不在这里下载。",
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


def render_run(run_dir: str | Path) -> str:
    directory = Path(run_dir)
    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    template = load_template(request["template_id"])
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
    if nav_path.is_file():
        opening_key = template.metadata.get("nav_initial_value_key")
        opening = None
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
            chart = chart_fragment(series)
    notices = []
    if template.kind == "synthetic":
        notices.append('<p class="banner">合成样例，不是市场收益</p>')
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
    tables = _artifact_tables(directory)
    benchmark = '<p class="note">未提供基准净值。</p>' if chart else ""
    drawdown = ""
    if series is not None:
        drawdown = drawdown_fragment(series)
        if _has_benchmark(nav_path):
            benchmark = '<p class="note">净值文件包含基准列，已画在主图。</p>'
    body = f"""<header class="page-head">
<p class="kicker">结果</p>
<h1>{escape(template.title)}</h1>
<p class="lede"><span class="status {status}">
{_STATUS.get(result["status"], status)}</span>
{_request_summary(request)}</p>
</header>
{"".join(notices)}
{benchmark}
{_saved_config(directory)}
<section class="panel command">
<h2>参数列表</h2>
<pre>{argv or "进程内合成样例，无外部命令"}</pre>
</section>
{chart}
{drawdown}
{tables}
{report_html}"""
    return _layout("运行结果", body, "results")


def render_environment() -> str:
    root = os.environ.get("QUANT_WORKSPACE_ROOT") or "未设置"
    rows = []
    for template_id in template_ids():
        template = load_template(template_id)
        ready = template_readiness(template)
        rows.append(
            "<tr>"
            f"<td>{escape(template.title)}</td>"
            f"<td>{escape(ready.message)}</td>"
            "</tr>"
        )
    body = f"""<header class="page-head">
<p class="kicker">环境</p>
<h1>工作区检查</h1>
<p class="lede">QUANT_WORKSPACE_ROOT：{escape(root)}</p>
</header>
<section class="panel">
<table>
<tr><th>模板</th><th>状态</th></tr>
{"".join(rows)}
</table>
</section>"""
    return _layout("环境", body, "data")


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
    server = ThreadingHTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


class _Handler(BaseHTTPRequestHandler):
    runs_root = Path("runs")

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            if path in {"/", "/strategy"}:
                self._send_html(render_home())
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
                self._send_html(render_template_page(template_id, preset))
            elif path.startswith("/runs/") and "/files/" in path:
                remainder = path.removeprefix("/runs/")
                run_id, relative = remainder.split("/files/", 1)
                self._send_asset(resolve_run_asset(self.runs_root, run_id, relative))
            elif path.startswith("/runs/"):
                run_id = path.removeprefix("/runs/")
                run_dir = safe_run_file(self.runs_root, run_id)
                self._send_html(render_run(run_dir))
            else:
                self.send_error(404)
        except (QuantStudioError, FileNotFoundError, KeyError, json.JSONDecodeError):
            self.send_error(404)

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        code_view = False
        if not path.startswith("/templates/"):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 64_000:
                raise QuantStudioError("请求过大")
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            code_view = path.endswith("/code")
            template_id = path.removeprefix("/templates/")
            if code_view:
                template_id = template_id.removesuffix("/code")
                self._send_text(compiled_from_form(template_id, data))
                return
            action = data.pop("action", ["preview"])[0]
            factor_form = data.pop("factor_form", [None])[0]
            chosen = data.pop("factor", None)
            snapshot = data.pop("snapshot", [None])[0] or None
            factors = list(chosen) if factor_form else None
            knobs = _parse_form_knobs(template_id, data)
            result = run(
                template_id,
                knobs,
                factors=factors,
                execute=action == "execute",
                runs_root=self.runs_root,
                snapshot=snapshot,
            )
            self.send_response(303)
            self.send_header("Location", f"/runs/{result.run_id}")
            self.end_headers()
        except (QuantStudioError, UnicodeDecodeError, ValueError) as exc:
            if code_view:
                self._send_text(f"错误: {exc}", status=400)
                return
            self.send_error(400, str(exc))

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
        label = escape(_LABELS.get(knob["name"], knob["name"]))
        if knob["type"] == "enum" or "choices" in knob:
            options = "".join(
                f'<option value="{escape(str(choice))}"'
                + (" selected" if choice == current else "")
                + f">{escape(_choice_label(knob['name'], choice))}</option>"
                for choice in knob["choices"]
            )
            control = f'<select name="{name}">{options}</select>'
        else:
            input_type = "text" if knob["type"] == "decimal-string" else "number"
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
    if template.metadata.get("requires_snapshot"):
        parts.append(
            "<label><span>已有港股快照目录</span>"
            '<input type="text" name="snapshot" placeholder="留空使用默认快照"></label>'
        )
    if dataset:
        parts.append(
            f"<p>{dataset.files} 个数据文件，最近更新 {escape(dataset.newest)}</p>"
            f'<p class="path">{escape(str(dataset.path))}</p>'
        )
    else:
        parts.append("<p>还没有读到这个仓库的数据目录。</p>")
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
    return (
        f"{hint}"
        '<div class="actions">'
        '<button class="btn" name="action" value="preview">仅预览</button>'
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


def _artifact_tables(directory: Path) -> str:
    titles = {
        "positions.csv": "持仓",
        "holdings.csv": "持仓",
        "orders.csv": "委托",
        "fills.csv": "成交",
        "trades.csv": "成交",
    }
    parts = []
    for name, title in titles.items():
        path = directory / name
        if path.is_file():
            parts.append(html_table(path, title))
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
        ("data", "数据", "/data"),
        ("strategy", "策略", "/strategy"),
        ("backtest", "回测", "/backtest"),
        ("results", "结果", "/results"),
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
<a class="brand" href="/strategy"><b>Quant Studio</b><span>本地研究台</span></a>
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
.stats { display: flex; gap: 12px; margin: 12px 0 4px; }
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
