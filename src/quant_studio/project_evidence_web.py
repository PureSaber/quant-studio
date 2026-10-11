"""Research evidence workbench: read all linked attempts, record explicit review."""

import json
from datetime import UTC, datetime
from html import escape

from quant_studio import QuantStudioError
from quant_studio.project_evidence import project_evidence
from quant_studio.projects import ProjectStore
from quant_studio.workbench_web import csrf, field

STATES = {
    None: "未复核",
    "candidate": "候选",
    "review": "复核中",
    "observe": "研究观察",
    "paused": "暂停研究",
    "retired": "研究淘汰",
}
NEXT = {
    None: ["candidate"],
    "candidate": ["review", "paused", "retired"],
    "review": ["candidate", "observe", "paused", "retired"],
    "observe": ["review", "paused", "retired"],
    "paused": ["review", "retired"],
    "retired": [],
}
VERDICTS = {
    "inconclusive": "暂不能下结论",
    "supports": "证据支持假设",
    "rejects": "证据不支持假设",
    "execution_failed": "执行失败，未检验假设",
}


def evidence_body(root, token, identifier):
    project = ProjectStore(root).get(identifier)
    body = f"""<header class="page-head"><h1>研究证据与复核</h1>
<a href="/projects/{identifier}">返回 {escape(project["name"])}</a>
<p>{escape(project["question"])}</p></header>"""
    try:
        view = project_evidence(root, identifier)
    except QuantStudioError as exc:
        return body + f'<section class="panel"><p>{escape(str(exc))}</p></section>'
    state = view["lifecycle"]
    body += f"""<section class="panel"><h2>研究进度</h2>
<p>研究问题 → 方案／预登记 → 全部尝试 → 证据 → 结论与复核</p>
<p>当前状态：<strong>{STATES.get(state["state"], "未知")}</strong>；
共 {len(view["attempts"])} 项已关联尝试。</p>
<p>观察与暂停只记录研究判断，不改变任何账户或任务的执行权限。</p>
<p>字节核验只说明所引用文件是否一致；统计有效性和业务解释仍需研究者判断。</p>
</section><section class="panel"><h2>方案与预登记</h2>
<p>项目保存属于探索记录；只有显式连接的 Lab 预登记才展示为预登记。</p>"""
    for record in view["registration"]:
        if record.get("study_id"):
            definition = record["definition"]["definition"]
            body += f"<h3>{escape(record['study_id'])}</h3>"
            body += f"<p>{escape(str(definition.get('hypothesis', '')))}</p>"
            body += f"<p>登记候选：{len(definition.get('parameters', []))}；"
            body += f"保留事件：{len(record['events'])}。</p>"
            body += "<details><summary>查看预登记与全部事件</summary><pre>"
            body += escape(json.dumps(record, ensure_ascii=False, indent=2))
            body += "</pre></details>"
    body += '</section><section class="panel"><h2>全部尝试与证据</h2>'
    if not view["attempts"]:
        body += "<p>尚无关联尝试。回到项目提交 Notebook 或运行已连接方案。</p>"
    for row in view["attempts"]:
        label = {"notebook": "Notebook", "native": "原生方案", "lab": "Lab 登记"}.get(
            row["pathway"], row["pathway"]
        )
        body += f"<h3>{escape(label)} · {escape(row['id'])}</h3>"
        body += f"<p>状态：{escape(row['status'])}；"
        body += "证据文件一致" if row["verified"] else "证据需要核对"
        body += "。</p>"
        if row.get("link", "").startswith(("/projects/", "/experiments/", "/jobs/")):
            body += f'<a href="{escape(row["link"], quote=True)}">打开实验与诊断</a>'
        if row.get("message"):
            body += f"<p>{escape(row['message'])}</p>"
        if row.get("association") == "historical_recipe_version":
            body += "<p>历史记录按相同方案版本关联，未声明由本项目提交。</p>"
        for issue in row["issues"]:
            body += f"<p>{escape(str(issue))}</p>"
        body += "<p>未声明统一比较口径的尝试，不合并收益或自动排名。</p>"
    body += "</section>"
    if view["missing"]:
        body += '<section class="panel"><h2>需要核对的来源</h2><ul>'
        body += "".join(f"<li>{escape(str(item))}</li>" for item in view["missing"])
        body += "</ul></section>"
    body += '<section class="panel"><h2>结论与复核记录</h2>'
    for review in reversed(state["reviews"]):
        if review["integrity"] == "invalid":
            body += "<p>历史复核的来源已变化或无法核验，需要重新复核。</p>"
            continue
        body += f"<h3>{VERDICTS.get(review['verdict'], review['verdict'])}</h3>"
        body += f"<p>{escape(review['reason'])}</p>"
        body += f"<p>{escape(review['actor'])} · {escape(review['recorded_at'])}</p>"
        if review["needs_review"]:
            body += (
                "<p><strong>项目、源码、输入或尝试清单已变化，需要复核。</strong></p>"
            )
        else:
            body += "<p>此复核引用的是当前证据版本。</p>"
    options = NEXT.get(state["state"], [])
    if options:
        targets = "".join(f'<option value="{s}">{STATES[s]}</option>' for s in options)
        verdicts = "".join(
            f'<option value="{s}">{v}</option>' for s, v in VERDICTS.items()
        )
        attempts = '<option value="">暂不引用单次结果</option>' + "".join(
            f'<option value="{escape(row["id"], quote=True)}">'
            f"{escape(row['pathway'])} · {escape(row['status'])} · "
            f"{escape(row['id'])}</option>"
            for row in view["attempts"]
        )
        body += f'''<form method="post" action="/projects/{identifier}/evidence-review">
{csrf(token)}<input type="hidden" name="revision" value="{project["revision"]}">
<input type="hidden" name="snapshot" value="{view["snapshot_sha256"]}">
<input type="hidden" name="head" value="{state["head_sha256"] or ""}">
<label>研究状态<select name="state">{targets}</select></label>
<label>判断<select name="verdict">{verdicts}</select></label>
<label>引用证据<select name="attempt">{attempts}</select></label>
<label>复核人<input name="actor" maxlength="120" required></label>
<label>结论、适用范围与理由
<textarea name="reason" maxlength="8000" required></textarea></label>
<p>复核人是本地署名。执行失败不能作为否定假设的证据；支持或否定判断必须引用完成且核验一致的结果。</p>
<button class="btn primary">保存研究复核</button></form>'''
    body += (
        '</section><details class="panel"><summary>证据身份与来源明细</summary><pre>'
    )
    body += escape(json.dumps(view, ensure_ascii=False, indent=2)) + "</pre></details>"
    return body


def evidence_post(handler, identifier, revision, data):
    project = ProjectStore(handler.runs_root).get(identifier)
    if project["revision"] != revision:
        raise QuantStudioError("项目已更新，请刷新证据工作台")
    request = {
        "expected_snapshot": field(data, "snapshot"),
        "expected_head": field(data, "head", "") or None,
        "to_state": field(data, "state"),
        "verdict": field(data, "verdict"),
        "actor": field(data, "actor"),
        "reason": field(data, "reason"),
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    selected = field(data, "attempt", "")
    request["attempt_ids"] = [selected] if selected else []
    if data:
        raise QuantStudioError("未知研究复核字段")
    project_evidence(handler.runs_root, identifier, review=request)
    handler._redirect(f"/projects/{identifier}/evidence")
