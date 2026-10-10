"""Upload, map, inspect and publish explicit research data versions."""

import json
from html import escape
from urllib.parse import parse_qs, urlencode, urlparse

from quant_studio import QuantStudioError
from quant_studio.intake_tools import PURPOSES, IntakeWorkspace, contract_from_form
from quant_studio.project_web import table
from quant_studio.recipes import _atomic
from quant_studio.workbench_web import csrf, field

_STATUS_LABELS = {
    "prepared": "映射已确认，等待提交",
    "queued": "已排队，等待前序任务",
    "running": "正在执行全量检查",
    "succeeded": "已通过并发布",
    "blocked": "质量检查未通过",
    "failed": "导入失败，可查看原因后重试",
    "interrupted": "服务中断，原请求已保留",
    "cancelled": "已取消，原请求已保留",
}


def _data(response):
    if not response.get("ok"):
        error = response.get("error", {})
        raise QuantStudioError(str(error.get("message", "数据操作未通过")))
    return response["data"]


def _error_message(response, default="数据操作未通过"):
    if not isinstance(response, dict):
        return default
    error = response.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or default)
    if error:
        return str(error)
    return str(response.get("message") or default)


def _select(name, values, selected=""):
    return (
        f'<select name="{escape(name)}">'
        + "".join(
            f'<option value="{escape(value, quote=True)}"'
            f"{' selected' if value == selected else ''}>{escape(label)}</option>"
            for value, label in values
        )
        + "</select>"
    )


def _input(name, label, value="", hint=""):
    return (
        f'<label>{escape(label)}<input name="{escape(name, quote=True)}" '
        f'value="{escape(value, quote=True)}"><small>{escape(hint)}</small></label>'
    )


def _format_value(value):
    if isinstance(value, (list, tuple)):
        return "、".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is True:
        return "是"
    if value is False:
        return "否"
    return "" if value is None else str(value)


def _retry_body(uploaded, identifier, encoding, delimiter, message):
    encodings = _select(
        "encoding",
        [("utf-8-sig", "UTF-8（含BOM）"), ("utf-8", "UTF-8"), ("gb18030", "GB18030")],
        encoding,
    )
    delimiters = _select(
        "delimiter",
        [(",", "逗号"), ("\t", "制表符"), (";", "分号"), ("|", "竖线")],
        delimiter,
    )
    return f"""<header class="page-head"><h1>原件解析需要调整</h1>
<p>{escape(uploaded["filename"])} · 原件已留存 ·
SHA-256 <code>{escape(uploaded["sha256"])}</code></p></header>
<section class="panel"><p role="alert">{escape(message)}</p>
<h2>调整编码或分隔符后重试</h2>
<p>重试只重新读取同一份原件，不会覆盖或另存上传内容。</p>
<form method="get" action="/intake/map/{identifier}">
<div class="recipe-grid"><label>文本编码{encodings}</label>
<label>分隔符{delimiters}</label></div>
<button class="btn primary">重新解析此原件</button></form>
<p><a href="/intake">返回原件与导入记录</a></p></section>"""


def _request_contract(store, identifier):
    store.request(identifier)
    try:
        value = json.loads(
            (store.root / "requests" / identifier / "contract.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError, TypeError) as exc:
        raise QuantStudioError("旧映射契约无法读取") from exc
    if not isinstance(value, dict) or not isinstance(value.get("mapping"), dict):
        raise QuantStudioError("旧映射契约格式无效")
    return value


def _preview_columns(store, upload_id):
    try:
        preview = json.loads(
            (store.root / "incoming" / upload_id / "preview.json").read_text(
                encoding="utf-8"
            )
        )
        columns = [column["name"] for column in preview["columns"]]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise QuantStudioError("旧原件字段记录无法读取，请重新确认映射") from exc
    if not columns or not all(isinstance(column, str) for column in columns):
        raise QuantStudioError("旧原件字段记录无效")
    return columns


def _published_version(record):
    response = record.get("response")
    data = response.get("data") if isinstance(response, dict) else None
    version = data.get("version") if isinstance(data, dict) else None
    if isinstance(version, dict):
        return str(version.get("id") or "")
    return str(version or "")


def _refresh_options(store):
    options = []
    for record in store.request_list():
        version = _published_version(record)
        if record.get("status") == "succeeded" and version:
            options.append((record, version))
    return options


def _refresh_context(store, identifier, columns):
    record = store.request(identifier)
    version = _published_version(record)
    if record.get("status") != "succeeded" or not version:
        raise QuantStudioError("只能复用已发布数据版本的确认映射")
    contract = _request_contract(store, identifier)
    old_columns = _preview_columns(store, record["upload_id"])
    added = [column for column in columns if column not in old_columns]
    removed = [column for column in old_columns if column not in columns]
    reordered = not added and not removed and old_columns != columns
    return {
        "record": record,
        "version": version,
        "contract": contract,
        "old_columns": old_columns,
        "changed": bool(added or removed or reordered),
        "added": added,
        "removed": removed,
        "reordered": reordered,
    }


def upload_body(token):
    encodings = _select(
        "encoding",
        [("utf-8-sig", "UTF-8（含BOM）"), ("utf-8", "UTF-8"), ("gb18030", "GB18030")],
    )
    delimiters = _select(
        "delimiter", [(",", "逗号"), ("\t", "制表符"), (";", "分号"), ("|", "竖线")]
    )
    return f"""<section class="panel"><h2>导入自己的数据</h2>
<p>上传CSV、TSV或Parquet，先查看原始字段，再明确映射和用途。单文件上限24MiB；大文件使用QDK命令行。</p>
<form method="post" action="/intake/upload" id="upload-form">{csrf(token)}
<label>数据文件<input id="data-file" type="file"
accept=".csv,.tsv,.parquet" required></label>
<input type="hidden" name="filename"><input type="hidden" name="content">
<div class="recipe-grid"><label>文本编码{encodings}</label>
<label>分隔符{delimiters}</label></div>
<button class="btn primary">上传并查看字段</button>
<p id="upload-error" role="alert"></p></form>
<script>document.getElementById('upload-form').addEventListener('submit',function(event){{
event.preventDefault();const form=this;
const file=document.getElementById('data-file').files[0];
const error=document.getElementById('upload-error');
if(!file||file.size>24*1024*1024){{
error.textContent='请选择24MiB以内的文件';return;}}
const reader=new FileReader();reader.onerror=()=>{{
error.textContent='文件读取失败，原件没有修改';}};
reader.onload=()=>{{form.elements.filename.value=file.name;
form.elements.content.value=String(reader.result).split(',')[1];form.submit();}};
reader.readAsDataURL(file);}});</script></section>"""


def intake_body(store, token):
    body = (
        '<header class="page-head"><h1>数据接入与质量</h1>'
        "<p>原件留存、格式转换和研究用途分别核验。未通过的数据保留问题记录。</p></header>"
    )
    body += upload_body(token)
    body += '<section class="panel"><h2>已留存原件</h2>'
    try:
        uploads = store.upload_list()
        if not uploads:
            body += "<p>尚未上传原件。</p>"
        for item in uploads:
            body += (
                f'<p><a href="/intake/map/{item["id"]}">'
                f"{escape(item['filename'])}</a> · {item['bytes']}字节 · "
                f"<code>{escape(item['sha256'][:16])}</code><br>"
                f"<small>原件标识：{escape(item['id'])}</small></p>"
            )
    except QuantStudioError as exc:
        body += f'<p role="alert">原件列表暂不可读：{escape(str(exc))}</p>'
    body += "</section>"
    try:
        datasets = _data(store.backend("list"))["datasets"]
        body += '<section class="panel"><h2>已发布数据版本</h2>'
        for item in datasets:
            link = urlencode(
                {"name": item["name"], "version": item.get("latest") or ""}
            )
            body += (
                f'<p><a href="/intake/view?{link}">{escape(item["name"])}</a>'
                f" · {item.get('version_count', 0)}个版本</p>"
            )
        body += "</section>"
    except QuantStudioError as exc:
        body += (
            f'<p class="banner">{escape(str(exc))}。<a href="/setup">配置环境</a></p>'
        )
    body += '<section class="panel"><h2>导入尝试</h2>'
    requests = store.request_list()
    if not requests:
        body += "<p>尚未提交字段映射。</p>"
    for item in requests:
        status = _STATUS_LABELS.get(item["status"], item["status"])
        body += (
            f'<p><a href="/intake/requests/{item["id"]}">'
            f"{escape(item['name'])}</a> · {escape(status)}"
        )
        if _published_version(item):
            body += "<br><small>上传新原件后，可在字段页选择复用此映射。</small>"
        body += "</p>"
    return body + "</section>"


def mapping_body(
    store,
    token,
    identifier,
    *,
    encoding="utf-8-sig",
    delimiter=",",
    reuse_request=None,
):
    uploaded = store.upload_record(identifier)
    try:
        response = store.inspect(identifier, encoding=encoding, delimiter=delimiter)
    except QuantStudioError as exc:
        return _retry_body(uploaded, identifier, encoding, delimiter, str(exc))
    if not isinstance(response, dict) or not response.get("ok"):
        return _retry_body(
            uploaded,
            identifier,
            encoding,
            delimiter,
            _error_message(response, "无法解析此原件"),
        )
    preview = response.get("data")
    try:
        columns = [column["name"] for column in preview["columns"]]
        sample_rows = preview.get("rows", [])
    except (AttributeError, KeyError, TypeError) as exc:
        raise QuantStudioError("字段预览回执不完整") from exc
    if not columns or not all(isinstance(column, str) and column for column in columns):
        raise QuantStudioError("原件没有可映射的字段")
    if len(columns) > 200:
        raise QuantStudioError("网页映射最多200列，请使用QDK命令行")
    _atomic(
        store.root / "incoming" / identifier / "preview.json",
        json.dumps(preview, ensure_ascii=False),
    )

    refresh = None
    refresh_notice = ""
    contract = {}
    name = uploaded["filename"].rsplit(".", 1)[0]
    parent = ""
    reuse_hidden = ""
    if reuse_request:
        refresh = _refresh_context(store, reuse_request, columns)
        name, parent = refresh["record"]["name"], refresh["version"]
        original = escape(json.dumps(refresh["contract"], ensure_ascii=False, indent=2))
        if refresh["changed"]:
            changes = []
            if refresh["added"]:
                changes.append("新增列：" + "、".join(refresh["added"]))
            if refresh["removed"]:
                changes.append("删除列：" + "、".join(refresh["removed"]))
            if refresh["reordered"]:
                changes.append("列顺序发生变化")
            refresh_notice = (
                '<section class="panel"><h2>检测到原始字段变化</h2>'
                f'<p role="alert">{escape("；".join(changes))}。自动复用已停止，'
                "请逐列重新确认下面的映射。</p>"
                f"<details><summary>查看旧契约映射</summary><pre>{original}</pre></details>"
                "</section>"
            )
        else:
            contract = refresh["contract"]
            reuse_hidden = (
                '<input type="hidden" name="reuse_request" '
                f'value="{escape(reuse_request, quote=True)}">'
            )
            refresh_notice = (
                '<section class="panel"><h2>正在复用已确认映射</h2>'
                f"<p>数据集：{escape(name)} · 父版本：<code>{escape(parent)}</code>。"
                "新旧原始字段完全一致，下面已填入旧契约，请在提交前复核。</p>"
                f"<details><summary>查看原契约映射</summary><pre>{original}</pre></details>"
                "</section>"
            )

    mapping = contract.get("mapping", {})
    target_by_source = {source: target for target, source in mapping.items()}
    types = contract.get("types", {})
    formats = contract.get("formats", {})
    metadata = contract.get("metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    units = metadata.get("units", {})
    units = units if isinstance(units, dict) else {}
    type_options = [
        ("string", "文本/证券代码"),
        ("number", "数值"),
        ("integer", "整数"),
        ("date", "日期"),
        ("datetime", "带时区时点"),
        ("boolean", "布尔"),
    ]
    rows = []
    for index, column in enumerate(columns):
        target = target_by_source.get(column, column if not contract else "")
        dtype = types.get(target, "string")
        fmt = formats.get(target, "")
        unit = units.get(target, "")
        rows.append(
            f"<tr><th>{escape(column)}</th><td>"
            f'<input name="map_{index}" value="{escape(target, quote=True)}" '
            f'aria-label="{escape(column, quote=True)}的规范列名"></td>'
            f"<td>{_select(f'type_{index}', type_options, dtype)}</td>"
            f'<td><input name="format_{index}" value="{escape(fmt, quote=True)}" '
            'aria-label="日期格式" placeholder="%Y-%m-%d"></td>'
            f'<td><input name="unit_{index}" value="{escape(unit, quote=True)}" '
            'aria-label="数值单位" placeholder="例如CNY、share"></td></tr>'
        )
    if sample_rows and isinstance(sample_rows[0], dict):
        sample_rows = [[row.get(column) for column in columns] for row in sample_rows]

    kinds = _select(
        "kind",
        [
            ("table", "通用表格探索"),
            ("daily_bars", "日线行情"),
            ("history", "历史财务数据"),
        ],
        contract.get("kind", "table"),
    )
    adjustments = _select(
        "adjustment",
        [
            ("", "未声明"),
            ("raw", "未复权"),
            ("split", "拆股复权"),
            ("total_return", "总收益"),
            ("provider_adjusted", "供应商复权"),
            ("none", "不适用"),
        ],
        metadata.get("adjustment", "") if isinstance(metadata, dict) else "",
    )
    availability = _select(
        "availability",
        [
            ("", "未声明"),
            ("latest_only", "当前最新记录"),
            ("point_in_time", "具有逐条历史可得时间"),
        ],
        metadata.get("availability", "") if isinstance(metadata, dict) else "",
    )
    try:
        refreshes = _refresh_options(store)
    except QuantStudioError:
        refreshes = []
    choices = '<option value="">不复用，创建首次导入映射</option>' + "".join(
        f'<option value="{record["id"]}"'
        f"{' selected' if reuse_request == record['id'] else ''}>"
        f"{escape(record['name'])} · {escape(version)}</option>"
        for record, version in refreshes
    )
    refresh_picker = f'''<section class="panel"><h2>刷新旧数据</h2>
<p>选择一个已发布版本后，系统会带入数据集名称、父版本和原契约映射；字段变化会先阻断自动复用并显示差异。</p>
<form method="get" action="/intake/map/{identifier}">
<input type="hidden" name="encoding" value="{escape(encoding, quote=True)}">
<input type="hidden" name="delimiter" value="{escape(delimiter, quote=True)}">
<label>复用已确认映射<select name="reuse">{choices}</select></label>
<button class="btn">检查字段并带入</button></form></section>'''
    primary_key = ",".join(contract.get("primary_key", []))
    name_input = _input("name", "数据集名称", name)
    source_input = _input("source", "原始来源/文件出处", metadata.get("source", ""))
    provider_input = _input(
        "provider", "供应商或导出系统", metadata.get("provider", "")
    )
    timezone_input = _input(
        "timezone",
        "数据时区",
        metadata.get("timezone", ""),
        "例如Asia/Shanghai；不确定时留空，严格用途会提示缺口",
    )
    keys_input = _input(
        "primary_key",
        "唯一键（规范列名，逗号分隔）",
        primary_key,
        "行情通常为symbol,date；不自动删除重复记录",
    )
    parent_input = _input("parent", "要更新的旧版本ID（首次导入留空）", parent)
    return f'''<header class="page-head"><h1>确认字段映射</h1>
<p>{escape(uploaded["filename"])} · 原件已留存 ·
SHA-256 <code>{escape(uploaded["sha256"])}</code></p></header>
{refresh_picker}{refresh_notice}
<section class="panel"><h2>原始样例</h2>
<p>仅预览最多20行，不代表全量质量检查通过。</p>{table(columns, sample_rows)}</section>
<section class="panel"><form method="post" action="/intake/import">{csrf(token)}
<input type="hidden" name="upload" value="{identifier}">{reuse_hidden}
<input type="hidden" name="encoding" value="{escape(encoding, quote=True)}">
<input type="hidden" name="delimiter" value="{escape(delimiter, quote=True)}">
<div class="recipe-grid">{name_input}<label>数据类型{kinds}</label>
{source_input}{provider_input}{timezone_input}
<label>复权口径{adjustments}</label><label>历史可得性{availability}</label>
{keys_input}{parent_input}</div>
<p>日线行情需要symbol,date,open,high,low,close；历史财务需要symbol,period_end及值列。规范列名留空表示本次不选该列，原件保留全部列。</p>
<div class="table-scroll"><table><thead><tr>
<th>原列</th><th>规范列名</th><th>类型</th><th>日期格式</th><th>单位</th>
</tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<p>证券代码使用文本以保留前导零。只按明确映射转换，不自动补值、猜单位或推断披露时间。</p>
<button class="btn primary">提交完整检查并发布符合要求的版本</button>
</form></section>'''


def quality_body(data):
    if not isinstance(data, dict):
        return (
            '<h2>质量报告尚不可用</h2><p role="alert">'
            "数据环境没有返回结构化检查结果，请查看任务日志后重试。</p>"
        )
    if data.get("ok") is False:
        message = _error_message(data, "全量质量检查没有完成")
        detail = escape(json.dumps(data, ensure_ascii=False, indent=2))
        return (
            "<h2>全量质量检查没有完成</h2>"
            f'<p role="alert">{escape(message)}</p>'
            "<p>原件和本次导入请求均已保留。修正来源或契约后，请新建导入请求。</p>"
            f"<details><summary>完整错误回执</summary><pre>{detail}</pre></details>"
        )
    if data.get("ok") is True and isinstance(data.get("data"), dict):
        data = data["data"]
    report = data.get("report", data)
    if not isinstance(report, dict):
        return (
            '<h2>质量报告尚不可用</h2><p role="alert">'
            "检查回执缺少报告内容，请查看任务日志。</p>"
        )
    allowed = report.get("allowed")
    state = (
        "用途检查通过"
        if allowed is True
        else "此用途被阻断"
        if allowed is False
        else str(report.get("status", "待检查"))
    )
    body = f"<h2>{escape(state)}</h2>"
    scope = report.get("scope", {})
    if not isinstance(scope, dict):
        scope = {"范围": scope}
    if scope:
        body += "<h3>实际检查范围</h3>" + table(
            ["范围项", "实际值"],
            [[key, _format_value(value)] for key, value in scope.items()],
        )
    full = report.get("full_scan", scope.get("full_scan", "以报告范围为准"))
    body += f"<p>完整扫描：{escape(str(full))}</p>"
    issues = report.get("issues", [])
    if not isinstance(issues, list):
        issues = []
    body += table(
        ["严重度", "检查项", "问题行", "问题列", "影响数", "说明", "处理建议"],
        [
            [
                issue.get("severity", issue.get("level", "未分级")),
                issue.get("rule", ""),
                _format_value(
                    issue.get(
                        "rows",
                        issue.get("affected_rows", issue.get("row", "")),
                    )
                ),
                _format_value(issue.get("columns", issue.get("column", ""))),
                issue.get("affected_count", ""),
                issue.get("message", ""),
                issue.get("recommendation", ""),
            ]
            for issue in issues
            if isinstance(issue, dict)
        ],
    )
    body += (
        "<details><summary>完整结构化检查结果</summary><pre>"
        + escape(json.dumps(data, ensure_ascii=False, indent=2))
        + "</pre></details>"
    )
    return body


def dataset_view(store, token, query):
    name, version = query.get("name", [""])[0], query.get("version", [""])[0]
    args = ["--name", name]
    if version:
        args += ["--version", version]
    data = _data(store.backend("show", args))
    version = data["version"]["id"]
    base = urlencode({"name": name, "version": version})
    body = (
        f'<header class="page-head"><h1>{escape(name)}</h1>'
        f"<p>固定版本：{escape(version)}</p></header>"
        f'<section class="panel">{quality_body(data)}</section>'
    )
    body += f'''<section class="panel"><h2>检查本次研究用途</h2>
<form method="get" action="/intake/check">
<input type="hidden" name="name" value="{escape(name, quote=True)}">
<input type="hidden" name="version" value="{escape(version, quote=True)}">
<div class="recipe-grid"><label>用途{_select("purpose", list(PURPOSES.items()))}</label>
{_input("columns", "实际使用的列（逗号分隔，留空为全部）")}
{_input("symbols", "证券代码（逗号分隔，留空为全部）")}
{_input("start", "开始日期", "", "YYYY-MM-DD")}
{_input("end", "结束日期", "", "YYYY-MM-DD")}</div>
<button class="btn">核验完整选定范围</button></form>
<p><a href="/intake/read?{base}">查看规范数据</a> ·
<a href="/intake">返回数据接入</a></p></section>
<section class="panel"><h2>比较数据版本</h2><form method="get" action="/intake/diff">
<input type="hidden" name="name" value="{escape(name, quote=True)}">
<input type="hidden" name="new" value="{escape(version, quote=True)}">
{_input("old", "旧版本ID", data["version"].get("parent_id") or "")}
<button class="btn">查看新增、删除与修订</button></form></section>'''
    return body


def scope_arguments(query):
    purpose = query.get("purpose", ["exploration"])[0]
    if purpose not in PURPOSES:
        raise QuantStudioError("未知研究用途")
    args = ["--name", query.get("name", [""])[0], "--purpose", purpose]
    for key in ("version", "start", "end"):
        if value := query.get(key, [""])[0]:
            args += ["--" + key, value]
    for key in ("columns", "symbols"):
        values = [v.strip() for v in query.get(key, [""])[0].split(",") if v.strip()]
        if values:
            args += ["--" + key, *values]
    return args


class IntakeHandler:
    def _intake_get(self, path):
        from quant_studio.server import _layout

        store = IntakeWorkspace(self.runs_root)
        query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        if path == "/intake":
            body = intake_body(store, self.csrf_token)
        elif path.startswith("/intake/map/"):
            body = mapping_body(
                store,
                self.csrf_token,
                path.rsplit("/", 1)[1],
                encoding=query.get("encoding", ["utf-8-sig"])[0],
                delimiter=query.get("delimiter", [","])[0],
                reuse_request=query.get("reuse", [None])[0],
            )
        elif path.startswith("/intake/requests/"):
            record = store.request(path.rsplit("/", 1)[1])
            status = _STATUS_LABELS.get(record["status"], record["status"])
            uploaded = store.upload_record(record["upload_id"])
            parent_label = escape(str(record.get("parent") or "首次导入"))
            body = (
                f'<header class="page-head"><h1>{escape(record["name"])}</h1>'
                f"<p>状态：{escape(status)}</p>"
                f'<p>原件：<a href="/intake/map/{record["upload_id"]}">'
                f"{escape(uploaded['filename'])}</a> · "
                f"父版本：{parent_label}</p></header>"
                '<section class="panel">'
            )
            if job_id := record.get("job_id"):
                body += f'<p><a href="/jobs/{job_id}">查看任务与日志</a></p>'
            response = record.get("response")
            if response is not None:
                body += quality_body(response)
                response_data = (
                    response.get("data") if isinstance(response, dict) else None
                )
                if isinstance(response_data, dict) and (
                    version := response_data.get("version")
                ):
                    version_id = (
                        version.get("id") if isinstance(version, dict) else version
                    )
                    link = urlencode({"name": record["name"], "version": version_id})
                    body += f'<a href="/intake/view?{link}">查看数据版本与用途检查</a>'
            elif message := record.get("message"):
                body += f'<p role="alert">{escape(str(message))}</p>'
            body += '<p><a href="/intake">返回导入列表</a></p></section>'
        elif path == "/intake/view":
            body = dataset_view(store, self.csrf_token, query)
        elif path in {"/intake/check", "/intake/read"}:
            action = path.rsplit("/", 1)[1]
            args = scope_arguments(query)
            if action == "read":
                args += ["--limit", "100"]
            response = store.backend(action, args)
            body = (
                '<header class="page-head"><h1>研究用途检查</h1></header>'
                '<section class="panel">'
            )
            result = response.get("data", response)
            body += quality_body(result.get("check", result))
            if action == "read" and response.get("ok"):
                rows = result.get("rows", [])
                columns = list(rows[0]) if rows else []
                body += table(columns, [[row.get(c) for c in columns] for row in rows])
                matched = result.get("matched_rows", "未知")
                body += f"<p>展示{len(rows)}行；完整选定范围有{matched}行。</p>"
            if action == "check" and result.get("allowed") is True:
                hidden = "".join(
                    f'<input type="hidden" name="{escape(key)}" '
                    f'value="{escape(values[0], quote=True)}">'
                    for key, values in query.items()
                    if key
                    in {
                        "name",
                        "version",
                        "purpose",
                        "columns",
                        "symbols",
                        "start",
                        "end",
                    }
                )
                connect_form = '<form method="post" action="/intake/connect">'
                submit = (
                    '<button class="btn primary">固定范围并创建项目</button></form>'
                )
                body += (
                    connect_form
                    + csrf(self.csrf_token)
                    + hidden
                    + _input("project_name", "新研究项目名称")
                    + _input("question", "想回答的研究问题")
                    + submit
                )
            body += "</section>"
        elif path == "/intake/diff":
            result = _data(
                store.backend(
                    "diff",
                    [
                        "--name",
                        query.get("name", [""])[0],
                        "--old",
                        query.get("old", [""])[0],
                        "--new",
                        query.get("new", [""])[0],
                    ],
                )
            )
            body = (
                '<header class="page-head"><h1>数据版本变化</h1></header>'
                '<section class="panel">'
            )
            body += table(["变化", "记录数"], list(result.get("rows", {}).items()))
            body += (
                "<pre>"
                + escape(json.dumps(result, ensure_ascii=False, indent=2))
                + "</pre></section>"
            )
        else:
            raise QuantStudioError("未知数据页面")
        self._send_html(_layout("数据接入与质量", body, "intake"))

    def _intake_post(self, path, data):
        store = IntakeWorkspace(self.runs_root)
        if path == "/intake/upload":
            filename, content = field(data, "filename"), field(data, "content")
            encoding, delimiter = (
                field(data, "encoding", "utf-8-sig"),
                field(data, "delimiter", ","),
            )
            if data:
                raise QuantStudioError("上传含未知字段")
            item = store.upload(filename, content)
            self._redirect(
                f"/intake/map/{item['id']}?"
                + urlencode({"encoding": encoding, "delimiter": delimiter})
            )
        elif path == "/intake/import":
            upload_id, name = field(data, "upload"), field(data, "name")
            parent = field(data, "parent", "") or None
            reuse_request = field(data, "reuse_request", "") or None
            source = store.source(upload_id)
            try:
                preview = json.loads(
                    (source.parent / "preview.json").read_text(encoding="utf-8")
                )
                columns = [column["name"] for column in preview["columns"]]
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise QuantStudioError("字段预览已失效，请返回原件重新解析") from exc
            if reuse_request:
                refresh = _refresh_context(store, reuse_request, columns)
                if refresh["changed"]:
                    raise QuantStudioError("原始字段已变化，不能自动复用旧映射")
            contract = contract_from_form(data, columns)
            if data:
                raise QuantStudioError("映射含未知字段")
            contract["input"]["format"] = source.suffix.lstrip(".")
            if source.suffix == ".parquet":
                contract["input"] = {"format": "parquet"}
            record = store.prepare(upload_id, name, contract, parent=parent)
            self._jobs().submit(
                "intake",
                "research-intake",
                {},
                profile=self._request_profile,
                intake={"request_id": record["id"]},
            )
            self._redirect(f"/intake/requests/{record['id']}")
        elif path == "/intake/connect":
            from quant_studio.projects import ProjectStore

            name, version = field(data, "name"), field(data, "version")
            purpose = field(data, "purpose", "exploration")
            project_name, question = (
                field(data, "project_name"),
                field(data, "question"),
            )
            scope = {}
            for key in ("columns", "symbols"):
                scope[key] = [
                    v.strip() for v in field(data, key, "").split(",") if v.strip()
                ]
            for key in ("start", "end"):
                scope[key] = field(data, key, "")
            if data:
                raise QuantStudioError("用途登记包含未知字段")
            dataset = store.register(name, version, purpose=purpose, scope=scope)
            project = ProjectStore(self.runs_root).save(
                project_name, question, datasets=[dataset["id"]]
            )
            self._redirect(f"/projects/{project['id']}")
        else:
            raise QuantStudioError("未知数据操作")
