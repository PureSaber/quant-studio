from __future__ import annotations

import json
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from quant_studio import QuantStudioError
from quant_studio.runner import run, safe_run_file
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
        cards.append(
            f"""<article class="template-card {card_kind}">
<span class="eyebrow">{label}</span>
<h2>{escape(template.title)}</h2>
<p>{escape(template.summary)}</p>
<a class="open" href="/templates/{escape(template.id)}">打开模板</a>
</article>"""
        )
    body = f"""<header class="page-head">
<p class="kicker">模板库</p>
<h1>选择一个研究模板</h1>
<p class="lede">只改允许的参数。研究模板先给出命令；
合成样例会画出净值，并标明不是市场收益。</p>
</header>
<main class="card-grid">{"".join(cards)}</main>"""
    return _layout("模板库", body)


def render_template_page(template_id: str) -> str:
    template = load_template(template_id)
    fields = []
    for knob in template.knobs:
        name = escape(knob["name"])
        default = escape(str(knob["default"]))
        label = escape(_LABELS.get(knob["name"], knob["name"]))
        if knob["type"] == "enum" or "choices" in knob:
            options = "".join(
                f'<option value="{escape(str(choice))}"'
                + (" selected" if choice == knob["default"] else "")
                + f">{escape(_choice_label(knob['name'], choice))}</option>"
                for choice in knob["choices"]
            )
            control = f'<select name="{name}">{options}</select>'
        else:
            input_type = "text" if knob["type"] == "decimal-string" else "number"
            step = ' step="any"' if knob["type"] == "number" else ""
            control = (
                f'<input type="{input_type}" name="{name}" value="{default}"{step}>'
            )
        fields.append(
            f"<label><span>{label}</span><small>{name}</small>{control}</label>"
        )
    kind = "合成样例" if template.kind == "synthetic" else "研究模板"
    body = f"""<header class="page-head">
<p class="kicker">{kind}</p>
<h1>{escape(template.title)}</h1>
<p class="lede">{escape(template.summary)}</p>
</header>
<section class="panel">
<form method="post" action="/templates/{escape(template.id)}">
{"".join(fields)}
<div class="actions">
<button class="btn" name="action" value="preview">仅预览</button>
<button class="btn primary" name="action" value="execute">运行</button>
</div>
</form>
<p class="note">{escape(template.disclaimer)}</p>
</section>"""
    return _layout(template.title, body)


def render_run(run_dir: str | Path) -> str:
    directory = Path(run_dir)
    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    template = load_template(request["template_id"])
    argv = " ".join(escape(str(item)) for item in result["argv"])
    report_html = ""
    if result.get("report"):
        report = safe_run_file(directory, result["report"], {".html"})
        if report.is_file():
            report_html = (
                f'<iframe title="运行报告" src="/runs/{escape(directory.name)}'
                f'/files/{escape(result["report"])}"></iframe>'
            )
    status = escape(str(result["status"]))
    notice = ""
    if template.kind == "synthetic":
        notice = '<p class="banner">合成样例，不是市场收益</p>'
    elif result["status"] != "succeeded":
        notice = (
            '<p class="banner quiet">这次没有嵌入净值。'
            "研究模板只有在上游报告写入本次运行目录后才显示曲线。</p>"
        )
    body = f"""<header class="page-head">
<p class="kicker">运行</p>
<h1>{escape(template.title)}</h1>
<p class="lede"><span class="status {status}">
{_STATUS.get(result["status"], status)}</span></p>
</header>
{notice}
<section class="panel command">
<h2>命令预览</h2>
<pre>{argv or "进程内合成样例，无外部命令"}</pre>
</section>
{report_html}"""
    return _layout("运行结果", body)


def resolve_run_asset(runs_root: str | Path, run_id: str, relative_path: str) -> Path:
    root = Path(runs_root).resolve()
    run_dir = (root / run_id).resolve()
    try:
        run_dir.relative_to(root)
    except ValueError as exc:
        raise QuantStudioError(f"非法运行 id: {run_id}") from exc
    path = safe_run_file(run_dir, relative_path, {".html", ".csv"})
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
            if path == "/":
                self._send_html(render_home())
            elif path.startswith("/templates/"):
                self._send_html(render_template_page(path.removeprefix("/templates/")))
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
        if not path.startswith("/templates/"):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 64_000:
                raise QuantStudioError("请求过大")
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            template_id = path.removeprefix("/templates/")
            action = data.pop("action", ["preview"])[0]
            knobs = _parse_form_knobs(template_id, data)
            result = run(
                template_id,
                knobs,
                execute=action == "execute",
                runs_root=self.runs_root,
            )
            self.send_response(303)
            self.send_header("Location", f"/runs/{result.run_id}")
            self.end_headers()
        except (QuantStudioError, UnicodeDecodeError, ValueError) as exc:
            self.send_error(400, str(exc))

    def _send_html(self, content: str) -> None:
        body = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_asset(self, path: Path) -> None:
        body = path.read_bytes()
        content_type = (
            "text/html; charset=utf-8"
            if path.suffix.lower() == ".html"
            else "text/csv; charset=utf-8"
        )
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


def _choice_label(name: str, choice: object) -> str:
    return _CHOICES.get(name, {}).get(str(choice), str(choice))


def _layout(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} · Quant Studio</title>
<style>{_CSS}</style></head>
<body><div class="shell">
<aside class="side">
<a class="brand" href="/"><b>Quant Studio</b><span>本地研究台</span></a>
<nav><a href="/">模板库</a></nav>
<p class="side-note">不下真实订单。净值只来自合成样例，或上游已经写入的报告。</p>
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
  padding: 10px 12px;
  border-radius: 8px;
  color: white;
  background: #172033;
  text-decoration: none;
}
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
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
  max-width: 1080px;
}
.template-card {
  display: flex;
  flex-direction: column;
  min-height: 210px;
  padding: 20px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: var(--card);
  box-shadow: 0 8px 24px rgba(16, 24, 40, 0.04);
}
.template-card.synthetic { background: #f7f3ff; border-color: #ddd0f5; }
.eyebrow { color: var(--muted); font-size: 12px; }
.template-card h2 { margin: 10px 0 8px; font-size: 20px; }
.template-card p { margin: 0; color: #475467; line-height: 1.55; }
.open { margin-top: auto; padding-top: 18px; color: var(--blue); font-weight: 650; }
.panel {
  max-width: 720px;
  padding: 24px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: var(--card);
  box-shadow: 0 8px 24px rgba(16, 24, 40, 0.04);
}
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
  border: 0;
  border-radius: 16px;
  background: white;
  box-shadow: 0 8px 24px rgba(16, 24, 40, 0.05);
}
@media (max-width: 1100px) {
  .shell { flex-direction: column; }
  .side { width: auto; padding: 16px 20px 8px; }
  nav { margin-top: 12px; }
  .side-note { display: none; }
  .stage { padding: 24px 20px 40px; }
}
"""
