"""Daily research surfaces: datasets, version history and experiment notebook."""

import json
from datetime import date, timedelta
from html import escape
from urllib.parse import parse_qs, urlparse

from quant_studio import QuantStudioError
from quant_studio.datasets import DatasetStore, collection_request
from quant_studio.derivative_datasets import form as derivative_form
from quant_studio.experiments import ExperimentStore, configuration_diff
from quant_studio.free_derivative_datasets import form as free_derivative_form
from quant_studio.presentation import template_title
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
    from decimal import Decimal

    from quant_studio.form_controls import LABELS
    from quant_studio.form_presentation import numeric, rate_echo, unit_for
    from quant_studio.presentation import RATE_FIELDS
    from quant_studio.research_web import _LABELS

    def label(path):
        key = path.rsplit(".", 1)[-1]
        title = (_LABELS | LABELS).get(key, "高级参数")
        return (
            f"{escape(title)}<details><summary>字段标识</summary>"
            f"<code>{escape(path)}</code></details>"
        )

    def value(path, item):
        key = path.rsplit(".", 1)[-1]
        if numeric(item):
            if key in RATE_FIELDS:
                return escape(f"{Decimal(str(item)) * 10000:g} bp = {rate_echo(item)}")
            return escape(f"{item} {unit_for(key)}")
        return escape(json.dumps(item, ensure_ascii=False))

    content = "".join(
        f"""<tr>
<td>{label(row["path"])}</td>
<td
class="diff-value">{value(row["path"], row["before"])}</td>
<td
class="diff-value">{value(row["path"], row["after"])}</td>
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
{free_derivative_form(csrf(token))}
{derivative_form(csrf(token))}
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


def experiments_body(root, token, query, jobs=()):
    from quant_studio.run_index import index_body

    return index_body(root, token, query, jobs)


def experiment_body(root, token, identifier):
    store = ExperimentStore(root)
    directory = store._run(identifier)
    record = store.annotation(identifier)
    note_error = (
        '<p class="banner" role="alert">'
        "备注无法读取；原文件已保留，请从备份恢复后编辑。</p>"
        if record.get("annotation_error")
        else ""
    )
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
<p>{escape(recipe.get("name") or template_title(request.get("template_id", "")))} · {
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
class="btn primary" {"disabled" if note_error else ""}>保存笔记</button>
</form>
{note_error}
<p
class="note">笔记独立保存，不改写原始配置、净值或运行结果。</p>
</section>
<details
class="panel">
<summary>代码、数据与环境身份</summary>
<pre>{escape(identity)}</pre>
</details>'''


def compare_body(root, identifiers):
    from quant_studio import etf_experiments
    from quant_studio.experiment_context import context_body, status_label
    from quant_studio.result_explorer import comparison_explorer

    if any(etf_experiments.is_etf(root, identifier) for identifier in identifiers):
        return etf_experiments.compare_body(root, identifiers)

    result = ExperimentStore(root).compare(identifiers)

    def pct(value):
        return "不可用" if value is None else f"{value * 100:.3f}%"

    rows = "".join(
        f"""<tr>
<td>
<a
href="/experiments/{r["run_id"]}">{escape(r["title"])}</a>
</td>
<td>{escape(status_label(r["status"]))}</td>
<td>{escape(r["currency"])}</td>
<td>{escape(r["dates"][0] + " → " + r["dates"][-1]) if r["dates"] else "不可用"}</td>
<td>{pct(r["return"])}</td>
<td>{pct(-r["drawdown"] if r["drawdown"] is not None else None)}</td>
<td>{escape(r.get("error") or "—")}</td>
</tr>"""
        for r in result["runs"]
    )
    chart = ""
    eligible = [r for r in result["runs"] if r["series"] is not None and not r["error"]]
    subset = (
        ExperimentStore(root).compare([r["run_id"] for r in eligible])
        if len(eligible) >= 2
        else None
    )
    if subset and subset["comparable"]:
        chart = comparison_explorer(eligible)
    if result["comparable"]:
        chart = comparison_explorer(result["runs"])
    warning = (
        "观测日期、研究类型与币种一致，可查看描述性差异；未据此评选有效策略。"
        if result["comparable"]
        else "所选记录不能全部合并收益曲线；配置和失败原因仍可比较。"
        "曲线仅展示其中口径一致且有有效净值的运行：" + "；".join(result["reasons"])
    )
    differences = []
    for row in result["runs"][1:]:
        changed = configuration_diff(
            result["runs"][0]["configuration"], row["configuration"]
        )
        differences.append(
            f"<h3>{escape(row['title'])} 相对首个实验</h3>" + diff_table(changed)
        )
    chart_note = (
        "图中以各自原始期初归一化；缩放不改变全区间指标。"
        if chart
        else "这些记录没有可合并的收益曲线。"
    )
    diffs = "".join(differences)
    for row in result["runs"]:
        diffs += f"<h3>{escape(row['title'])} · 时间与版本</h3>"
        diffs += context_body(root, row["run_id"])
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
<th>失败或缺失原因</th>
</tr>{rows}</table>
</div>{chart}<p
class="note">{chart_note}费用、数据性质和模型假设请结合各自报告。</p>
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
                experiments_body(
                    self.runs_root, self.csrf_token, query, self._jobs().list()
                ),
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
        elif path in {
            "/datasets/collect",
            "/datasets/refresh",
            "/datasets/derivatives",
            "/datasets/free-derivatives",
        }:
            if path.endswith("/free-derivatives"):
                from quant_studio.free_derivative_datasets import FIELDS
                from quant_studio.free_derivative_datasets import (
                    collection_request as free_request,
                )

                request = free_request(*(field(data, name, "") for name in FIELDS))
            elif path.endswith("/derivatives"):
                from quant_studio.derivative_datasets import (
                    collection_request as derivative_request,
                )

                request = derivative_request(
                    *(
                        field(data, name, "")
                        for name in (
                            "name",
                            "template",
                            "provider",
                            "contracts_path",
                            "start",
                            "end",
                            "rights_note",
                            "dataset",
                            "max_cost_usd",
                        )
                    )
                )
            elif path.endswith("/refresh"):
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
