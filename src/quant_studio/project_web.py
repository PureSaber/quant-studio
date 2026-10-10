"""Project-oriented navigation over datasets, immutable recipes and experiments."""

import json
import os
from html import escape
from urllib.parse import parse_qs, urlencode, urlparse

from quant_studio import QuantStudioError
from quant_studio.data_catalog import catalog, preview_file
from quant_studio.datasets import DatasetStore
from quant_studio.notebooks import NotebookStore
from quant_studio.project_assistant import AssistantStore
from quant_studio.projects import ProjectStore
from quant_studio.recipes import RecipeStore
from quant_studio.workbench_web import csrf, field


def table(headers, rows):
    return (
        '<div class="table-scroll"><table><thead><tr>'
        + "".join(f"<th>{escape(str(h))}</th>" for h in headers)
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in row) + "</tr>"
            for row in rows
        )
        + "</tbody></table></div>"
    )


def catalog_body(root, query):
    search = query.get("q", [""])[0]
    selected = query.get("dataset", [""])[0]
    body = f'''<header class="page-head"><h1>数据目录</h1>
<p>查找数据、查看字段与样本，再进入研究。登记身份与业务适用性分别检查。</p></header>
<section class="panel"><form method="get" action="/catalog">
<label>搜索名称、来源或证券代码
<input name="q" value="{escape(search, quote=True)}"></label>
<button class="btn">搜索</button></form>
<a href="/datasets">采集或登记数据</a></section>'''
    items = (
        [DatasetStore(root).get(selected)] if selected else catalog(root, query=search)
    )
    for item in items:
        link = urlencode({"dataset": item["id"]})
        create = urlencode({"template": item["template_id"], "dataset": item["id"]})
        body += f"""<section class="panel"><h2><a href="/catalog?{link}">
{escape(item["name"])}</a></h2><p>{escape(item["provider"])} ·
{escape(str(item["start"]))}至{escape(str(item["end"]))}</p>
<p>{escape(item["limits"])}</p>
<a href="/research/new?{create}">用此数据创建方案</a>"""
        if selected:
            for name in item["hashes"]:
                file_link = urlencode({"dataset": item["id"], "file": name})
                body += f'<p><a href="/catalog?{file_link}">{escape(name)}</a></p>'
            filename = query.get("file", [None])[0]
            if filename is not None:
                report = preview_file(root, item["id"], filename)
                scope = "全文件" if report["scan_complete"] else "部分样本"
                body += f"""<h3>{escape(filename)}</h3>
<p>已扫描{report["scanned_rows"]}行（{scope}），
其中整行重复{report["duplicate_rows"]}行。</p>"""
                body += table(
                    ["字段", "类型", "含义", "扫描范围内空值数"],
                    [
                        [
                            c["name"],
                            c.get("type", "CSV原始文本"),
                            c["description"],
                            c["missing"],
                        ]
                        for c in report["columns"]
                    ],
                )
                body += "<h3>前20行预览</h3>" + table(
                    [c["name"] for c in report["columns"]], report["rows"]
                )
                body += f"<p>{escape(report['scope'])}</p>"
        body += "</section>"
    return body


def project_body(root, token, identifier=None, query=None):
    store = ProjectStore(root)
    if identifier is None:
        query = query or {}
        body = """<header class="page-head"><h1>研究项目</h1>
<p>从研究问题出发，把数据、方案、代码、实验和结论放在一起。</p></header>
<section class="panel"><form method="get"><label>搜索项目<input name="q"></label>
<button class="btn">搜索</button></form></section>"""
        for project in store.list(query=query.get("q", [""])[0]):
            body += f"""<section class="panel"><h2>
<a href="/projects/{project["id"]}">{escape(project["name"])}</a></h2>
<p>{escape(project["question"])}</p></section>"""
        return (
            body
            + f"""<section class="panel"><h2>新建研究项目</h2>
<form method="post" action="/projects/new">{csrf(token)}
<label>名称<input name="name" maxlength="120" required></label>
<label>想回答的问题
<textarea name="question" maxlength="4000" required></textarea></label>
<button class="btn primary">创建项目</button></form></section>"""
        )
    project = store.get(identifier, (query or {}).get("revision", [None])[0])
    inputs = DatasetStore(root).list()
    recipes = RecipeStore(root)
    recipe_options = {(r["id"], r["revision"]): r for r in recipes.list()}
    for ref in project["recipes"]:
        recipe_options[(ref["id"], ref["revision"])] = recipes.get(
            ref["id"], ref["revision"]
        )
    selected = {(r["id"], r["revision"]) for r in project["recipes"]}
    choices = "".join(
        f'''<label><input type="checkbox" name="dataset" value="{item["id"]}"
{"checked" if item["id"] in project["datasets"] else ""}>
{escape(item["name"])}</label>'''
        for item in inputs
    )
    recipe_choices = "".join(
        f'''<label><input type="checkbox" name="recipe" value="{rid}@{rev}"
{"checked" if (rid, rev) in selected else ""}>
{escape(record["name"])} · {rev[:12]}</label>'''
        for (rid, rev), record in recipe_options.items()
    )
    body = f'''<header class="page-head"><h1>{escape(project["name"])}</h1>
<p>{escape(project["question"])}</p></header><section class="panel">
<form method="post" action="/projects/{identifier}/save">{csrf(token)}
<input type="hidden" name="revision" value="{project["revision"]}">
<label>名称<input name="name" value="{escape(project["name"], quote=True)}"
maxlength="120" required></label>
<label>研究问题<textarea name="question" maxlength="4000" required
>{escape(project["question"])}</textarea></label>
<h2>连接数据集</h2>{choices or "<p>先在数据目录登记输入。</p>"}
<a href="/catalog">打开数据目录</a>
<h2>固定方案版本</h2>{recipe_choices or "<p>还没有方案。</p>"}
<a href="/research">创建或编辑方案</a>
<label>研究笔记与结论<textarea name="notes" rows="6" maxlength="16000"
>{escape(project["notes"])}</textarea></label>
<button class="btn primary">保存新项目版本</button></form></section>'''
    body += """<section class="panel"><h2>运行已连接方案</h2>
<p>每项先执行原生预检，通过后运行；提交时固定方案版本。失败保留诊断。</p>"""
    for ref in project["recipes"]:
        record = recipes.get(ref["id"], ref["revision"])
        body += f'''<form method="post" action="/projects/{identifier}/run">
{csrf(token)}
<input type="hidden" name="revision" value="{project["revision"]}">
<input type="hidden" name="recipe" value="{ref["id"]}@{ref["revision"]}">
<a href="/research/{ref["id"]}?revision={ref["revision"]}">
{escape(record["name"])} · {ref["revision"][:12]}</a>
<button class="btn">预检并运行</button></form>'''
    body += """</section><section class="panel"><h2>项目实验</h2>
<form method="get" action="/experiments/compare">"""
    for record in store.experiments(identifier):
        body += f'''<p><input type="checkbox" name="run" value="{record["run_id"]}">
<a href="/experiments/{record["run_id"]}">{escape(record["title"])}</a>
 · {escape(record["status"])}</p>'''
    body += '<button class="btn">比较所选实验</button></form></section>'
    body += '<section class="panel"><h2>项目版本记录</h2>'
    for version in store.history(identifier):
        body += f"""<p><a href="/projects/{identifier}?revision={version["revision"]}">
{version["revision"][:12]}</a> · {escape(version["created_at"])}</p>"""
    body += "</section>"
    body += notebook_body(root, token, project)
    body += f'''<section class="panel"><h2>研究助手</h2>
<p>根据本项目的数据、方案和实验记录整理问题，先检查证据再选择是否调用模型。</p>
<form method="post" action="/projects/{identifier}/assistant-prepare">{csrf(token)}
<input type="hidden" name="revision" value="{project["revision"]}">
<label>想了解什么<textarea name="question" maxlength="4000" required></textarea></label>
<button class="btn">准备证据与建议</button></form></section>'''
    for advice in AssistantStore(root).list(identifier):
        body += f"""<p><a href="/projects/{identifier}/advice/{advice["id"]}">
研究建议 · {escape(advice["created_at"])}</a> · {escape(advice["status"])}</p>"""
    return body


def notebook_body(root, token, project):
    store = NotebookStore(root)
    identifier = project["id"]
    body = '<section class="panel"><h2>交互研究与Notebook实验</h2>'
    body += (
        "<p>在JupyterLab编辑草稿；提交后台实验时复制代码与已登记输入，"
        "结果单独保存。</p>"
    )
    body += f'''<form method="post" action="/projects/{identifier}/notebook-init">
{csrf(token)}<input type="hidden" name="revision" value="{project["revision"]}">
<button class="btn">创建草稿或刷新数据上下文</button></form>'''
    try:
        link = store.lab_link(identifier)
        body += f'''<p><a href="{escape(link, quote=True)}" target="_blank"
rel="noopener noreferrer">打开JupyterLab</a></p>'''
        body += f'''<form method="post" action="/projects/{identifier}/notebook-run">
{csrf(token)}<input type="hidden" name="revision" value="{project["revision"]}">
<button class="btn primary">冻结已保存草稿并提交实验</button></form>'''
    except QuantStudioError as exc:
        body += f"<p>{escape(str(exc))}</p>"
    for record in store.list(identifier):
        body += f"""<p><a href="/projects/{identifier}/notebook/{record["id"]}">
{escape(record["created_at"])}</a> · {escape(record["status"])}</p>"""
    return body + "</section>"


def notebook_result_body(root, identifier, execution):

    store = NotebookStore(root)
    record = store.get(identifier, execution)
    body = f"""<header class="page-head"><h1>Notebook实验</h1>
<a href="/projects/{identifier}">返回研究项目</a></header><section class="panel">
<p>状态：{escape(record["status"])}</p><p>{escape(record.get("message", ""))}</p>
<p>项目版本：{record["project_revision"]}</p>"""
    for name in record.get("artifacts", {}):
        path = store.artifact(identifier, execution, name)
        if name in {"report.html", "execution.log"}:
            body += f"<h2>{name}</h2>"
            if name == "report.html":
                # Restrict the document even though the worker escapes all text.
                content = escape(path.read_text(encoding="utf-8"), quote=True)
                body += f'''<iframe title="研究输出" sandbox="" srcdoc="{content}"
style="width:100%;height:600px"></iframe>'''
            else:
                body += (
                    f"<pre>{escape(path.read_text(encoding='utf-8')[-20000:])}</pre>"
                )
    body += "<details><summary>代码、数据与环境证据</summary>"
    body += (
        "<pre>"
        + escape(json.dumps(record, ensure_ascii=False, indent=2))
        + "</pre></details>"
    )
    for name in ("source.ipynb", "inputs.json", *record.get("artifacts", {})):
        body += f"""<p><a href="/projects/{identifier}/notebook/{execution}/{name}"
>下载{escape(name)}</a></p>"""
    return body + "</section>"


class ProjectHandler:
    def _project_get(self, path):
        from quant_studio.server import _layout

        query = parse_qs(urlparse(self.path).query)
        if path == "/catalog":
            body, active = catalog_body(self.runs_root, query), "catalog"
        elif "/advice/" in path:
            parts = path.removeprefix("/projects/").split("/")
            if len(parts) != 3 or parts[1] != "advice":
                raise QuantStudioError("未知助手页面")
            body, active = (
                assistant_body(self.runs_root, self.csrf_token, parts[0], parts[2]),
                "projects",
            )
        elif "/notebook/" in path:
            parts = path.removeprefix("/projects/").split("/")
            if len(parts) == 4 and parts[1] == "notebook":
                artifact = NotebookStore(self.runs_root).artifact(
                    parts[0], parts[2], parts[3]
                )
                content = artifact.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header(
                    "Content-Disposition", f'attachment; filename="{artifact.name}"'
                )
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            if len(parts) != 3 or parts[1] != "notebook":
                raise QuantStudioError("未知实验页面")
            body, active = (
                notebook_result_body(self.runs_root, parts[0], parts[2]),
                "projects",
            )
        else:
            identifier = (
                None if path == "/projects" else path.removeprefix("/projects/")
            )
            body, active = (
                project_body(self.runs_root, self.csrf_token, identifier, query),
                "projects",
            )
        self._send_html(_layout("研究工作区", body, active))

    def _project_post(self, path, data):
        store = ProjectStore(self.runs_root)
        if path == "/projects/new":
            name, question = field(data, "name"), field(data, "question")
            if data:
                raise QuantStudioError("未知项目字段")
            project = store.save(name, question)
        else:
            parts = path.removeprefix("/projects/").split("/")
            if len(parts) != 2:
                raise QuantStudioError("未知项目操作")
            identifier, action = parts
            revision = field(data, "revision")
            if action == "assistant-prepare":
                question = field(data, "question")
                if data:
                    raise QuantStudioError("未知助手字段")
                record = AssistantStore(self.runs_root).prepare(
                    identifier, revision, question
                )
                self._redirect(f"/projects/{identifier}/advice/{record['id']}")
                return
            if action == "assistant-run":
                request_id, mode = field(data, "request_id"), field(data, "mode")
                record = AssistantStore(self.runs_root).get(identifier, request_id)
                if (
                    revision != record["context_sha256"]
                    or mode not in {"offline", "model"}
                    or data
                ):
                    raise QuantStudioError("助手证据或提交方式无效")
                model = None
                if mode == "model":
                    model = os.environ.get("QUANT_STUDIO_ASSISTANT_MODEL")
                    if not model or os.environ.get("QUANT_AGENT_LLM_OK") != "1":
                        raise QuantStudioError("尚未配置模型或服务端授权")
                job = self._jobs().submit(
                    "assistant",
                    "research-assistant",
                    {},
                    profile=self._request_profile,
                    assistant={
                        "project_id": identifier,
                        "id": request_id,
                        "model": model,
                    },
                )
                self._redirect(f"/jobs/{job['job_id']}")
                return
            if action in {"notebook-init", "notebook-run"}:
                if data:
                    raise QuantStudioError("未知Notebook字段")
                project = store.get(identifier, revision)
                notebooks = NotebookStore(self.runs_root)
                if action == "notebook-init":
                    notebooks.initialize(identifier, revision)
                    self._redirect(f"/projects/{identifier}")
                else:
                    record = notebooks.prepare(identifier, revision)
                    job = self._jobs().submit(
                        "notebook",
                        "notebook",
                        {},
                        profile=self._request_profile,
                        notebook={"project_id": identifier, "id": record["id"]},
                    )
                    self._redirect(f"/jobs/{job['job_id']}")
                return
            if action == "save":
                name, question, notes = (
                    field(data, "name"),
                    field(data, "question"),
                    field(data, "notes", ""),
                )
                datasets, raw_refs = data.pop("dataset", []), data.pop("recipe", [])
                refs = []
                for raw in raw_refs:
                    components = raw.split("@")
                    if len(components) != 2:
                        raise QuantStudioError("方案版本无效")
                    refs.append(dict(zip(("id", "revision"), components, strict=True)))
                if data:
                    raise QuantStudioError("未知项目字段")
                project = store.save(
                    name,
                    question,
                    project_id=identifier,
                    expected=revision,
                    datasets=datasets,
                    recipes=refs,
                    notes=notes,
                )
            elif action == "run":
                project = store.get(identifier, revision)
                reference = field(data, "recipe")
                if data or reference not in {
                    r["id"] + "@" + r["revision"] for r in project["recipes"]
                }:
                    raise QuantStudioError("只能运行本项目版本连接的方案")
                rid, rev = reference.split("@")
                recipe = RecipeStore(self.runs_root).get(rid, rev)
                job = self._jobs().submit(
                    "workflow",
                    recipe["template_id"],
                    {},
                    snapshot=recipe["snapshot"],
                    profile=self._request_profile,
                    recipe=recipe,
                )
                self._redirect(f"/jobs/{job['job_id']}")
                return
            else:
                raise QuantStudioError("未知项目操作")
        self._redirect(f"/projects/{project['id']}")


def assistant_body(root, token, project_id, identifier):
    from quant_studio.datasets import file_hash

    store = AssistantStore(root)
    record = store.get(project_id, identifier)
    directory = store.directory(project_id, identifier)
    context = json.loads((directory / "context.json").read_text(encoding="utf-8"))
    body = f"""<header class="page-head"><h1>研究助手</h1>
<a href="/projects/{project_id}">返回项目</a></header><section class="panel">
<h2>{escape(context["question"])}</h2><p>{escape(record["scope"])}</p>
<p>状态：{escape(record["status"])} · {escape(record.get("message", ""))}</p>"""
    for evidence in context["evidence"]:
        body += f'''<h3><a href="{escape(record["links"][evidence["id"]], quote=True)}">
{evidence["id"]} · 查看来源</a></h3><pre>{escape(evidence["text"])}</pre>'''
    if record["status"] == "prepared":
        body += f'''<form method="post" action="/projects/{project_id}/assistant-run">
{csrf(token)}<input type="hidden" name="revision" value="{record["context_sha256"]}">
<input type="hidden" name="request_id" value="{identifier}">
<button class="btn" name="mode" value="offline">离线整理证据</button>'''
        model = os.environ.get("QUANT_STUDIO_ASSISTANT_MODEL")
        if model and os.environ.get("QUANT_AGENT_LLM_OK") == "1":
            body += f"""<p>模型：{escape(model)}。点击后发送上方问题与证据摘要。</p>
<button class="btn" name="mode" value="model">同意发送这些内容并生成建议</button>"""
        else:
            body += "<p>在线模型尚未配置，离线整理可独立使用。</p>"
        body += "</form>"
    if record.get("answer_sha256"):
        if file_hash(directory / "answer.json") != record["answer_sha256"]:
            raise QuantStudioError("助手结果完整性核验失败")
        answer = json.loads((directory / "answer.json").read_text(encoding="utf-8"))
        body += f"<h2>建议与证据</h2><p>{escape(answer['scope'])}</p>"
        for finding in answer["findings"]:
            body += "<p>" + escape(finding["text"]) + "</p><p>"
            for citation in finding["citations"]:
                href = escape(record["links"][citation], quote=True)
                body += f'<a href="{href}">{citation}</a> '
            body += "</p>"
        body += (
            "<ol>"
            + "".join(f"<li>{escape(step)}</li>" for step in answer["next_steps"])
            + "</ol>"
        )
    return body + "</section>"
