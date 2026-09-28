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
<span>{label}</span><h2>{escape(template.title)}</h2>
<p>{escape(template.summary)}</p>
<a href="/templates/{escape(template.id)}">打开模板</a>
</article>"""
        )
    return _layout(
        "quant-studio", "<h1>本地模板回测台</h1><main>" + "".join(cards) + "</main>"
    )


def render_template_page(template_id: str) -> str:
    template = load_template(template_id)
    fields = []
    for knob in template.knobs:
        name = escape(knob["name"])
        default = escape(str(knob["default"]))
        if knob["type"] == "enum" or "choices" in knob:
            options = "".join(
                f'<option value="{escape(str(choice))}"'
                + (" selected" if choice == knob["default"] else "")
                + f">{escape(str(choice))}</option>"
                for choice in knob["choices"]
            )
            control = f'<select name="{name}">{options}</select>'
        else:
            input_type = "text" if knob["type"] == "decimal-string" else "number"
            step = ' step="any"' if knob["type"] == "number" else ""
            control = (
                f'<input type="{input_type}" name="{name}" value="{default}"{step}>'
            )
        fields.append(f"<label>{name}{control}</label>")
    body = f"""<a href="/">返回首页</a>
<h1>{escape(template.title)}</h1>
<p>{escape(template.summary)}</p>
<form method="post" action="/templates/{escape(template.id)}">
{"".join(fields)}
<button name="action" value="preview">预览</button>
<button name="action" value="execute">执行</button>
</form>
<p>{escape(template.disclaimer)}</p>"""
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
    synthetic_notice = (
        "<p><strong>合成样例，不是市场收益</strong></p>"
        if template.kind == "synthetic"
        else ""
    )
    body = f"""<a href="/">返回首页</a>
<h1>运行结果：{escape(result["status"])}</h1>
<h2>命令预览</h2><pre>{argv}</pre>
{synthetic_notice}{report_html}"""
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


def _layout(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:1100px;margin:2rem auto;
padding:0 1rem;color:#18212f}}
main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem}}
.template-card{{border:1px solid #ccd3df;border-radius:12px;padding:1rem}}
.synthetic{{border-color:#9b6bda;background:#faf7ff}}
label{{display:block;margin:.8rem 0}}
input,select{{display:block;padding:.5rem;min-width:18rem}}
button{{margin-right:.5rem;padding:.6rem 1rem}}
pre{{white-space:pre-wrap;background:#f4f6f9;padding:1rem}}
iframe{{width:100%;height:480px;border:1px solid #ccd3df}}
</style></head><body>{body}</body></html>"""
