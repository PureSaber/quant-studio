"""Human-reviewed research protocol and decisions within the existing project."""

from html import escape

from quant_studio import QuantStudioError
from quant_studio.experiment_context import status_label
from quant_studio.projects import ProjectStore
from quant_studio.research_cards import ACTIONS, FIELDS, ROLES, VERDICTS, DecisionStore
from quant_studio.workbench_web import csrf, field


def options(values, selected=""):
    return "".join(
        f'<option value="{key}"'
        + (" selected" if key == selected else "")
        + f">{escape(label)}</option>"
        for key, label in values.items()
    )


def card_body(root, token, project, section="all"):
    identifier, revision = project["id"], project["revision"]
    card = project.get("research_card") or {}
    registered = (
        "已登记；实验固定提交时的项目版本。" if card else "未登记；旧实验不追认预登记。"
    )
    body = f'''<section class="panel" id="research-card"><h2>研究卡与决策</h2>
<p>选数据 → 研究卡 → 预检运行 → 比较 → 决策 → 模拟跟踪</p>
<p><a href="/catalog">数据目录</a> · <a href="/today">今日运行</a></p>
<p>{registered}</p>
<form method="post" action="/projects/{identifier}/card-save">{csrf(token)}
<input type="hidden" name="revision" value="{revision}">'''
    for key, label in FIELDS.items():
        body += (
            f'<label>{label}<textarea name="{key}" maxlength="4000" required>'
            f"{escape(card.get(key, ''))}</textarea></label>"
        )
    body += f"""<label>样本用途<select name="sample_role">
{options(ROLES, card.get("sample_role", "development"))}</select></label>"""
    for key, label in (("start", "区间开始"), ("end", "区间结束")):
        body += (
            f'<label>{label}<input type="date" name="{key}" required '
            f'value="{escape(card.get(key, ""), quote=True)}"></label>'
        )
    body += '<button class="btn">保存新的研究卡版本</button></form>'
    if section == "design":
        return body + "</section>"
    if section == "decisions":
        body = """<section class="panel">
<h2>正式结论与证据</h2>
<p>正式决策需引用研究卡和相关实验；失败实验也可作为证据。没有证据时，可随时保存上方备注。</p>"""
    body += (
        f'<p><a href="/projects/{identifier}?tab=overview">查看或登记研究设计</a></p>'
    )
    from quant_studio.project_registration import candidates

    candidate_options = '<option value="">暂不提名</option>' + "".join(
        f'<option value="{escape(name, quote=True)}">{escape(name)}</option>'
        for name in sorted({row["candidate"] for row in candidates(root, identifier)})
    )
    body += f'''<h3>记录人工决策</h3><p>提名只记录决定；不会自动创建或切换账户。</p>
<form method="post" action="/projects/{identifier}/decision">{csrf(token)}
<input type="hidden" name="revision" value="{revision}">
<label>结论<select name="verdict">{options(VERDICTS, "inconclusive")}</select></label>
<label>下一步<select name="action">{options(ACTIONS)}</select></label>
<label>理由<textarea name="rationale" maxlength="8000" required></textarea></label>
<label>下次复核日期（可选）<input type="date" name="next_review_date"></label>
<label>提名的已完成原生候选（仅提名时必选）
<select name="candidate">{candidate_options}</select></label>
<label>已配置的模拟账户ID（可选）<input name="account_id"></label><p>引用实验：</p>'''
    for record in ProjectStore(root).experiments(identifier):
        association = "明确项目关联" if record["research_project"] else "历史配方关联"
        body += (
            f'<label class="check"><input type="checkbox" name="evidence" '
            f'value="{record["run_id"]}">{escape(record["title"])} · '
            f"{escape(status_label(record['status']))} · {association}</label>"
        )
    body += '<button class="btn">保存决策与证据引用</button></form>'
    for decision in DecisionStore(root).list(identifier):
        body += (
            f"<article><h3>{VERDICTS[decision['verdict']]} · "
            f"{ACTIONS[decision['action']]}</h3><p>{escape(decision['rationale'])}</p>"
            f"<p>{escape(decision['recorded_at'])} · "
            f"研究卡 {decision['project_revision'][:12]}</p>"
        )
        for ref in decision["evidence"]:
            body += f'<a href="/experiments/{ref["run_id"]}">{ref["run_id"][:12]}</a> '
        if decision["account"]:
            body += (
                f'<p><a href="/accounts/{decision["account"]["source_id"]}">'
                "查看关联模拟账户</a></p>"
            )
        body += "</article>"
    return body + "</section>"


def card_post(root, identifier, revision, action, data):
    store = ProjectStore(root)
    project = store.get(identifier, revision)
    if action == "card-save":
        card = {
            key: field(data, key) for key in (*FIELDS, "sample_role", "start", "end")
        }
        if data:
            raise QuantStudioError("未知研究卡字段")
        return store.save(
            project["name"],
            project["question"],
            project_id=identifier,
            expected=revision,
            datasets=project["datasets"],
            recipes=project["recipes"],
            notes=project["notes"],
            research_card=card,
        )
    values = {key: field(data, key) for key in ("verdict", "action", "rationale")}
    values.update(
        candidate=field(data, "candidate", ""),
        account_id=field(data, "account_id", ""),
        next_review_date=field(data, "next_review_date", ""),
        evidence=data.pop("evidence", []),
    )
    if data:
        raise QuantStudioError("未知决策字段")
    return DecisionStore(root).append(identifier, revision, **values)
