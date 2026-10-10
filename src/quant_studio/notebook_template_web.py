"""Personal template forms and side-by-side Notebook research results."""

import json
from html import escape
from urllib.parse import urlencode

from quant_studio import QuantStudioError
from quant_studio.datasets import DatasetStore
from quant_studio.notebook_metrics import compare_notebooks, condition_rows
from quant_studio.notebook_templates import NotebookTemplateStore, parameter_values
from quant_studio.notebooks import NotebookStore
from quant_studio.projects import ProjectStore
from quant_studio.workbench_web import csrf, field


def template_save_form(root, token, project):
    store = NotebookTemplateStore(root)
    binding = store.binding(project["id"])
    schema = store.get(binding["template"]["id"])["parameters"] if binding else {}
    schema_text = escape(json.dumps(schema, ensure_ascii=False, indent=2))
    return f'''<details class="panel"><summary>把当前Notebook保存为研究模板</summary>
<p>保存代码与参数定义，清除旧输出。下次选择模板、数据和参数，会创建新研究项目。</p>
<form method="post" action="/projects/{project["id"]}/template-save">{csrf(token)}
<input type="hidden" name="revision" value="{project["revision"]}">
<label>模板名称<input name="name" maxlength="120" required
value="{escape(project["name"], quote=True)}"></label>
<details><summary>参数定义（模板作者填写一次）</summary>
<p>代码通过<code>parameters["window"]</code>读取表单值；
通过<code>context["datasets"]</code>读取此次选择的数据。
保留空对象表示没有可填参数，代码中原有常量不会自动替换。</p>
<p>例如观察窗口：<code>{{"window": {{"label": "观察窗口", "type": "integer",
"default": 20, "min": 1, "max": 120}}}}</code></p>
<label>参数定义JSON<textarea name="parameters" rows="6" maxlength="20000"
>{schema_text}</textarea></label>
<p>支持integer、number、string、date、boolean、choice；choice需填写choices选项列表。</p>
<p>需要并排比较的结果，在Notebook末尾定义research_metrics。例如：</p>
<pre>research_metrics = {{
    "样本数": {{"value": len(data), "unit": "行", "definition": "实际参与统计的记录数"}}
}}</pre></details>
<button class="btn">保存为新模板版本</button></form></details>'''


def save_template(root, identifier, revision, data):
    name = field(data, "name")
    raw = field(data, "parameters", "{}")
    if data:
        raise QuantStudioError("未知模板字段")
    try:
        schema = json.loads(raw)
    except ValueError as exc:
        raise QuantStudioError("参数定义不是有效JSON") from exc
    return NotebookTemplateStore(root).save(identifier, revision, name, schema)


def _parameter_input(key, spec):
    default = spec["default"]
    name = "param_" + key
    label = escape(spec["label"])
    kind = spec["type"]
    if kind in {"choice", "boolean"}:
        options = (
            [(v, v) for v in spec["choices"]]
            if kind == "choice"
            else [("true", "是"), ("false", "否")]
        )
        selected = str(default).lower() if kind == "boolean" else default
        control = (
            f'<select name="{name}">'
            + "".join(
                f'<option value="{escape(v, quote=True)}" '
                f"{'selected' if v == selected else ''}>{escape(text)}</option>"
                for v, text in options
            )
            + "</select>"
        )
    else:
        input_type = {"date": "date", "integer": "number", "number": "number"}.get(
            kind, "text"
        )
        bounds = " ".join(
            f'{bound}="{spec[bound]}"' for bound in ("min", "max") if bound in spec
        )
        step = (
            'step="any"'
            if kind == "number"
            else 'step="1"'
            if kind == "integer"
            else ""
        )
        control = (
            f'<input name="{name}" type="{input_type}" '
            f'value="{escape(str(default), quote=True)}" {bounds} {step} '
            f'maxlength="2000" {"required" if kind != "string" else ""}>'
        )
    return f"<label>{label}{control}</label>"


def template_library_body(root, token, query):
    store = NotebookTemplateStore(root)
    selected = query.get("template", [None])[0]
    search = query.get("q", [""])[0]
    body = """<header class="page-head"><h1>研究模板库</h1>
<p>选模板 → 选数据、填参数 → 创建研究 → 运行并比较。</p>
<a href="/projects">返回研究项目</a></header>"""
    if not selected:
        body += f'''<section class="panel"><form method="get"><label>搜索模板
<input name="q" value="{escape(search, quote=True)}"></label>
<button class="btn">搜索</button></form></section>'''
        matches = [
            t
            for t in store.list()
            if search.casefold() in (t["name"] + t["question"]).casefold()
        ]
        for template in matches:
            body += f"""<section class="panel"><h2>{escape(template["name"])}</h2>
<p>{escape(template["question"])}</p><p>{len(template["parameters"])}个可填参数 ·
{escape(template["created_at"])}</p>
<a class="btn primary" href="/projects/templates?template={template["id"]}"
>用此模板开始研究</a></section>"""
        if not matches:
            body += (
                '<section class="panel"><p>还没有匹配的模板。'
                "在研究项目中保存Notebook后，"
                "选择“把当前Notebook保存为研究模板”。</p></section>"
            )
        return body
    template = store.get(selected)
    body += f'''<section class="panel"><h2>{escape(template["name"])}</h2>
<p>{escape(template["question"])}</p>
<form method="post" action="/projects/templates/create">{csrf(token)}
<input type="hidden" name="template" value="{template["id"]}">
<input type="hidden" name="revision" value="{template["revision"]}">
<label>新研究名称<input name="name" required maxlength="120"
value="{escape(template["name"][:114] + " · 新研究", quote=True)}"></label>
<h3>选择已登记数据</h3><p>数据选择次序按目录顺序保存；具体读取、筛选和计算由模板代码决定。</p>'''
    datasets = DatasetStore(root).list()
    for item in datasets:
        body += f'''<label class="check"><input type="checkbox" name="dataset"
value="{item["id"]}">{escape(item["name"])} ·
{escape(str(item.get("start", "")))}至{escape(str(item.get("end", "")))}</label>'''
    if not datasets:
        body += (
            "<p>当前没有登记的数据，纯计算研究可以不选数据。"
            '<a href="/catalog">打开数据目录</a></p>'
        )
    body += "<h3>填写研究参数</h3>"
    for key, spec in template["parameters"].items():
        body += _parameter_input(key, spec)
    if not template["parameters"]:
        body += "<p>此模板没有需要填写的参数。</p>"
    body += """<button class="btn primary">创建研究项目</button></form>
<p>创建后可直接提交实验，也可在JupyterLab继续编辑；原草稿和历史实验保留。</p></section>"""
    source_link = (
        f"/projects/{template['source']['project_id']}"
        f"?revision={template['source']['revision']}"
    )
    body += f"""<details class="panel"><summary>查看模板代码与来源</summary>
<p>保存时间：{escape(template["created_at"])}；版本：{template["revision"]}</p>
<a href="{source_link}">打开来源项目版本</a>"""
    for cell in template["notebook"]["cells"]:
        body += "<pre>" + escape("".join(cell["source"])) + "</pre>"
    return body + "</details>"


def create_from_template(root, data):
    identifier, revision, name = (
        field(data, "template"),
        field(data, "revision"),
        field(data, "name"),
    )
    datasets = data.pop("dataset", [])
    store = NotebookTemplateStore(root)
    template = store.get(identifier)
    values = {
        k: field(data, "param_" + k)
        for k in template["parameters"]
        if "param_" + k in data
    }
    if data:
        raise QuantStudioError("未知模板参数")
    return store.instantiate(
        identifier,
        revision,
        name,
        datasets,
        parameter_values(template["parameters"], values, form=True),
    )


def comparison_body(root, query):
    from quant_studio.project_web import table

    selected = query.get("run", [])
    body = """<style>
.notebook-comparison table{table-layout:fixed;min-width:680px;width:100%}
.notebook-comparison th,.notebook-comparison td{
white-space:normal;overflow-wrap:anywhere}
</style><div class="notebook-comparison">
<header class="page-head"><h1>Notebook实验比较</h1>
<p>先核对条件，再看指标。缺失、失败或不同口径不会被当成可排名的数值。</p>
<a href="/projects">返回研究项目</a> ·
<a href="/projects/templates">打开模板库</a></header>"""
    if len(selected) >= 2:
        result = compare_notebooks(root, selected)
        names = [r["name"] + " · " + r["id"][:8] for r in result["records"]]
        body += '<section class="panel"><h2>实验条件</h2>'
        for warning in result["warnings"]:
            body += "<p><strong>请留意：</strong>" + escape(warning) + "</p>"
        body += (
            "<p>数据范围来自登记记录；Notebook内部的二次筛选应在参数和指标口径中说明。"
            "这里不自动评定优劣。</p>"
        )
        body += table(["条件", *names], condition_rows(result, compact=True))
        body += "<details><summary>完整数据、代码与环境身份</summary>"
        body += table(["条件", *names], condition_rows(result)) + "</details></section>"
        body += '<section class="panel"><h2>关键指标</h2>'
        body += table(["指标", "单位", "统计口径", *names], result["rows"])
        if not result["rows"]:
            body += (
                "<p>所选实验尚无可用指标。请在Notebook中声明research_metrics"
                "并重新运行。</p>"
            )
        for record in result["records"]:
            href = f"/projects/{record['project_id']}/notebook/{record['id']}"
            body += f"""<p><a href="{href}"
>查看{escape(record["name"])}的完整实验</a></p>"""
        link = urlencode({"run": selected, "download": "csv"}, doseq=True)
        body += (
            '<a class="btn" '
            f'href="/projects/notebook-compare?{escape(link, quote=True)}"'
            ">下载条件与指标CSV</a></section>"
        )
    body += (
        '<section class="panel"><h2>选择2至4个实验</h2>'
        '<form method="get" action="/projects/notebook-compare">'
    )
    for project in ProjectStore(root).list():
        for record in NotebookStore(root).list(project["id"]):
            if "inputs.json" not in record["hashes"]:
                continue
            reference = project["id"] + ":" + record["id"]
            body += f'''<label class="check"><input type="checkbox" name="run"
value="{reference}" {"checked" if reference in selected else ""}>
{escape(project["name"])} · {escape(record["created_at"])} ·
{escape(record["status"])}</label>'''
    return (
        body
        + '<button class="btn primary">比较所选实验</button></form></section></div>'
    )
