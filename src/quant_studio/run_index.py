"""One read-only index of existing runs, jobs, batches and project history."""

import json
import os
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import urlencode

from quant_studio import QuantStudioError
from quant_studio.experiments import ExperimentStore
from quant_studio.notebook_batches import NotebookBatchStore
from quant_studio.notebooks import NotebookStore
from quant_studio.presentation import STATUS, display_time, status_label, template_title
from quant_studio.projects import ProjectStore
from quant_studio.research_batches import BatchStore
from quant_studio.templates import load_template, template_ids

KINDS = {
    "run": "方案运行",
    "job": "后台任务",
    "batch": "批量实验",
    "notebook": "交互研究",
    "history": "项目版本",
    "durable": "后台因子研究",
}
ACTIVE = {
    "queued",
    "running",
    "checking",
    "cancelling",
    "submitted",
    "preparing",
    "preflight_queued",
    "preflight_running",
    "execution_queued",
    "execution_running",
}


def records(root, jobs=()):
    projects = ProjectStore(root)
    project_rows = projects.list()
    rows = []
    for r in ExperimentStore(root).list():
        project = (r.get("research_project") or {}).get("id")
        related = [
            p["id"]
            for p in project_rows
            if not project
            and any(ref["id"] == r.get("recipe", {}).get("id") for ref in p["recipes"])
        ]
        rows.append(
            {
                **r,
                "kind": "run",
                "link": f"/experiments/{r['run_id']}",
                "projects": related + ([project] if project else []),
                "saved": r.get("created_at") or r.get("when", ""),
                "note": "备注无法读取，打开记录查看诊断"
                if r.get("annotation_error")
                else r["note"],
                "search": str(r),
            }
        )
    for j in jobs:
        project = (j.get("research_project") or {}).get("id") or (
            j.get("notebook") or {}
        ).get("project_id")
        rows.append(
            {
                "kind": "job",
                "link": f"/jobs/{j['job_id']}?inspect=1",
                "title": (j.get("recipe") or {}).get("name")
                or template_title(j.get("template_id", "")),
                "status": j["status"],
                "projects": [project],
                "template_id": j.get("template_id", ""),
                "saved": j.get("created_at", ""),
                "note": "",
                "search": str(j),
            }
        )
    if (Path(root) / ".batches").is_dir():
        for b in BatchStore(root).list():
            related = [
                p["id"]
                for p in project_rows
                if any(ref["id"] == b["recipe"]["id"] for ref in p["recipes"])
            ]
            rows.append(
                {
                    "kind": "batch",
                    "link": f"/batches/{b['batch_id']}",
                    "title": b["recipe"]["name"],
                    "status": b["status"],
                    "projects": related,
                    "template_id": b["recipe"]["template_id"],
                    "saved": b["created_at"],
                    "note": f"{b['candidate_count']} 组候选",
                    "search": str(b),
                }
            )
    notebooks = NotebookStore(root)
    for p in project_rows:
        for version in projects.history(p["id"]):
            rows.append(
                {
                    "kind": "history",
                    "link": f"/projects/{p['id']}?revision={version['revision']}",
                    "title": version["name"],
                    "status": "saved",
                    "projects": [p["id"]],
                    "template_id": "",
                    "saved": version["created_at"],
                    "note": version["notes"],
                    "search": str(version),
                }
            )
        for r in notebooks.list(p["id"]):
            rows.append(
                {
                    "kind": "notebook",
                    "link": f"/projects/{p['id']}/notebook/{r['id']}",
                    "title": p["name"] + " · 交互研究",
                    "status": r["status"],
                    "projects": [p["id"]],
                    "template_id": "notebook",
                    "saved": r["created_at"],
                    "note": "",
                    "search": str(r),
                }
            )
        for b in NotebookBatchStore(root).list(p["id"]):
            statuses = [
                notebooks.get(p["id"], attempt)["status"]
                for c in b["candidates"]
                for attempt in c["attempts"][-1:]
            ]
            state = batch_status(statuses, len(b["candidates"]))
            rows.append(
                {
                    "kind": "batch",
                    "link": f"/projects/{p['id']}/iterations?batch={b['id']}",
                    "title": p["name"] + " · 参数扫描",
                    "status": state,
                    "projects": [p["id"]],
                    "template_id": "notebook",
                    "saved": b["created_at"],
                    "note": f"{len(b['candidates'])} 组候选",
                    "search": str(b),
                }
            )
    if os.environ.get("QUANT_STUDIO_DURABLE_CONFIG"):
        from quant_studio.durable_research import configured_client

        try:
            client = configured_client()
            for job in client.list():
                rows.append(
                    {
                        "kind": "durable",
                        "link": f"/durable-research/{job['job_id']}",
                        "title": "后台因子研究",
                        "status": job["status"],
                        "projects": [job["request"].get("project_id")],
                        "template_id": "",
                        "saved": job.get("created_at", ""),
                        "note": "",
                        "search": str(job),
                    }
                )
            for batch in client.batches() if client.supports_batches() else []:
                rows.append(
                    {
                        "kind": "batch",
                        "link": f"/durable-research/batches/{batch['batch_id']}",
                        "title": "后台因子批次",
                        "status": batch["status"],
                        "projects": [batch["request"].get("project_id")],
                        "template_id": "",
                        "saved": batch.get("created_at", ""),
                        "note": "",
                        "search": str(batch),
                    }
                )
            if getattr(client, "history_limited", False):
                rows.append(
                    {
                        "kind": "durable",
                        "link": "/durable-research",
                        "title": "后台历史需要升级研究工具",
                        "status": "blocked",
                        "projects": [],
                        "template_id": "",
                        "saved": "",
                        "note": (
                            "旧版后台只支持最近30条；升级后台研究工具后可读取完整历史。"
                        ),
                        "notice": True,
                        "search": "",
                    }
                )
        except (QuantStudioError, OSError, ValueError) as exc:
            rows.append(
                {
                    "kind": "durable",
                    "link": "/durable-research",
                    "title": "后台研究暂时无法读取",
                    "status": "unknown",
                    "projects": [],
                    "template_id": "",
                    "saved": "",
                    "note": "打开后台研究查看连接诊断",
                    "search": str(exc),
                }
            )

    def timestamp(row):
        try:
            if type(row["saved"]) in {int, float}:
                return row["saved"]
            return datetime.fromisoformat(row["saved"]).timestamp()
        except (ValueError, TypeError):
            return float("-inf")

    return sorted(rows, key=timestamp, reverse=True)


def index_body(root, token, query, jobs=()):
    choices = {
        key: query.get(key, [""])[0]
        for key in ("q", "status", "template", "project", "kind")
    }
    status_choices = {}
    for code, label in STATUS.items():
        status_choices.setdefault(label, code)
    if choices["status"] in STATUS:
        choices["status"] = status_choices[STATUS[choices["status"]]]
    rows = records(root, jobs)
    notices = [row for row in rows if row.get("notice")]
    rows = [row for row in rows if not row.get("notice")]
    if choices["q"]:
        # Keep the former history search's verified Notebook conditions/results
        # searchable after its route moves to the unified index.
        notebooks = NotebookStore(root)
        for row in rows:
            if row["kind"] == "notebook":
                project, execution = row["link"].split("/")[2::2]
                record = notebooks.get(project, execution)
                for filename in ("inputs.json", "metrics.json", "execution.log"):
                    if filename not in {
                        **record["hashes"],
                        **record.get("artifacts", {}),
                    }:
                        continue
                    path = notebooks.artifact(project, execution, filename)
                    row["search"] += " " + path.read_text("utf-8")[-6000:]
            elif row["kind"] == "run":
                from quant_studio.run_record import load_run_result

                row["search"] += " " + json.dumps(
                    load_run_result(Path(root) / row["run_id"]), ensure_ascii=False
                )
    rows = [
        r
        for r in rows
        if (not choices["kind"] or r["kind"] == choices["kind"])
        and (
            not choices["q"]
            or choices["q"].casefold()
            in (r["title"] + r["note"] + r["search"]).casefold()
        )
        and (
            not choices["status"]
            or (
                r["status"] in ACTIVE
                if choices["status"] == "active"
                else status_label(r["status"]) == STATUS.get(choices["status"])
            )
        )
        and (not choices["template"] or r["template_id"] == choices["template"])
        and (not choices["project"] or choices["project"] in r["projects"])
    ]
    page = max(1, min(int(query.get("page", ["1"])[0]), max(1, (len(rows) + 24) // 25)))

    def select(name, label, values):
        options = '<option value="">全部</option>' + "".join(
            f'<option value="{escape(value, quote=True)}"'
            + (" selected" if choices[name] == value else "")
            + f">{escape(title)}</option>"
            for value, title in values
        )
        return f'<label>{label}<select name="{name}">{options}</select></label>'

    filters = (
        select("kind", "记录类型", KINDS.items())
        + select(
            "status",
            "状态",
            [
                ("active", "进行中"),
                *((code, label) for label, code in status_choices.items()),
            ],
        )
        + select(
            "project", "项目", [(p["id"], p["name"]) for p in ProjectStore(root).list()]
        )
        + select(
            "template",
            "模板",
            [(key, load_template(key).title) for key in template_ids()],
        )
    )
    body = f'''<header class="page-head"><h1>运行记录</h1>
<p>统一搜索方案运行、交互研究、任务、批次和项目版本。失败记录也可比较参数与诊断。</p></header>
<form method="get" class="panel"><div class="recipe-grid"><label>搜索名称、备注或版本
<input name="q" value="{escape(choices["q"], quote=True)}"></label>{filters}</div>
<button class="btn">搜索</button></form>'''
    body += "".join(
        '<p class="notice notice-attention">'
        f'{escape(row["note"])} <a href="{escape(row["link"], quote=True)}">'
        "查看后台工具</a></p>"
        for row in notices
    )
    if not rows:
        return (
            body + '<section class="panel"><h2>还没有匹配的运行记录</h2>'
            "<p>从研究项目运行一个方案后，记录会出现在这里。第一次可使用无需数据的合成样例。</p>"
            '<a class="btn" href="/projects/new">新建研究</a> '
            '<a class="btn" href="/templates/synthetic-demo">'
            "用合成样例试一次</a></section>"
        )
    body += (
        '<section class="panel"><form action="/experiments/compare">'
        '<div class="table-scroll"><table><thead><tr><th>选择</th><th>研究</th>'
        "<th>类型</th><th>状态</th><th>备注</th>"
        '<th title="记录保存的时间，不是行情截止时间">保存时间</th></tr></thead><tbody>'
    )
    for r in rows[(page - 1) * 25 : page * 25]:
        state = r["status"] if r["status"] in STATUS else "unknown"
        checkbox = (
            f'<input type="checkbox" name="run" value="{r["run_id"]}" '
            f'aria-label="选择 {escape(r["title"], quote=True)}">'
            if r["kind"] == "run"
            else "—"
        )
        evidence = (
            "<br><small>合成数据 · 仅用于熟悉流程</small>"
            if r["template_id"] == "synthetic-demo"
            else ""
        )
        body += (
            f'<tr><td>{checkbox}</td><td><a href="{escape(r["link"], quote=True)}">'
            f"{escape(r['title'])}</a>{evidence}</td><td>{KINDS[r['kind']]}</td>"
            f'<td><span class="status {state}">{status_label(r["status"])}</span>'
            f"</td><td>{escape(r['note'][:100])}</td>"
            f"<td>{escape(display_time(r['saved']))}</td></tr>"
        )
    body += (
        '</tbody></table></div><button class="btn primary">比较所选实验</button>'
        '<p>交互研究使用 <a href="/projects/notebook-compare">'
        "Notebook 指标比较</a>。</p></form>"
    )
    links = " · ".join(
        '<a href="/experiments?'
        f'{escape(urlencode({**choices, "page": target}), quote=True)}">{title}</a>'
        for target, title in [
            (max(1, page - 1), "上一页"),
            (min(max(1, (len(rows) + 24) // 25), page + 1), "下一页"),
        ]
    )
    return body + f"<p>共 {len(rows)} 条 · 第 {page} 页 · {links}</p></section>"


def batch_status(statuses, expected):
    if any(s in ACTIVE for s in statuses):
        return "running"
    for terminal in ("cancel_failed", "interrupted", "failed", "blocked", "cancelled"):
        if terminal in statuses:
            return terminal
    if (
        len(statuses) == expected
        and statuses
        and all(s in {"succeeded", "completed"} for s in statuses)
    ):
        return "completed"
    return (
        "prepared"
        if not statuses or all(s == "ready" for s in statuses)
        else "incomplete"
    )
