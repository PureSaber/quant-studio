"""Daily research surfaces: datasets, version history and experiment notebook."""

import json
from datetime import date, timedelta
from html import escape
from urllib.parse import parse_qs, urlencode, urlparse

from quant_studio import QuantStudioError
from quant_studio.datasets import DatasetStore, collection_request
from quant_studio.experiments import ExperimentStore, configuration_diff
from quant_studio.templates import load_template, template_ids


def csrf(token):
    return f'''<input
type="hidden"
name="_csrf_token"
value="{escape(token, quote=True)}">'''


def field(data, key, default=None):
    values = data.pop(key, [default])
    if len(values) != 1 or values[0] is None:
        raise QuantStudioError(f"字段缺少或重复：{key}")
    return values[0]


def diff_table(rows):
    content = "".join(
        f"""<tr>
<td>{escape(row["path"])}</td>
<td
class="diff-value">{escape(json.dumps(row["before"], ensure_ascii=False))}</td>
<td
class="diff-value">{escape(json.dumps(row["after"], ensure_ascii=False))}</td>
</tr>"""
        for row in rows
    )
    return f"""<div
class="table-scroll">
<table>
<tr>
<th>参数</th>
<th>原值</th>
<th>新值</th>
</tr>{content or "<tr><td colspan=3>配置没有变化</td></tr>"}</table>
</div>"""


def history_body(store, identifier, query):
    versions = store.history(identifier)
    latest = versions[-1]
    before = query.get(
        "before",
        [versions[-2]["revision"] if len(versions) > 1 else latest["revision"]],
    )[0]
    after = query.get("after", [latest["revision"]])[0]
    revision_map = {r["revision"]: r for r in versions}
    if before not in revision_map or after not in revision_map:
        raise QuantStudioError("只能比较此方案已保存的版本")
    rows = "".join(
        f"""<tr>
<td>
<a
href="/research/{identifier}?revision={r["revision"]}">V{i}</a>
<br>
<code>{r["revision"][:12]}</code>
</td>
<td>{escape(r["created_at"])}</td>
<td>{escape(r.get("note", "") or "未填写修改说明")}</td>
</tr>"""
        for i, r in enumerate(versions, 1)
    )

    def choices(selected):
        return "".join(
            f'''<option
value="{r["revision"]}" {"selected" if r["revision"] == selected else ""}>
V{i} · {escape(r.get("note", "")[:60])}</option>'''
            for i, r in enumerate(versions, 1)
        )

    return f"""<header
class="page-head">
<h1>{escape(latest["name"])} · 版本历史</h1>
<p>V1、V2 按保存顺序编号；原始版本与运行关联保持不变。</p>
<a
href="/research/{identifier}">返回最新方案</a>
</header>
<section
class="panel">
<table>
<tr>
<th>版本</th>
<th>保存时间</th>
<th>修改说明</th>
</tr>{rows}</table>
</section>
<section
class="panel">
<h2>参数变化</h2>
<form
method="get">
<div
class="recipe-grid">
<label>原版本<select
name="before">{choices(before)}</select>
</label>
<label>新版本<select
name="after">{choices(after)}</select>
</label>
</div>
<button
class="btn">比较版本</button>
</form>{diff_table(configuration_diff(revision_map[before], revision_map[after]))}
</section>"""


def dataset_body(root, token, query):
    store = DatasetStore(root)
    cards = []
    for item in store.list():
        symbols = "、".join(str(v) for v in item["symbols"][:30]) or "由原生配置指定"
        verified = (
            store.verify(item["id"])
            if query.get("verify", [""])[0] == item["id"]
            else None
        )
        state = (
            "字节检查通过"
            if verified and verified["valid"]
            else "；".join(verified["problems"])
            if verified
            else "已登记；使用前会检查文件变化"
        )
        refresh = ""
        if item["template_id"] == "hk-equity-daily" and item["provider"] in {
            "akshare_sina_hk",
            "akshare_eastmoney_hk",
        }:
            refresh = f'''<form
method="post"
action="/datasets/refresh">{csrf(token)}<input
type="hidden"
name="id"
value="{item["id"]}">
<label>更新截止日期<input
type="date"
name="end"
value="{date.today().isoformat()}" required>
</label>
<button
class="btn">重新采集为新版本</button>
</form>'''
        label = (
            "已准入研究数据"
            if item.get("kind") == "intake"
            else load_template(item["template_id"]).title
        )
        destination = (
            "/projects"
            if item.get("kind") == "intake"
            else "/research/new?template="
            + item["template_id"]
            + "&dataset="
            + item["id"]
        )
        cards.append(
            f"""<section
class="panel">
<h2>{escape(item["name"])}</h2>
<p>{escape(label)} · {escape(item["provider"])}</p>
<p>覆盖：{escape(str(item["start"]))} → {escape(str(item["end"]))}<br>
标的：{escape(symbols)}</p>
<p
class="note">{escape(item["evidence"])}<br>{escape(state)}<br>{escape(item["limits"])}</p>
<p>
<a
href="{escape(destination, quote=True)}">
用此数据新建研究</a> · <a
href="/datasets?verify={item["id"]}">检查文件完整性</a>
</p>{refresh}<details>
<summary>路径与身份</summary>
<p>{escape(item["path"])}</p>
<code>{item["identity"]}</code>
</details>
</section>"""
        )
    choices = "".join(
        f'''<option
value="{t}">{escape(load_template(t).title)}</option>'''
        for t in template_ids()
        if load_template(t).metadata.get("input_source")
    )
    today = date.today()
    cards_html = "".join(cards) or (
        '<section class="panel">尚未登记数据集。'
        "可登记已有输入，或明确选择数据源采集。</section>"
    )
    return f'''<header
class="page-head">
<h1>研究数据集</h1>
<p>选数据集进入研究。采集总是创建新快照，已有研究继续使用原输入。</p>
<a
href="/data">查看原生预检证据与缺失项</a>
</header>
<div
class="recipe-grid">{cards_html}</div>
<section
class="panel">
<h2>采集真实港股日线</h2>
<p>调用已安装的原生数据层。来源只使用你选择的供应商，失败保留日志；不填补缺失行情。此入口采集价格与日历，完整公司行动和历史股票池仍需单独证据。</p>
<form
method="post"
action="/datasets/collect">{csrf(token)}<input
type="hidden"
name="template"
value="hk-equity-daily">
<div
class="recipe-grid">
<label>数据集名称<input
name="name"
value="我的港股行情"
maxlength="120" required>
</label>
<label>数据提供方<select
name="provider">
<option
value="akshare_sina_hk">新浪港股（AKShare）</option>
<option
value="akshare_eastmoney_hk">东方财富港股（AKShare）</option>
</select>
</label>
<label>证券代码<input
name="symbols"
placeholder="00700, 00005" required>
<small>1–20 只，五位代码；空格或逗号分隔。</small>
</label>
<label>开始日期<input
type="date"
name="start"
value="{(today - timedelta(days=365)).isoformat()}" required>
</label>
<label>结束日期<input
type="date"
name="end"
value="{today.isoformat()}" required>
</label>
</div>
<button
class="btn primary">采集并登记新数据集</button>
</form>
</section>
<details
class="panel">
<summary>登记已有数据或原生配置</summary>
<p>适用于 A 股缓存、港股快照、基金数据、美股数据包，以及统计套利／择时配置。
文件存在不等于原生预检通过。</p>
<form
method="post"
action="/datasets/register">{csrf(token)}<div
class="recipe-grid">
<label>数据集名称<input
name="name"
maxlength="120" required>
</label>
<label>研究类型<select
name="template">{choices}</select>
</label>
<label>服务端已有路径<input
name="path" required>
</label>
</div>
<button
class="btn">检查文件并登记</button>
</form>
</details>'''


def experiments_body(root, token, query):
    store = ExperimentStore(root)
    text, status = query.get("q", [""])[0], query.get("status", [""])[0]
    records = store.list(query=text, status=status)
    page = max(1, int(query.get("page", ["1"])[0]))
    page = min(page, max(1, (len(records) + 24) // 25))
    rows = []
    for r in records[(page - 1) * 25 : page * 25]:
        revision = r["recipe"].get("revision", "")
        rows.append(
            f'''<tr>
<td>
<input
type="checkbox"
name="run"
value="{r["run_id"]}"
aria-label="选择 {escape(r["title"], quote=True)}">
</td>
<td>
<a
href="/experiments/{r["run_id"]}">{escape(r["title"])}</a>
<br>
<small>{escape(r["template_id"])} · {revision[:12]}</small>
</td>
<td>{escape(r["status"])}</td>
<td>{escape(r["note"][:100])}</td>
<td>{escape(r["when"])}</td>
</tr>'''
        )
    links = " · ".join(
        f"""<a
href="/experiments?{escape(urlencode({"q": text, "status": status, "page": p}))}">
{label}</a>"""
        for p, label in [
            (max(1, page - 1), "上一页"),
            (min(max(1, (len(records) + 24) // 25), page + 1), "下一页"),
        ]
    )
    return f'''<header
class="page-head">
<h1>实验笔记与比较</h1>
<p>搜索全部历史记录，给实验命名和写备注。选择 2–4 次运行比较参数与结果。</p>
</header>
<section
class="panel">
<form
method="get">
<div
class="recipe-grid">
<label>搜索名称、备注或版本<input
name="q"
value="{escape(text, quote=True)}">
</label>
<label>状态<select
name="status">
<option
value="">全部</option>{
        "".join(
            f'''<option
value="{v}" {"selected" if v == status else ""}>{label}</option>'''
            for v, label in [
                ("succeeded", "运行成功"),
                ("checked", "预检通过"),
                ("failed", "失败"),
                ("blocked", "输入阻断"),
                ("interrupted", "中断"),
            ]
        )
    }</select>
</label>
</div>
<button
class="btn">搜索</button>
</form>
</section>
<section
class="panel">
<form
action="/experiments/compare"
method="get">
<div
class="table-scroll">
<table>
<tr>
<th>选择</th>
<th>实验</th>
<th>状态</th>
<th>备注</th>
<th>记录时间</th>
</tr>{"".join(rows)}</table>
</div>
<button
class="btn primary">比较所选实验</button>
</form>
<p>共 {len(records)} 条 · 第 {page} 页，每页 25 条 · {links}</p>
</section>'''


def experiment_body(root, token, identifier):
    store = ExperimentStore(root)
    directory = store._run(identifier)
    record = store.annotation(identifier)
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    recipe = request.get("recipe") or {}
    provenance = directory / "provenance.json"
    identity = (
        provenance.read_text(encoding="utf-8")
        if provenance.exists()
        else "旧运行没有统一的代码／输入身份回执。原生证据保留在完整报告中。"
    )
    return f'''<header
class="page-head">
<h1>实验笔记</h1>
<p>{escape(recipe.get("name") or request.get("template_id", ""))} · {
        identifier[:12]
    }</p>
<a
href="/runs/{identifier}">打开结果、日志与报告</a> · <a
href="/experiments">返回实验列表</a>{
        f""" · <a
href="/research/{recipe["id"]}/history">方案版本历史</a>"""
        if recipe
        else ""
    }</header>
<section
class="panel">
<form
method="post"
action="/experiments/{identifier}/note">{csrf(token)}<input
type="hidden"
name="revision"
value="{record["revision"]}">
<label>实验名称<input
name="title"
maxlength="120"
value="{escape(record["title"], quote=True)}">
</label>
<label>研究备注<textarea
name="note"
maxlength="4000" rows="6">{escape(record["note"])}</textarea>
</label>
<button
class="btn primary">保存笔记</button>
</form>
<p
class="note">笔记独立保存，不改写原始配置、净值或运行结果。</p>
</section>
<details
class="panel">
<summary>代码、数据与环境身份</summary>
<pre>{escape(identity)}</pre>
</details>'''


def compare_body(root, identifiers):
    from quant_studio.result_explorer import comparison_explorer

    result = ExperimentStore(root).compare(identifiers)

    def pct(value):
        return "不可用" if value is None else f"{value * 100:.3f}%"

    rows = "".join(
        f"""<tr>
<td>
<a
href="/experiments/{r["run_id"]}">{escape(r["title"])}</a>
</td>
<td>{escape(r["status"])}</td>
<td>{escape(r["currency"])}</td>
<td>{escape(r["dates"][0] + " → " + r["dates"][-1]) if r["dates"] else "不可用"}</td>
<td>{pct(r["return"])}</td>
<td>{pct(r["drawdown"])}</td>
</tr>"""
        for r in result["runs"]
    )
    chart = ""
    if result["comparable"]:
        chart = comparison_explorer(result["runs"])
    warning = (
        "观测日期、研究类型与币种一致，可查看描述性差异；未据此评选有效策略。"
        if result["comparable"]
        else "口径不一致，仅并排展示各自结果，不合并曲线或排名："
        + "；".join(result["reasons"])
    )
    differences = []
    for row in result["runs"][1:]:
        changed = configuration_diff(
            result["runs"][0]["configuration"], row["configuration"]
        )
        differences.append(
            f"<h3>{escape(row['title'])} 相对首个实验</h3>" + diff_table(changed)
        )
    diffs = "".join(differences)
    return f"""<header
class="page-head">
<h1>实验对比</h1>
<a
href="/experiments">返回实验列表</a>
</header>
<section
class="panel">
<p>{escape(warning)}</p>
<div
class="table-scroll">
<table>
<tr>
<th>实验</th>
<th>状态</th>
<th>币种</th>
<th>观测区间</th>
<th>区间收益</th>
<th>最大回撤</th>
</tr>{rows}</table>
</div>{chart}<p
class="note">图中展示全部所选实验，以各自原始期初归一化；缩放不改变表格的全区间指标。费用、数据性质和模型假设请结合各自报告。</p>
</section>
<section
class="panel">
<h2>参数差异</h2>{diffs}</section>"""


class WorkbenchHandler:
    def _workbench_get(self, path):
        from quant_studio.server import _layout

        query = parse_qs(urlparse(self.path).query)
        if path == "/datasets":
            body, active = (
                dataset_body(self.runs_root, self.csrf_token, query),
                "datasets",
            )
        elif path == "/experiments":
            body, active = (
                experiments_body(self.runs_root, self.csrf_token, query),
                "experiments",
            )
        elif path == "/experiments/compare":
            body, active = (
                compare_body(self.runs_root, query.get("run", [])),
                "experiments",
            )
        elif path.startswith("/experiments/"):
            body, active = (
                experiment_body(self.runs_root, self.csrf_token, path.split("/")[-1]),
                "experiments",
            )
        elif path == "/operations":
            from quant_studio.operations import operations_body

            body, active = operations_body(self.runs_root), "operations"
        else:
            raise QuantStudioError("未知页面")
        self._send_html(_layout("研究工作台", body, active))

    def _workbench_post(self, path, data):
        store = DatasetStore(self.runs_root)
        if path == "/datasets/register":
            name, template, source = (
                field(data, "name"),
                field(data, "template"),
                field(data, "path"),
            )
            if data:
                raise QuantStudioError("未知数据集字段")
            item = store.register(name, template, source)
            self._redirect(f"/datasets?verify={item['id']}")
        elif path in {"/datasets/collect", "/datasets/refresh"}:
            if path.endswith("/refresh"):
                previous = store.get(field(data, "id"))
                request = collection_request(
                    previous["name"] + " · 更新",
                    previous["template_id"],
                    " ".join(previous["symbols"]),
                    previous["start"],
                    field(data, "end"),
                    previous["provider"],
                    previous["id"],
                )
            else:
                request = collection_request(
                    *(
                        field(data, name)
                        for name in [
                            "name",
                            "template",
                            "symbols",
                            "start",
                            "end",
                            "provider",
                        ]
                    )
                )
            if data:
                raise QuantStudioError("未知采集字段")
            job = self._jobs().submit(
                "collect",
                request["template_id"],
                {},
                profile=self._request_profile,
                collection=request,
            )
            self._redirect(f"/jobs/{job['job_id']}")
        elif path.startswith("/experiments/") and path.endswith("/note"):
            identifier = path.removeprefix("/experiments/").removesuffix("/note")
            title, note, revision = (
                field(data, "title", ""),
                field(data, "note", ""),
                field(data, "revision", ""),
            )
            if data:
                raise QuantStudioError("未知备注字段")
            ExperimentStore(self.runs_root).annotate(
                identifier, title, note, expected=revision
            )
            self._redirect(f"/experiments/{identifier}")
        else:
            raise QuantStudioError("未知操作")
