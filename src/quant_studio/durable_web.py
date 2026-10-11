"""Authenticated workbench pages for independently executed factor studies."""

import json
import uuid
from html import escape

from quant_studio import QuantStudioError
from quant_studio.durable_research import configured_client
from quant_studio.workbench_web import csrf, field

STATUS = {
    "queued": "等待Worker",
    "running": "正在计算",
    "succeeded": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
    "expired": "租约失效",
}


class DurableHandler:
    def _durable_get(self, path):
        from quant_studio.server import _layout

        body = '<header class="page-head"><h1>后台因子研究</h1></header>'
        try:
            client = configured_client()
        except (QuantStudioError, FileNotFoundError, ValueError) as exc:
            body += '<section class="panel"><p>' + escape(str(exc)) + "</p></section>"
            self._send_html(_layout("后台研究", body, "durable"))
            return
        if path == "/durable-research":
            batch_available = client.supports_batches()
            body += (
                '<section class="panel"><p>选择固定快照和观察窗口。'
                "关闭浏览器或重启网页服务后，独立Worker继续计算。</p>"
            )
            for name, item in client.snapshots.items():
                body += f'''<form method="post" action="/durable-research/submit">
{csrf(self.csrf_token)}<h2>{escape(item["title"])}</h2>
<input type="hidden" name="snapshot" value="{escape(name, quote=True)}">
<input type="hidden" name="revision" value="{client.revision(name)}">
<input type="hidden" name="key" value="{uuid.uuid4().hex}">
<label>观察窗口（周期）<input type="number" name="window" min="1" max="4096"
value="{item["request"]["payload"]["factors"][0]["window_periods"]}" required></label>
<button class="btn primary">提交研究</button></form>'''
                if batch_available:
                    body += f'''<form method="post"
action="/durable-research/batch-submit">
{csrf(self.csrf_token)}<h3>比较多个观察窗口</h3>
<input type="hidden" name="snapshot" value="{escape(name, quote=True)}">
<input type="hidden" name="revision" value="{client.revision(name)}">
<input type="hidden" name="key" value="{uuid.uuid4().hex}">
<label>窗口（逗号分隔，最多64个）
<input name="windows" value="5,20,60" maxlength="320" required></label>
<p>候选共用同一固定输入，分别保留结果及失败记录。</p>
<button class="btn">提交批量研究</button></form>'''
            body += '</section><section class="panel"><h2>最近批次</h2><ul>'
            for batch in client.batches() if batch_available else []:
                body += (
                    f'<li><a href="/durable-research/batches/{batch["batch_id"]}">'
                    + escape(batch["request"]["run_id"])
                    + "</a> · "
                    + escape(STATUS[batch["status"]])
                    + "</li>"
                )
            body += "</ul>"
            if not batch_available:
                body += "<p>当前环境尚未启用批量研究，单次研究可继续使用。</p>"
            body += (
                '</section><section class="panel"><h2>最近任务</h2>'
                "<table><tr><th>研究</th><th>状态</th><th>观察窗口</th></tr>"
            )
            for job in client.list():
                body += (
                    f'<tr><td><a href="/durable-research/{job["job_id"]}">'
                    + escape(job["request"]["run_id"])
                    + "</a></td><td>"
                    + escape(STATUS[job["status"]])
                    + "</td><td>"
                    + escape(
                        str(job["request"]["payload"]["factors"][0]["window_periods"])
                    )
                    + "</td></tr>"
                )
            body += "</table></section>"
        elif path.startswith("/durable-research/batches/"):
            batch_id = path.removeprefix("/durable-research/batches/")
            batch = client.batch("get", batch_id)
            body += (
                '<section class="panel"><h2>'
                + escape(STATUS[batch["status"]])
                + "</h2>"
                + "<p>项目："
                + escape(batch["request"]["project_id"])
                + "</p>"
                + "<p>"
                + escape(
                    " · ".join(
                        f"{STATUS[state]} {count}"
                        for state, count in batch["counts"].items()
                    )
                )
                + "</p>"
            )
            if batch["retry_of"]:
                body += (
                    '<p>重试来源：<a href="/durable-research/batches/'
                    + escape(batch["retry_of"], quote=True)
                    + '">原始批次</a>。'
                    "原始结果和执行尝试继续保留。</p>"
                )
            if not batch["terminal"]:
                body += f"""<form method="post"
action="/durable-research/batches/{batch_id}/cancel">
{csrf(self.csrf_token)}<button class="btn">取消尚未完成的候选</button></form>"""
            if batch["counts"]["failed"] or batch["counts"]["cancelled"]:
                body += f'''<form method="post"
action="/durable-research/batches/{batch_id}/retry">
{csrf(self.csrf_token)}<input type="hidden" name="key" value="{uuid.uuid4().hex}">
<button class="btn">新建批次重试失败及取消候选</button></form>'''
            body += "<table><tr><th>候选</th><th>状态</th><th>执行尝试</th></tr>"
            for member in batch["members"]:
                job = member["job"]
                body += (
                    "<tr><td>"
                    + escape(member["candidate_id"])
                    + "</td><td>"
                    + escape(STATUS[job["status"]])
                    + "</td><td>"
                    + f'<a href="/durable-research/{job["job_id"]}">结果与历史</a>'
                    + "</td></tr>"
                )
            body += (
                '</table><p><a href="/durable-research">返回后台研究</a></p></section>'
            )
        else:
            job_id = path.removeprefix("/durable-research/")
            job = client.query("get", job_id)
            body += (
                '<section class="panel"><h2>' + escape(STATUS[job["status"]]) + "</h2>"
            )
            if job["status"] in {"queued", "running"}:
                body += f"""<form method="post"
action="/durable-research/{job_id}/cancel">
{csrf(self.csrf_token)}<button class="btn">请求取消</button></form>"""
            if job.get("result"):
                body += (
                    "<h3>研究结果</h3><pre>"
                    + escape(json.dumps(job["result"], ensure_ascii=False, indent=2))
                    + "</pre>"
                )
            body += (
                "<details><summary>执行尝试与事件</summary><pre>"
                + escape(
                    json.dumps(
                        {
                            "attempts": client.query("attempts", job_id),
                            "events": client.query("events", job_id),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                + '</pre></details><p><a href="/durable-research">'
                "返回后台研究</a></p></section>"
            )
        self._send_html(_layout("后台研究", body, "durable"))

    def _durable_post(self, path, data):
        client = configured_client()
        if path == "/durable-research/batch-submit":
            if set(data) != {"snapshot", "windows", "key", "revision"}:
                raise QuantStudioError("后台研究批次参数不完整")
            batch = client.submit_batch(
                *(
                    field(data, key)
                    for key in ("snapshot", "windows", "key", "revision")
                )
            )
            self._redirect(f"/durable-research/batches/{batch['batch_id']}")
        elif path.startswith("/durable-research/batches/"):
            batch_id, separator, action = path.removeprefix(
                "/durable-research/batches/"
            ).partition("/")
            if not separator or (
                action == "cancel"
                and data
                or action == "retry"
                and set(data) != {"key"}
                or action not in {"cancel", "retry"}
            ):
                raise QuantStudioError("后台研究批次操作无效")
            batch = client.batch(
                action, batch_id, field(data, "key") if action == "retry" else None
            )
            self._redirect(f"/durable-research/batches/{batch['batch_id']}")
        elif path == "/durable-research/submit":
            if set(data) != {"snapshot", "window", "key", "revision"}:
                raise QuantStudioError("后台研究参数不完整")
            job = client.submit(
                *(field(data, key) for key in ("snapshot", "window", "key", "revision"))
            )
            self._redirect(f"/durable-research/{job['job_id']}")
        elif path.endswith("/cancel") and not data:
            job_id = path.removeprefix("/durable-research/").removesuffix("/cancel")
            client.query("cancel", job_id)
            self._redirect(f"/durable-research/{job_id}")
        else:
            raise QuantStudioError("后台研究操作不支持")
