"""Project-oriented navigation over datasets, immutable recipes and experiments."""

import json
import os
from html import escape
from urllib.parse import parse_qs, urlencode, urlparse

from quant_studio import QuantStudioError
from quant_studio.data_catalog import catalog, preview_file
from quant_studio.datasets import DatasetStore
from quant_studio.notebooks import NotebookStore, notebook_config
from quant_studio.project_assistant import AssistantStore
from quant_studio.projects import ProjectStore
from quant_studio.recipes import RecipeStore
from quant_studio.workbench_web import csrf, field


def table(headers, rows):
    return (
        '<div class="table-scroll" tabindex="0" role="region" '
        'aria-label="可左右滚动的表格"><table><thead><tr>'
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
        destination = (
            "/projects" if item.get("kind") == "intake" else f"/research/new?{create}"
        )
        body += f"""<section class="panel"><h2><a href="/catalog?{link}">
{escape(item["name"])}</a></h2><p>{escape(item["provider"])} ·
{escape(str(item["start"]))}至{escape(str(item["end"]))}</p>
<p>{escape(item["limits"])}</p>
<a href="{destination}">用此数据开展研究</a>"""
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
    if identifier == "new":
        from quant_studio.project_workspace import new_project_body

        return new_project_body(token, query)
    if identifier is None:
        query = query or {}
        body = """<header class="page-head"><h1>研究项目</h1>
<p>从研究问题出发，把数据、方案、代码、实验和结论放在一起。</p>
<a class="btn primary" href="/projects/new">新建研究</a>
<a class="btn" href="/projects/findings">研究结论</a>
<a class="btn" href="/projects/templates">交互研究模板</a>
<a class="btn" href="/projects/notebook-compare">Notebook实验比较</a></header>
<section class="panel"><form method="get"><label>搜索项目<input name="q"></label>
<button class="btn">搜索</button></form></section>"""
        for project in store.list(query=query.get("q", [""])[0]):
            body += f"""<section class="panel"><h2>
<a href="/projects/{project["id"]}">{escape(project["name"])}</a></h2>
<p>{escape(project["question"])}</p></section>"""
        return body
    from quant_studio.project_workspace import project_page

    return project_page(root, token, identifier, query or {})


def notebook_body(root, token, project):
    from quant_studio.notebook_template_web import template_save_form
    from quant_studio.notebook_templates import NotebookTemplateStore

    store = NotebookStore(root)
    identifier = project["id"]
    body = '<section class="panel"><h2>交互研究与Notebook实验</h2>'
    body += (
        "<p>在JupyterLab编辑草稿；提交后台实验时复制代码与已登记输入，"
        "结果单独保存。</p>"
    )
    binding = NotebookTemplateStore(root).binding(identifier)
    if binding:
        body += f"<p>使用模板：{escape(binding['template']['name'])}</p>"
        body += table(["研究参数", "值"], list(binding["parameters"].items()))
    body += (
        '<p><a href="/projects/templates">从模板开始新研究</a> · '
        '<a href="/projects/notebook-compare">并排比较Notebook实验</a></p>'
    )
    body += (
        f'<p><a class="btn" href="/projects/{identifier}/iterations">'
        "在本项目调参或批量运行</a></p>"
    )
    body += f'''<form method="post" action="/projects/{identifier}/notebook-init">
{csrf(token)}<input type="hidden" name="revision" value="{project["revision"]}">
<label>新草稿起点<select name="template">
<option value="">空白研究草稿</option>
<option value="data-quality-exploration">这份数据能回答什么：分布、缺失与质量</option>
<option value="price-return-missingness">价格收益与观测缺口</option>
<option value="historical-financial-availability">历史财务数据何时真实可得</option>
</select></label>
<p>模板需要连接已通过对应用途检查的数据版本。已有草稿保留原代码。</p>
<button class="btn">创建草稿或刷新数据上下文</button></form>'''
    try:
        link = store.lab_link(identifier)
        mode = (
            "Linux Docker 隔离执行；若宿主不支持会拒绝提交。"
            if notebook_config().get("isolation")
            else "可信本地执行；独立Python环境不提供安全隔离。"
        )
        body += "<p>后台实验模式：" + mode + "</p>"
        body += "<p>JupyterLab交互编辑仍使用已配置的可信本地环境。</p>"
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
    body += "</section>"
    if (store.workspace(identifier) / "research.ipynb").is_file():
        body += template_save_form(root, token, project)
    return body


def notebook_result_body(root, identifier, execution):

    store = NotebookStore(root)
    record = store.get(identifier, execution)
    mode = (
        "Linux Docker 隔离执行"
        if record["runtime"].get("isolation")
        else "可信本地执行（无安全隔离）"
    )
    body = f"""<header class="page-head"><h1>Notebook实验</h1>
<a href="/projects/{identifier}">返回研究项目</a></header><section class="panel">
<p>状态：{escape(record["status"])}</p><p>{escape(record.get("message", ""))}</p>
<p>项目版本：{record["project_revision"]}</p>"""
    body += f"<p>执行模式：{mode}</p>"
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
        from quant_studio.notebook_template_web import (
            comparison_body,
            template_library_body,
        )
        from quant_studio.server import _layout

        query = parse_qs(urlparse(self.path).query)
        if path == "/projects/findings":
            from quant_studio.findings import findings_body

            body, active = findings_body(self.runs_root, query), "findings"
        elif path.endswith("/evidence"):
            from quant_studio.project_evidence_web import evidence_body

            parts = path.removeprefix("/projects/").split("/")
            if len(parts) != 2:
                raise QuantStudioError("未知研究证据页面")
            body, active = (
                evidence_body(self.runs_root, self.csrf_token, parts[0]),
                "projects",
            )
        elif path.endswith("/registration"):
            from quant_studio.project_registration_web import body as registration_body

            body, active = (
                registration_body(
                    self.runs_root, self.csrf_token, path.split("/")[2], query
                ),
                "projects",
            )
        elif path.endswith("/iterations"):
            from quant_studio.notebook_batch_web import iterations_body

            body, active = (
                iterations_body(
                    self.runs_root, self.csrf_token, path.split("/")[2], query
                ),
                "projects",
            )
        elif path == "/projects/templates":
            body, active = (
                template_library_body(self.runs_root, self.csrf_token, query),
                "projects",
            )
        elif path == "/projects/notebook-compare":
            if query.get("download") == ["csv"]:
                from quant_studio.notebook_metrics import (
                    compare_notebooks,
                    comparison_csv,
                )

                content = comparison_csv(
                    compare_notebooks(self.runs_root, query.get("run", []))
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header(
                    "Content-Disposition",
                    'attachment; filename="notebook-comparison.csv"',
                )
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            body, active = comparison_body(self.runs_root, query), "projects"
        elif path == "/catalog":
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
        if path == "/projects/templates/create":
            from quant_studio.notebook_template_web import create_from_template

            project = create_from_template(self.runs_root, data)
            self._redirect(f"/projects/{project['id']}?tab=plan")
            return
        if path == "/projects/new":
            name, question = field(data, "name"), field(data, "question")
            template_id = field(data, "template_id", "")
            if data:
                raise QuantStudioError("未知项目字段")
            for label, value, limit in (
                ("名称", name, 120),
                ("研究问题", question, 4000),
            ):
                if not value.strip() or len(value) > limit:
                    raise QuantStudioError(f"{label}须为非空文本，最多{limit}字")
            refs = []
            if template_id:
                from quant_studio.templates import load_template, render_template

                template = load_template(template_id)
                recipe = RecipeStore(self.runs_root).save(
                    name,
                    template_id,
                    render_template(template).config,
                    cli={
                        knob["name"]: knob["default"]
                        for knob in template.knobs
                        if knob.get("target") == "cli"
                    },
                )
                refs = [{"id": recipe["id"], "revision": recipe["revision"]}]
            project = store.save(name, question, recipes=refs)
            if refs:
                self._redirect(
                    f"/research/{recipe['id']}?project={project['id']}&project_revision={project['revision']}"
                )
                return
        else:
            parts = path.removeprefix("/projects/").split("/")
            if len(parts) != 2:
                raise QuantStudioError("未知项目操作")
            identifier, action = parts
            revision = field(data, "revision")
            if action in {"registration-preview", "registration-confirm"}:
                from quant_studio.project_registration_web import (
                    post as registration_post,
                )

                registration_post(self, identifier, revision, action, data)
                return
            if action in {"card-save", "decision"}:
                from quant_studio.research_card_web import card_post

                card_post(self.runs_root, identifier, revision, action, data)
                self._redirect(
                    f"/projects/{identifier}?tab="
                    + ("conclusions" if action == "decision" else "overview")
                )
                return
            if action == "evidence-review":
                from quant_studio.project_evidence_web import evidence_post

                evidence_post(self, identifier, revision, data)
                return
            if action.startswith("iterations-"):
                from quant_studio.notebook_batch_web import iterations_post

                iterations_post(self, identifier, revision, action, data)
                return
            if action == "template-save":
                from quant_studio.notebook_template_web import save_template

                template = save_template(self.runs_root, identifier, revision, data)
                self._redirect(f"/projects/templates?template={template['id']}")
                return
            if action == "assistant-prepare":
                question = field(data, "question")
                include_notebook = field(data, "include_notebook", "") == "yes"
                if data:
                    raise QuantStudioError("未知助手字段")
                record = AssistantStore(self.runs_root).prepare(
                    identifier, revision, question, include_notebook=include_notebook
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
                template = (
                    field(data, "template", "") or None
                    if action == "notebook-init"
                    else None
                )
                if data:
                    raise QuantStudioError("未知Notebook字段")
                project = store.get(identifier, revision)
                notebooks = NotebookStore(self.runs_root)
                if action == "notebook-init":
                    notebooks.initialize(identifier, revision, template=template)
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
            if action in {"notes-save", "overview-save", "data-save", "recipes-save"}:
                project = store.get(identifier, revision)
                values = {
                    key: project[key]
                    for key in ("name", "question", "notes", "datasets", "recipes")
                }
                tab = {
                    "notes-save": "conclusions",
                    "overview-save": "overview",
                    "data-save": "data",
                    "recipes-save": "plan",
                }[action]
                if action == "notes-save":
                    values["notes"] = field(data, "notes", "")
                elif action == "overview-save":
                    values.update(
                        name=field(data, "name"), question=field(data, "question")
                    )
                elif action == "data-save":
                    values["datasets"] = data.pop("dataset", [])
                else:
                    refs = []
                    for raw in data.pop("recipe", []):
                        parts = raw.split("@")
                        if len(parts) != 2:
                            raise QuantStudioError("方案版本无效")
                        refs.append({"id": parts[0], "revision": parts[1]})
                    values["recipes"] = refs
                if data:
                    raise QuantStudioError("未知项目字段")
                store.save(**values, project_id=identifier, expected=revision)
                self._redirect(f"/projects/{identifier}?tab={tab}")
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
                    research_project={"id": identifier, "revision": revision},
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
    if record.get("notebook_sha256"):
        if file_hash(directory / "source.ipynb") != record["notebook_sha256"]:
            raise QuantStudioError("Notebook源码身份发生变化")
        body += (
            "<h2>将发送的Notebook源码</h2><pre>"
            + escape((directory / "source.ipynb").read_text("utf-8"))
            + "</pre>"
        )
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
        if answer.get("patches"):
            from quant_studio.notebook_proposals import proposal_diff

            changes = proposal_diff(directory, answer["patches"])
            body += (
                "<h2>待审阅修改建议</h2><p>草稿尚未改变。"
                "可在JupyterLab逐项采纳后另建实验验证。</p>"
            )
            body += "<pre>" + escape(changes) + "</pre>"
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
