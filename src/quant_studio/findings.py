"""Read-only index over existing, integrity-checked human research decisions."""

from html import escape

from quant_studio import QuantStudioError
from quant_studio.decision_summary import summary_body
from quant_studio.projects import ProjectStore
from quant_studio.research_cards import ACTIONS, ROLES, VERDICTS, DecisionStore


def findings_body(root, query):
    action = query.get("action", [""])[0]
    if action and action not in ACTIONS:
        raise QuantStudioError("未知研究行动")
    options = '<option value="">全部结论</option>' + "".join(
        f'<option value="{key}"{(" selected" if action == key else "")}>'
        f"{label}</option>"
        for key, label in ACTIONS.items()
    )
    body = f"""<header class="page-head"><h1>研究结论</h1>
<p>汇总已记录的假设、否定证据和后续行动。运行成功不等于假设成立；
每条结论保留当时的研究卡版本和实验依据。</p></header>
<section class="panel"><form method="get"><label>后续行动
<select name="action">{options}</select></label><button class="btn">筛选</button>
</form></section>"""
    projects, decisions = ProjectStore(root), DecisionStore(root)
    daily, daily_read = None, False
    count = 0
    for current in projects.list():
        records = decisions.list(current["id"])
        if not records and not action:
            count += 1
            body += (
                f'<section class="panel"><h2><a href="/projects/{current["id"]}">'
                f"{escape(current['name'])}</a></h2><p>尚未记录结论</p></section>"
            )
        for record in records:
            if action and record["action"] != action:
                continue
            count += 1
            project = projects.get(current["id"], record["project_revision"])
            card = project["research_card"]
            evidence = "".join(
                f'<li><a href="/runs/{ref["run_id"]}">{escape(ref["run_id"])}</a>'
                f" · {escape(ref['status'])}</li>"
                for ref in record["evidence"]
            )
            href = f"/projects/{project['id']}?revision={project['revision']}"
            if record.get("account") and not daily_read:
                from quant_studio.etf_daily import inspect_daily

                daily_read = True
                try:
                    daily = inspect_daily()
                except (QuantStudioError, OSError, ValueError):
                    daily = None
            summary = summary_body(projects.runs_root, record, daily)
            body += f'''<section class="panel"><h2><a href="{href}">
{escape(project["name"])}</a></h2>
<p class="badge">{VERDICTS[record["verdict"]]} · {ACTIONS[record["action"]]}</p>
<p>假设：{escape(card["mechanism"])}</p>
<p>{ROLES[card["sample_role"]]} · {escape(card["start"])} 至 {escape(card["end"])}</p>
<p>否定条件：{escape(card["rejection_rule"])}</p>
<p>结论依据：{escape(record["rationale"])}</p>
{summary}
<p>记录时间：{escape(record["recorded_at"])}</p><ul>{evidence}</ul></section>'''
    if not count:
        body += '<section class="panel"><p>尚无符合条件的研究结论。</p></section>'
    return body
