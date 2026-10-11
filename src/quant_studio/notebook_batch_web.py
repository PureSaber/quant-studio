"""Same-project Notebook iteration forms and persistent batch results."""

import json
from datetime import datetime
from html import escape
from urllib.parse import urlencode

from quant_studio import QuantStudioError
from quant_studio.notebook_batches import NotebookBatchStore
from quant_studio.notebook_templates import NotebookTemplateStore
from quant_studio.notebooks import NotebookStore
from quant_studio.projects import ProjectStore
from quant_studio.workbench_web import csrf, field

_STATUS = {
    "preparing": "准备中",
    "prepared": "等待提交",
    "submitted": "已提交",
    "ready": "等待提交",
    "queued": "排队中",
    "running": "运行中",
    "cancelling": "正在取消",
    "cancelled": "已取消",
    "cancel_failed": "未能取消，请查看实际结果",
    "succeeded": "已完成",
    "failed": "失败",
    "interrupted": "已中断",
    "blocked": "未通过检查",
}


def iterations_body(root, token, project_id, query):
    project = ProjectStore(root).get(project_id)
    templates = NotebookTemplateStore(root)
    binding = templates.binding(project_id)
    selected = query.get("template", [binding.get("template", {}).get("id", "")])[0]
    body = f"""<header class="page-head"><h1>Notebook调参与批量实验</h1>
<a href="/projects/{project_id}">返回项目：{escape(project["name"])}</a></header>
<section class="panel"><p>使用本项目已保存草稿，固定同一份数据、代码和环境。
每个参数只填一个值就是单次调参；填写多个值会展开组合。</p>
<form method="get"><label>参数定义<select name="template">"""
    for template in templates.list():
        chosen = "selected" if selected == template["id"] else ""
        body += f'''<option value="{template["id"]}" {chosen}
>{escape(template["name"])}</option>'''
    body += (
        '<option value="">请选择已保存的个人模板</option></select></label>'
        '<button class="btn">选择</button></form>'
    )
    if selected:
        template = templates.get(selected)
        body += f'''<form method="post"
action="/projects/{project_id}/iterations-prepare">{csrf(token)}
<input type="hidden" name="revision" value="{project["revision"]}">
<input type="hidden" name="template" value="{selected}">
<p>草稿代码通过parameters读取值。候选值用JSON数组，例如[5,10,20,60]；文字用["沪深","港股"]。</p>'''
        for key, spec in template["parameters"].items():
            current = binding.get("parameters", {}).get(key, spec["default"])
            value = escape(json.dumps([current], ensure_ascii=False), quote=True)
            body += (
                f'<label>{escape(spec["label"])}<input name="grid_{key}" '
                f'value="{value}" required></label>'
            )
        body += """<label>最多组合数<input name="limit" type="number"
min="1" max="32" value="4" required></label>
<button class="btn primary">冻结输入并预览候选</button></form>"""
    body += "</section>"
    batches = NotebookBatchStore(root)
    for batch in batches.list(project_id):
        created = (
            datetime.fromisoformat(batch["created_at"])
            .astimezone()
            .strftime("%Y-%m-%d %H:%M:%S %z")
        )
        body += f'<section class="panel"><h2>{escape(created)}</h2>'
        body += (
            f"<p>批次：{escape(_STATUS[batch['status']])} · "
            f"{len(batch['candidates'])}组参数</p>"
        )
        for action, label in [
            ("submit", "提交／补全排队"),
            ("cancel", "取消未完成任务"),
        ]:
            if action == "submit" and batch["status"] not in {"prepared", "submitted"}:
                continue
            body += f'''<form method="post"
action="/projects/{project_id}/iterations-{action}">{csrf(token)}
<input type="hidden" name="revision" value="{project["revision"]}">
<input type="hidden" name="batch" value="{batch["id"]}">
<button class="btn">{label}</button></form>'''
        body += (
            '<form method="get" action="/projects/notebook-compare">'
            "<p>选择2至4次实验比较指标：</p>"
        )
        for candidate in batch["candidates"]:
            for number, execution in enumerate(candidate["attempts"], 1):
                record = NotebookStore(root).get(project_id, execution)
                body += f'''<label class="check"><input type="checkbox"
name="run" value="{project_id}:{execution}">
{escape(json.dumps(candidate["parameters"], ensure_ascii=False))}
· 第{number}次 · {escape(_STATUS[record["status"]])}
<a href="/projects/{project_id}/notebook/{execution}">结果与日志</a></label>'''
        body += '<button class="btn">比较选中实验</button></form>'
        for candidate in batch["candidates"]:
            execution = candidate["attempts"][-1]
            if NotebookStore(root).get(project_id, execution)["status"] in {
                "failed",
                "interrupted",
                "cancelled",
                "blocked",
            }:
                body += f'''<form method="post"
action="/projects/{project_id}/iterations-retry">{csrf(token)}
<input type="hidden" name="revision" value="{project["revision"]}">
<input type="hidden" name="batch" value="{batch["id"]}">
<input type="hidden" name="execution" value="{execution}">
<button class="btn">重试
{escape(json.dumps(candidate["parameters"], ensure_ascii=False))}
</button></form>'''
        body += "</section>"
    return body


def iterations_post(handler, project, revision, action, data):
    store = NotebookBatchStore(handler.runs_root)
    if action == "iterations-prepare":
        template = field(data, "template")
        limit = int(field(data, "limit"))
        grid = {
            key.removeprefix("grid_"): json.loads(field(data, key))
            for key in list(data)
            if key.startswith("grid_")
        }
        if data:
            raise QuantStudioError("未知批量参数")
        store.prepare(project, revision, template, grid, max_candidates=limit)
    else:
        identifier = field(data, "batch")
        execution = field(data, "execution") if action == "iterations-retry" else None
        if data:
            raise QuantStudioError("未知批次操作字段")
        if action == "iterations-submit":
            store.submit(project, identifier, handler._jobs())
        elif action == "iterations-cancel":
            store.cancel(project, identifier, handler._jobs())
        elif action == "iterations-retry":
            store.retry(project, identifier, execution, handler._jobs())
        else:
            raise QuantStudioError("未知批次操作")
        template = store.get(project, identifier)["template"]
    handler._redirect(
        f"/projects/{project}/iterations?" + urlencode({"template": template})
    )
