"""Step-oriented project views over the existing immutable stores."""

from html import escape
from urllib.parse import urlencode

from quant_studio import QuantStudioError
from quant_studio.datasets import DatasetStore
from quant_studio.presentation import display_time, status_label
from quant_studio.projects import ProjectStore
from quant_studio.recipes import RecipeStore
from quant_studio.templates import load_template, template_ids
from quant_studio.workbench_web import csrf

TABS = (
    ("overview", "概览"),
    ("data", "数据"),
    ("plan", "方案与运行"),
    ("results", "结果"),
    ("conclusions", "结论"),
)


def new_project_body(token, query=None):
    selected = (query or {}).get("template", ["synthetic-demo"])[0]
    load_template(selected)
    choices = "".join(
        f'<option value="{key}"'
        + (" selected" if key == selected else "")
        + f">{escape(load_template(key).title)}</option>"
        for key in template_ids()
    )
    return f"""<header class="page-head">
<h1>新建研究</h1>
<p>创建项目和第一个方案，然后连接数据、调整参数。</p>
</header>
<section class="panel">
<form method="post" action="/projects/new">{csrf(token)}
<label>项目名称<input name="name" maxlength="120" required></label>
<label>想回答的问题<textarea
name="question"
maxlength="4000" required
placeholder="例如：调整持仓数量会怎样影响回撤？">
</textarea>
</label>
<label>研究模板<select name="template_id">{choices}</select></label>
<p class="note">合成样例只用于熟悉流程。真实研究需要匹配的数据和工具。</p>
<button class="btn primary">创建研究并调整方案</button></form></section>"""


def project_page(root, token, identifier, query):
    from quant_studio.project_web import notebook_body
    from quant_studio.research_card_web import card_body

    store, recipes = ProjectStore(root), RecipeStore(root)
    project = store.get(identifier, query.get("revision", [None])[0])
    tab = query.get("tab", ["overview"])[0]
    if tab not in dict(TABS):
        raise QuantStudioError("未知项目页面")
    target = f"/projects/{identifier}"
    revision = project["revision"]
    historical = store.get(identifier)["revision"] != revision
    scope = f"&revision={revision}" if historical else ""
    tabs = "".join(
        f'<a href="{target}?tab={key}{scope}"'
        + (' aria-current="page"' if tab == key else "")
        + f">{label}</a>"
        for key, label in TABS
    )
    body = f"""<header class="page-head">
<h1>{escape(project["name"])}</h1>
<p>{escape(project["question"])}</p>"""
    if historical:
        body += f'''<p class="notice">正在查看历史项目版本。
<a href="{target}">返回当前版本</a></p>'''
    body += f'</header><nav class="tabs" aria-label="研究步骤">{tabs}</nav>'

    def form(action):
        return f'''<form method="post" action="{target}/{action}">{csrf(token)}
<input type="hidden"
name="revision"
value="{revision}">'''

    if tab == "overview":
        body += f'''<section class="panel">
<h2>下一步</h2>
<p>已连接 {len(project["datasets"])} 个数据集、{len(project["recipes"])} 个方案。</p>
<div class="actions">
<a class="btn"
href="{target}?tab=data{scope}">查看数据</a>
<a class="btn primary"
href="{target}?tab=plan{scope}">调整方案并运行</a>
<a class="btn"
href="{target}?tab=conclusions{scope}">记录想法</a>
</div>
</section>'''
        body += (
            '<details class="panel"><summary>编辑项目名称和研究问题</summary>'
            + form("overview-save")
        )
        body += f'''<label>项目名称<input
name="name"
value="{escape(project["name"], quote=True)}"
maxlength="120" required>
</label>
<label>研究问题<textarea
name="question"
maxlength="4000" required>{escape(project["question"])}</textarea>
</label>
<button class="btn">保存项目</button>
</form>
</details>'''
        body += (
            '<details class="panel"><summary>研究设计与样本约定</summary>'
            + card_body(root, token, project, section="design")
            + "</details>"
        )
        body += '<details class="panel"><summary>项目版本与证据</summary>'
        for index, version in enumerate(reversed(store.history(identifier)), 1):
            body += f'''<p>
<a
href="{target}?revision={version["revision"]}">项目版本 {index}</a>
 ·
{escape(display_time(version["created_at"]))}</p>'''
        body += f'<a href="{target}/evidence">研究证据与复核</a></details>'
        body += '<details class="panel"><summary>研究助手</summary>' + form(
            "assistant-prepare"
        )
        body += """<label>想了解什么<textarea
name="question"
maxlength="4000" required>
</textarea>
</label>
<label class="check">
<input type="checkbox"
name="include_notebook"
value="yes">包含交互研究源码</label>
<button class="btn">准备证据与建议</button>
</form>"""
        from quant_studio.project_assistant import AssistantStore

        for advice in AssistantStore(root).list(identifier):
            body += f'''<p>
<a
href="{target}/advice/{advice["id"]}">研究建议 ·
{escape(advice["created_at"])}</a>

</p>'''
        body += "</details>"
    elif tab == "data":
        body += '<section class="panel"><h2>项目数据</h2>' + form("data-save")
        items = DatasetStore(root).list()
        for item in items:
            checked = " checked" if item["id"] in project["datasets"] else ""
            body += f'''<label class="check">
<input type="checkbox"
name="dataset"
value="{item["id"]}"{checked}>{escape(item["name"])}</label>'''
        body += "<p>尚无已登记数据。合成样例不需要外部数据。</p>" if not items else ""
        body += """<button class="btn">保存数据选择</button> <a
href="/datasets">获取或登记数据</a> · <a
href="/intake">导入文件</a>
</form>
<p class="note">每个方案中选择实际使用的数据集；项目可关联多份数据。</p>
</section>"""
    elif tab == "plan":
        binding = urlencode({"project": identifier, "project_revision": revision})
        body += f"""<section class="panel">
<h2>研究方案</h2>
<p>编辑后运行会保存当前参数；每次运行固定其方案版本。</p>
<a class="btn"
href="/research/new?{binding}">添加方案</a>
</section>"""
        for ref in project["recipes"]:
            record = recipes.get(ref["id"], ref["revision"])
            body += f"""<section class="panel">
<h2>{escape(record["name"])}</h2>
<p>保存于 {escape(display_time(record["created_at"]))}</p>
<a class="btn primary"
href="/research/{ref["id"]}?revision={ref["revision"]}&{binding}">调整参数并运行</a>
"""
            body += "<details><summary>直接运行项目固定的版本</summary>" + form("run")
            body += f'''<input type="hidden"
name="recipe"
value="{ref["id"]}@{ref["revision"]}">
<p>仅使用已保存的版本 <code>{ref["revision"][:12]}</code>。</p>
<button class="btn">检查数据并运行此版本</button>
</form>
</details>
</section>'''
        body += '<details class="panel"><summary>连接已有方案</summary>' + form(
            "recipes-save"
        )
        available = {(r["id"], r["revision"]): r for r in recipes.list()}
        for ref in project["recipes"]:
            available[(ref["id"], ref["revision"])] = recipes.get(
                ref["id"], ref["revision"]
            )
        selected = {(r["id"], r["revision"]) for r in project["recipes"]}
        for (rid, rev), record in available.items():
            checked = " checked" if (rid, rev) in selected else ""
            body += f'''<label class="check">
<input type="checkbox"
name="recipe"
value="{rid}@{rev}"{checked}>{escape(record["name"])} ·
{escape(display_time(record["created_at"]))}</label>'''
        body += '<button class="btn">保存方案连接</button></form></details>'
        body += (
            '<details class="panel"><summary>高级：交互研究与批量实验</summary>'
            + notebook_body(root, token, project)
            + "</details>"
        )
    elif tab == "results":
        body += """<section class="panel">
<h2>运行结果</h2>
<p>可比较配置和失败原因；只有适用的净值曲线参与收益比较。</p>
<form method="get" action="/experiments/compare">"""
        records = store.experiments(identifier)
        for record in records:
            body += f'''<label class="check">
<input type="checkbox"
name="run"
value="{record["run_id"]}">
<a
href="/experiments/{record["run_id"]}">{escape(record["title"])}</a>
 ·
{status_label(record["status"])} ·
{escape(record.get("when", ""))}</label>'''
        body += (
            '<button class="btn">比较所选运行</button>'
            if records
            else "<p>尚无运行记录。可先调整方案，或直接在结论页记录想法。</p>"
        )
        body += f'''</form></section>
<a href="{target}/registration">将候选方案用于模拟跟踪</a>'''
    else:
        body += (
            '<section class="panel"><h2>研究备注</h2>'
            "<p>随时记录想法、放弃原因、失败和证据不足，无需先成功运行。</p>"
            + form("notes-save")
        )
        body += f"""<label>备注<textarea
name="notes" rows="6"
maxlength="16000">{escape(project["notes"])}</textarea>
</label>
<button class="btn primary">保存备注</button>
</form>
</section>"""
        body += card_body(root, token, project, section="decisions")
    return body
