"""Explicit project selection, immutable preview and recoverable confirmation UI."""

from html import escape

from quant_studio import QuantStudioError
from quant_studio import project_registration as registration
from quant_studio.etf_daily_web import details, local_time
from quant_studio.projects import ProjectStore, digest
from quant_studio.workbench_web import csrf, field


def body(root, token, project_id, query):
    project = ProjectStore(root).get(project_id)
    base = f"/projects/{project_id}"
    page = (
        "<style>.registration-page{overflow-wrap:anywhere;min-width:0}"
        ".registration-page select{max-width:100%}</style>"
        '<div class="registration-page">'
        f"<h1>模拟账户登记 · {escape(project['name'])}</h1>"
    )
    page += f'<a href="{base}">返回项目</a>'
    page += (
        "<p>登记冻结一个已完成候选。新增账户独立核验；"
        "每日调度需另行配置，当前调度账户保持原绑定。</p>"
    )
    try:
        registration.configuration(root)
    except QuantStudioError as exc:
        return (
            page
            + f'<p role="alert">{escape(str(exc))}</p>'
            + '<a href="/delivery">检查运行环境</a></div>'
        )
    preview_id = query.get("preview", [""])[0]
    if not preview_id:
        choices = registration.candidates(root, project_id)
        if choices:
            options = "".join(
                f'<option value="{digest(row)}">{escape(row["title"])} · '
                f"{escape(row['run_id'])} · {escape(row['candidate'])}</option>"
                for row in choices
            )
            page += f'''<section class="panel"><h2>选择已完成的原生候选</h2>
<form method="post" action="{base}/registration-preview">{csrf(token)}
<input type="hidden" name="revision" value="{project["revision"]}">
<label>实验与候选<select name="selected">{options}</select></label>
<label>首个观察日<input type="date" name="start" required></label>
<label>窗口截止<input type="date" name="end" required></label>
<p>起点须为未来已知交易日，且晚于研究结束日；资金和成本取自冻结配方。</p>
<button class="btn">检查前置条件并生成预览</button></form></section>'''
        else:
            page += (
                "<p>暂无可登记候选。请在本项目完成研究卡、连接方案并运行完整候选族；"
                "失败实验保留在项目实验中。</p>"
            )
        for record in registration.invoke(root, "list", {"project_id": project_id})[
            "previews"
        ]:
            page += (
                f'<p><a href="{base}/registration?preview={record["id"]}">'
                f"{escape(record['account_id'])} · "
                f"{escape(local_time(record['created_at']))}</a></p>"
            )
        return page + "</div>"
    value = registration.inspect(root, project_id, preview_id)
    plan, request = value["plan"], value["request"]
    definition = plan["definition"]
    receipt = value.get("receipt") or {}
    state = receipt.get("state", "prepared")
    labels = {
        "prepared": "待人工确认",
        "registering": "登记中或上次登记中断，请核验任务后恢复",
        "completed": "登记完成",
        "recoverable_failure": "登记失败，可核验后恢复",
    }
    page += '<section class="panel"><h2>冻结预览</h2>'
    page += (
        f"<p>状态：{escape(labels.get(state, state))}；有效期至 "
        f"{escape(local_time(value['expires_at']))}</p>"
        f"<p>账户：{escape(value['account_id'])}；候选：{escape(request['candidate'])}；"
        "初始模拟资金："
        f"{escape(str(definition['recipe']['costs']['initial_capital']))}</p>"
        f"<p>观察区间：{escape(request['start'])} 至 {escape(request['end'])}；"
        f'来源实验：<a href="/runs/{escape(request["run_id"], quote=True)}">'
        f"查看原生实验</a>；项目版本：{escape(request['revision'])}</p>"
        "<p>前置条件已通过：完整候选族、原生证据、输入快照、代码身份、"
        "未来交易日、当前行情及研究预检。确认时重新核验；过期或变更需重新预览。</p>"
    )
    page += details(plan["prerequisites"], "原生前置检查结果") + details(
        definition, "完整冻结配方、输入、成本与代码版本"
    )
    if receipt.get("error"):
        page += f'<p role="alert">{escape(receipt["error"])}</p>'
    if value.get("inspection_error"):
        page += '<p role="alert">当前账户核验未通过：'
        page += escape(value["inspection_error"]) + "</p>"
    if value.get("inspection"):
        page += "<h2>当前账户独立核验</h2><p>尚未接管任何每日调度。</p>"
        page += details(value["inspection"], "账户身份、观察和账本证据")
    if state != "completed":
        page += f'''<form method="post" action="{base}/registration-confirm">
{csrf(token)}
<input type="hidden" name="revision" value="{request["revision"]}">
<input type="hidden" name="preview_id" value="{escape(preview_id, quote=True)}">
<label><input type="checkbox" name="confirmed" value="yes" required>
我已审阅冻结配方和观察窗口，确认仅登记模拟账户；重复提交复用同一账户。</label>
<button class="btn">确认登记 / 核验并恢复</button></form>'''
    page += f'<p><a href="{base}/registration">重新选择并预览</a> · '
    page += '<a href="/jobs">检查登记任务及中断记录</a></p>'
    return page + details(receipt, "登记回执与失败记录") + "</section></div>"


def post(handler, project_id, revision, action, data):
    if action == "registration-preview":
        selected, start, end = (
            field(data, "selected"),
            field(data, "start"),
            field(data, "end"),
        )
        if data:
            raise QuantStudioError("未知登记字段")
        value = registration.prepare(
            handler.runs_root, project_id, revision, selected, start, end
        )
        handler._redirect(f"/projects/{project_id}/registration?preview={value['id']}")
        return
    preview_id, confirmed = field(data, "preview_id"), field(data, "confirmed", "")
    if data or confirmed != "yes":
        raise QuantStudioError("请明确确认已审阅的模拟登记预览")
    value = registration.inspect(handler.runs_root, project_id, preview_id)
    if value["request"]["revision"] != revision:
        raise QuantStudioError("确认与预览版本不一致")
    payload = registration.queue_payload(handler.runs_root, project_id, preview_id)
    jobs = handler._jobs()
    retries = sum(
        record.get("collection") == payload
        and record["status"] not in {"queued", "running", "cancelling", "succeeded"}
        for record in jobs.list()
    )
    job = jobs.submit(
        "collect",
        "etf-weekly-research",
        {},
        profile=handler._request_profile,
        collection=payload,
        dispatch_key=digest({"payload": payload, "retry": retries}),
    )
    handler._redirect(f"/jobs/{job['job_id']}")
