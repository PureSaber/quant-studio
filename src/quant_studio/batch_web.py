"""HTML and handler mixin for persisted research batches."""

from __future__ import annotations

import csv
import io
import json
from html import escape
from urllib.parse import parse_qs, urlparse

from quant_studio import QuantStudioError
from quant_studio.recipes import RecipeStore
from quant_studio.research_batches import BatchStore
from quant_studio.research_resources import ResourceStore

_STATUS_LABELS = {
    "prepared": "待显式提交",
    "submitted": "已提交",
    "running": "执行中",
    "cancelling": "取消中",
    "cancelled": "已取消",
    "completed": "已完成",
    "interrupted": "服务中断",
    "preflight_queued": "预检排队",
    "preflight_running": "预检中",
    "preflight_failed": "预检失败",
    "execution_queued": "运行排队",
    "execution_running": "运行中",
    "execution_failed": "运行失败",
    "succeeded": "成功",
}


def _csrf(token: str) -> str:
    return (
        '<input type="hidden" name="_csrf_token" value="'
        + escape(token, quote=True)
        + '">'
    )


def batch_list_body(
    batches: list[dict],
    *,
    query: str = "",
    status: str = "",
    token: str = "",
    recipes: list[dict] | None = None,
) -> str:
    rows = []
    for batch in batches:
        summary = batch["summary"]
        rows.append(
            f"""<tr>
<td><a href="/batches/{batch["batch_id"]}">{escape(batch["recipe"]["name"])}</a>
<br><small><code>{batch["recipe"]["revision"][:12]}</code></small></td>
<td>{escape(batch["recipe"]["template_id"])}</td>
<td>{batch["candidate_count"]}</td>
<td>{escape(_STATUS_LABELS.get(batch["status"], batch["status"]))}</td>
<td>{summary["succeeded"]}成功／{summary["failed"]}失败／{summary["cancelled"]}取消</td>
<td>{escape(batch["updated_at"])}</td>
</tr>"""
        )
    status_options = "".join(
        f'<option value="{value}"{" selected" if value == status else ""}>'
        f"{label}</option>"
        for value, label in [
            ("", "全部"),
            ("prepared", "待显式提交"),
            ("running", "执行中"),
            ("completed", "已完成"),
            ("cancelled", "已取消"),
            ("interrupted", "服务中断"),
        ]
    )
    prepare = ""
    if recipes is not None:
        choices = "".join(
            f'<option value="{row["id"]}:{row["revision"]}">'
            f"{escape(row['name'])} · {row['revision'][:12]}</option>"
            for row in recipes
        )
        prepare = f"""<section class="panel">
<h2>预览参数网格</h2>
<p>先计算并展示全部候选；此步骤不会进入任务队列。</p>
<form method="post" action="/batches/prepare">{_csrf(token)}
<label for="batch-recipe">精确方案版本</label>
<select id="batch-recipe" name="recipe" required>{choices}</select>
<label for="batch-grid">参数网格JSON</label>
<textarea id="batch-grid" name="grid" rows="6" required>{{}}</textarea>
<label for="batch-max">最大候选数</label>
<input id="batch-max" name="max_candidates" type="number"
min="1" max="10000" value="100" required>
<label for="batch-wall">墙钟软限额（秒）</label>
<input id="batch-wall" name="wall_seconds" type="number"
min="0.1" step="0.1" value="300" required>
<label for="batch-memory">内存软限额（字节，可选）</label>
<input id="batch-memory" name="memory_bytes" type="number" min="1">
<label for="batch-output">输出软限额（字节，可选）</label>
<input id="batch-output" name="output_bytes" type="number" min="1">
<button class="btn primary">预览候选</button>
</form>
<p class="note">预算由轮询监控执行，不是操作系统强隔离；
采样不可用时会明确显示不可用。</p>
</section>"""
    return f"""<header class="page-head">
<h1>批量实验</h1>
<p>批次固定到一个方案版本。结果按候选与attempt保留，不自动评选策略。</p>
</header>
<section class="panel">
<form method="get" action="/batches">
<label for="batch-query">搜索批次</label>
<input id="batch-query" name="q" value="{escape(query, quote=True)}">
<label for="batch-list-status">批次状态</label>
<select id="batch-list-status" name="status">{status_options}</select>
<button class="btn">筛选</button>
</form>
</section>{prepare}
<section class="panel">
<div class="table-scroll batch-table-scroll"><table>
<thead><tr><th>方案</th><th>模块</th><th>候选数</th><th>状态</th><th>汇总</th><th>更新时间</th></tr></thead>
<tbody>{"".join(rows) or '<tr><td colspan="6">没有匹配的批次。</td></tr>'}</tbody>
</table></div>
</section>"""


def batch_detail_body(
    batch: dict,
    token: str,
    *,
    candidate_status: str = "",
    resource_lookup=None,
) -> str:
    if candidate_status and candidate_status not in set(_STATUS_LABELS):
        raise QuantStudioError("候选状态筛选无效")
    knob_names = list(batch["grid"])
    headings = "".join(f"<th>{escape(name)}</th>" for name in knob_names)
    rows = []
    for candidate in batch["candidates"]:
        if candidate_status and candidate["status"] != candidate_status:
            continue
        attempt = candidate["attempts"][-1] if candidate["attempts"] else None
        usage = _attempt_resource(attempt, resource_lookup)
        knobs = "".join(
            f"<td>{escape(_display(candidate['knobs'][name]))}</td>"
            for name in knob_names
        )
        retry = (
            '<input type="checkbox" name="candidate" '
            f'value="{candidate["candidate_id"]}" '
            f'aria-label="重试候选{candidate["index"]}">'
            if candidate["status"]
            in {"preflight_failed", "execution_failed", "interrupted"}
            else ""
        )
        preflight = _job_link(attempt, "preflight")
        execution = _job_link(attempt, "execution")
        rows.append(
            f"""<tr>
<td>{retry}{candidate["index"]}</td>{knobs}
<td>{escape(_STATUS_LABELS.get(candidate["status"], candidate["status"]))}</td>
<td>{len(candidate["attempts"])}</td>
<td>{preflight}</td><td>{execution}</td>
<td>{_metric(usage, "queue_wait_seconds", "秒")}</td>
<td>{_metric(usage, "running_seconds", "秒")}</td>
<td>{_metric(usage, "peak_rss_bytes", "字节")}</td>
<td>{_metric(usage, "output_bytes", "字节")}</td>
</tr>"""
        )
    options = "".join(
        f'<option value="{value}"{" selected" if candidate_status == value else ""}>'
        f"{label}</option>"
        for value, label in [("", "全部候选")]
        + [
            (key, label)
            for key, label in _STATUS_LABELS.items()
            if key not in {"submitted", "running", "completed", "cancelling"}
        ]
    )
    actions = ""
    if batch["status"] == "prepared":
        actions += f"""<form method="post"
action="/batches/{batch["batch_id"]}/submit">
{_csrf(token)}<input type="hidden" name="submission_key"
value="{batch["batch_id"]}:{batch["revision"]}">
<input type="hidden" name="revision" value="{batch["revision"]}">
<button class="btn primary" aria-label="显式提交全部候选">显式提交全部候选</button>
</form>"""
    if batch["status"] in {"submitted", "running", "cancelling"}:
        actions += f"""<form method="post" action="/batches/{batch["batch_id"]}/cancel">
{_csrf(token)}<input type="hidden" name="revision" value="{batch["revision"]}">
<button class="btn" aria-label="取消剩余候选">取消剩余候选</button>
</form>"""
    if any(
        row["status"] in {"preflight_failed", "execution_failed", "interrupted"}
        for row in batch["candidates"]
    ):
        retry_open = f"""<form method="post"
action="/batches/{batch["batch_id"]}/retry">
{_csrf(token)}<input type="hidden" name="retry_key"
value="retry:{batch["revision"]}">"""
        retry_open += (
            '<input type="hidden" name="revision" value="' + batch["revision"] + '">'
        )
        retry_close = '<button class="btn">为所选失败项创建新attempt</button></form>'
    else:
        retry_open = retry_close = ""
    summary = batch["summary"]
    return f"""<style>
.batch-table-scroll{{overflow-x:auto;max-width:100%}}
.batch-table-scroll table{{min-width:980px}}
@media(max-width:600px){{.batch-table-scroll{{margin-inline:-12px;padding-inline:12px}}}}
</style>
<header class="page-head"><h1>{escape(batch["recipe"]["name"])} · 批量实验</h1>
<p>方案版本<code>{batch["recipe"]["revision"]}</code>；共{batch["candidate_count"]}个候选。</p>
<a href="/batches">返回批次列表</a> ·
<a href="/batches/{batch["batch_id"]}/csv">导出CSV</a>
</header>
<section class="panel"><p>状态：
{escape(_STATUS_LABELS.get(batch["status"], batch["status"]))}；
{summary["succeeded"]}成功，{summary["failed"]}失败，{summary["cancelled"]}取消，{summary["active"]}活动。</p>
<p class="note">资源预算是轮询软限额，不代表系统强隔离。
RSS或输出采样失败时显示“不可用”，不会显示为合格。</p>
<div class="actions">{actions}</div></section>
<section class="panel"><form method="get">
<label for="batch-status-filter">状态筛选</label>
<select id="batch-status-filter" name="candidate_status">{options}</select>
<button class="btn">筛选</button></form></section>
<section class="panel">{retry_open}
<div class="table-scroll batch-table-scroll"><table>
<thead><tr><th>候选</th>{headings}<th>状态</th><th>attempt数</th><th>预检Job</th><th>运行Job</th><th>排队等待</th><th>执行耗时</th><th>RSS峰值</th><th>输出体积</th></tr></thead>
<tbody>{"".join(rows) or '<tr><td colspan="12">没有匹配的候选。</td></tr>'}</tbody>
</table></div>{retry_close}</section>"""


def batch_csv(batch: dict, *, resource_lookup=None) -> str:
    knob_names = list(batch["grid"])
    columns = [
        "batch_id",
        "recipe_revision",
        "candidate_id",
        "candidate_index",
        *knob_names,
        "candidate_status",
        "attempt_no",
        "attempt_status",
        "preflight_job_id",
        "preflight_run_id",
        "execution_job_id",
        "execution_run_id",
        "queue_wait_seconds",
        "running_seconds",
        "peak_rss_bytes",
        "output_bytes",
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for candidate in batch["candidates"]:
        attempts = candidate["attempts"] or [None]
        for attempt in attempts:
            usage = _attempt_resource(attempt, resource_lookup)
            row = {
                "batch_id": batch["batch_id"],
                "recipe_revision": batch["recipe"]["revision"],
                "candidate_id": candidate["candidate_id"],
                "candidate_index": candidate["index"],
                "candidate_status": candidate["status"],
                "attempt_no": attempt["attempt_no"] if attempt else "",
                "attempt_status": attempt["status"] if attempt else "",
                "preflight_job_id": attempt["preflight_job_id"] if attempt else "",
                "preflight_run_id": attempt["preflight_run_id"] if attempt else "",
                "execution_job_id": attempt["execution_job_id"] if attempt else "",
                "execution_run_id": attempt["execution_run_id"] if attempt else "",
                "queue_wait_seconds": (usage or {}).get("queue_wait_seconds", ""),
                "running_seconds": (usage or {}).get("running_seconds", ""),
                "peak_rss_bytes": (usage or {}).get("peak_rss_bytes", ""),
                "output_bytes": (usage or {}).get("output_bytes", ""),
            }
            row.update(
                {name: _csv_cell(candidate["knobs"][name]) for name in knob_names}
            )
            writer.writerow(row)
    return output.getvalue()


class BatchHandler:
    """Mixin; the main handler owns routing, same-origin checks and CSRF parsing."""

    batch_store = None
    batch_supervisor = None

    def _batches(self) -> BatchStore:
        if self.batch_store is None:
            type(self).batch_store = BatchStore(self.runs_root)
        return self.batch_store

    def _batch_get(self, path: str) -> None:
        from quant_studio.server import _layout

        query = parse_qs(urlparse(self.path).query)
        store = self._batches()
        if path == "/batches":
            text = query.get("q", [""])[0]
            status = query.get("status", [""])[0]
            body = batch_list_body(
                store.list(query=text, status=status),
                query=text,
                status=status,
                token=self.csrf_token,
                recipes=RecipeStore(self.runs_root).list(),
            )
            self._send_html(_layout("批量实验", body, "batches"))
            return
        parts = path.removeprefix("/batches/").split("/")
        if len(parts) not in {1, 2}:
            raise QuantStudioError("未知批次路径")
        batch = store.get(parts[0])
        resources = ResourceStore(self.runs_root)

        def lookup(job_id):
            try:
                return resources.get(job_id)
            except QuantStudioError:
                return None

        if len(parts) == 2 and parts[1] == "csv":
            self._send_text(
                batch_csv(batch, resource_lookup=lookup),
                content_type="text/csv; charset=utf-8",
            )
            return
        if len(parts) != 1:
            raise QuantStudioError("未知批次路径")
        candidate_status = query.get("candidate_status", [""])[0]
        body = batch_detail_body(
            batch,
            self.csrf_token,
            candidate_status=candidate_status,
            resource_lookup=lookup,
        )
        self._send_html(_layout("批量实验", body, "batches"))

    def _batch_post(self, path: str, data: dict[str, list[str]]) -> None:
        store = self._batches()
        supervisor = self.__class__.batch_supervisor
        if path == "/batches/prepare":
            recipe_ref = _single(data, "recipe")
            try:
                recipe_id, revision = recipe_ref.split(":", 1)
                grid = json.loads(_single(data, "grid"))
                max_candidates = int(_single(data, "max_candidates"))
                wall = float(_single(data, "wall_seconds"))
                memory = _optional_int(_single(data, "memory_bytes", ""))
                output = _optional_int(_single(data, "output_bytes", ""))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise QuantStudioError("批次预览字段无效") from exc
            if data:
                raise QuantStudioError("未知批次预览字段")
            record = store.prepare(
                recipe_id,
                revision,
                grid,
                max_candidates=max_candidates,
                budget={
                    "wall_seconds": wall,
                    "memory_bytes": memory,
                    "output_bytes": output,
                },
            )
            self._redirect(f"/batches/{record['batch_id']}")
            return
        parts = path.removeprefix("/batches/").split("/")
        if len(parts) != 2:
            raise QuantStudioError("未知批次操作")
        batch_id, action = parts
        if supervisor is None:
            raise QuantStudioError("批次后台协调器尚未启动")
        if action == "submit":
            key = _single(data, "submission_key")
            revision = _single(data, "revision")
            if data:
                raise QuantStudioError("未知批次提交字段")
            record = supervisor.submit(batch_id, submission_key=key, expected=revision)
        elif action == "cancel":
            revision = _single(data, "revision")
            if data:
                raise QuantStudioError("未知批次取消字段")
            record = supervisor.cancel(batch_id, expected=revision)
        elif action == "retry":
            key = _single(data, "retry_key")
            revision = _single(data, "revision")
            candidates = data.pop("candidate", [])
            if data:
                raise QuantStudioError("未知批次重试字段")
            record = supervisor.retry(
                batch_id,
                candidates,
                retry_key=key,
                expected=revision,
            )
        else:
            raise QuantStudioError("未知批次操作")
        self._redirect(f"/batches/{record['batch_id']}")


def _single(data: dict[str, list[str]], key: str, default=None) -> str:
    values = data.pop(key, [default])
    if len(values) != 1 or values[0] is None:
        raise QuantStudioError(f"字段缺少或重复：{key}")
    return values[0]


def _optional_int(value: str) -> int | None:
    return None if value == "" else int(value)


def _display(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _csv_cell(value):
    text = _display(value)
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def _job_link(attempt: dict | None, phase: str) -> str:
    if not attempt or not attempt.get(f"{phase}_job_id"):
        return "未创建"
    job_id = attempt[f"{phase}_job_id"]
    return f'<a href="/jobs/{job_id}"><code>{escape(job_id[:12])}</code></a>'


def _attempt_resource(attempt: dict | None, lookup):
    if not attempt or lookup is None:
        return None
    job_id = attempt.get("execution_job_id") or attempt.get("preflight_job_id")
    return lookup(job_id) if job_id else None


def _metric(usage: dict | None, key: str, unit: str) -> str:
    value = (usage or {}).get(key)
    if value is None:
        return "不可用"
    if isinstance(value, float):
        return f"{value:.3f}{unit}"
    return f"{value}{unit}"


__all__ = [
    "BatchHandler",
    "batch_csv",
    "batch_detail_body",
    "batch_list_body",
]
