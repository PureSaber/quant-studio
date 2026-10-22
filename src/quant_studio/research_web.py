"""Research plan forms, with a native-config escape hatch and immutable executions."""

from __future__ import annotations

import copy
import json
from html import escape
from urllib.parse import parse_qs, urlparse

from quant_studio import QuantStudioError
from quant_studio.presentation import display_time
from quant_studio.recipes import (
    RecipeStore,
    load_input_config,
    parse_config,
    recipe_template,
)
from quant_studio.runner import run
from quant_studio.settings import setting
from quant_studio.templates import load_template, render_template, template_ids

_LABELS = {
    "start_date": "开始日期",
    "end_date": "结束日期",
    "data_start": "行情开始日期",
    "data_end": "行情结束日期",
    "train_start": "训练开始日期",
    "test_start": "验证开始日期",
    "instruments": "股票名单与交易规则",
    "symbols": "研究标的",
    "top_n": "持仓数量",
    "initial_cash": "初始资金",
    "initial_capital": "初始资金",
    "factors": "因子名单",
    "provider": "数据提供方",
    "rebalance_sessions": "调仓间隔（交易日）",
    "rebalance_freq": "调仓频率",
    "symbols_limit": "标的数量上限",
}


def _leaves(value, prefix=""):
    for key, child in value.items():
        path = prefix + "/" + key.replace("~", "~0").replace("/", "~1")
        if key in {"benchmark_weights", "factor_bounds", "active_factor_bounds"} or (
            key == "allocation"
            and isinstance(child, dict)
            and child.get("protocol_version") == 2
        ):
            yield path, key, child
        elif isinstance(child, dict) and child:
            yield from _leaves(child, path)
        else:
            yield path, key, child


def _fields(
    value, prefix, knobs=(), *, human_units=False, currency=None, factor_catalog=()
):
    from decimal import Decimal

    from quant_studio.form_controls import ADVANCED, LABELS, help_text, special_control
    from quant_studio.form_presentation import (
        GROUPS,
        group_for,
        numeric,
        rate_echo,
        unit_for,
    )
    from quant_studio.presentation import RATE_FIELDS

    parts, advanced = [], []
    groups = {group: [] for group in GROUPS}
    for path, key, child in _leaves(value):
        name = escape(prefix + path, quote=True)
        knob = next(
            (k for k in knobs if k.get("path") == path[1:].replace("/", ".")), None
        )
        label = escape(
            knob.get("label", _LABELS.get(key, LABELS.get(key, "扩展参数")))
            if knob
            else _LABELS.get(key, LABELS.get(key, "扩展参数"))
        )
        universe_name = path in {"/universe", "/universe/name"}
        if universe_name:
            label = "股票池"
        group = (
            GROUPS[0]
            if universe_name or path.startswith("/universe/")
            else group_for(key)
        )
        scaled = human_units and key in RATE_FIELDS and numeric(child)
        is_numeric = numeric(child) and (
            not isinstance(child, str)
            or scaled
            or (knob and knob["type"] in {"decimal-string", "number", "int"})
        )
        unit = unit_for(key, currency=currency, rate=scaled) if is_numeric else ""
        if unit and "（" not in label:
            label += f"（{escape(unit)}）"
        display_value = Decimal(str(child)) * 10000 if scaled else child
        special = special_control(
            name, key, child, factor_risk=bool(value.get("risk_model"))
        )
        summary_value = ""
        if universe_name and child == "hs300":
            summary_value = ' data-summary-value="沪深300"'
            special = (
                f'<input type="hidden" name="{name}" value="hs300">'
                '<input value="沪深300" readonly aria-label="股票池">'
                "<small>模板固定</small>"
            )
        if key == "factors" and isinstance(child, list) and factor_catalog:
            catalog = {item["name"]: item["label"] for item in factor_catalog}
            catalog.update({v: "自定义因子" for v in child if v not in catalog})
            special = f'<input type="hidden" name="{name}" value="">' + "".join(
                f'<label class="check"><input type="checkbox" name="{name}" '
                f'value="{escape(code, quote=True)}" '
                f'data-factor-label="{escape(title, quote=True)}"'
                + (" checked" if code in child else "")
                + f">{escape(title)}"
                f'<code class="technical-name" hidden>{escape(code)}</code></label>'
                for code, title in catalog.items()
            )
        if knob and knob["type"] == "enum":
            from quant_studio.server import _choice_label

            options = "".join(
                f'<option value="{escape(str(v), quote=True)}"'
                + (" selected" if child == v else "")
                + ">"
                + escape(knob.get("choice_labels", {}).get(v, _choice_label(key, v)))
                + "</option>"
                for v in knob["choices"]
            )
            control = f'<select name="{name}">{options}</select>'
        elif special is not None:
            control = special
        elif isinstance(child, (dict, list)) or child is None:
            raw = json.dumps(child, ensure_ascii=False, indent=2)
            control = (
                f'''<textarea
name="{name}" rows="5" spellcheck="false">'''
                f"""{escape(raw)}</textarea>"""
            )
        elif isinstance(child, bool):
            control = (
                f'''<select
name="{name}">'''
                + "".join(
                    f'''<option
value="{val}" '''
                    f"{'selected' if child == (val == 'true') else ''}>"
                    f"""{label}</option>"""
                    for val, label in (("true", "是"), ("false", "否"))
                )
                + "</select>"
            )
        else:
            kind = "number" if is_numeric else "text"
            if (
                isinstance(child, str)
                and len(child) == 10
                and child[4:5] == "-"
                and child[7:8] == "-"
            ):
                kind = "date"
            bounds = ""
            if knob and kind == "number":
                for attribute, source in (("min", "minimum"), ("max", "maximum")):
                    if source in knob:
                        bound = Decimal(str(knob[source])) * (10000 if scaled else 1)
                        bounds += f' {attribute}="{bound}"'
            if key == "schema" or (knob and knob.get("readonly")):
                bounds += ' readonly aria-description="模板固定"'
            control = (
                f'''<input
type="{kind}" step="any"
name="{name}" '''
                f'value="{escape(str(display_value), quote=True)}"{bounds}>'
            )
        hint = f"= {rate_echo(child)}；1 bp = 0.01%" if scaled else help_text(key)
        if key == "factors" and factor_catalog:
            hint = "勾选模板已声明的因子；技术名称可按需显示。"
        wide = key in {"instruments", "allocation", "factors"}
        field = (
            f'<div class="recipe-field{" wide" if wide else ""}" '
            f'data-label="{label}" data-key="{escape(key)}" data-unit="{escape(unit)}" '
            f'data-group="{group}"{summary_value}>'
            + (
                f"<h3>{label}</h3>{control}"
                if wide
                else f"<label>{label}{control}</label>"
            )
            + f'<code class="technical-name" hidden>{escape(key)}</code>'
            + f"""<small class="field-help">{escape(hint)}</small>
<span class="field-error" aria-live="polite"></span>
</div>"""
        )
        (
            advanced
            if not knob
            and not universe_name
            and (
                key in ADVANCED
                or key not in _LABELS | LABELS
                or (
                    isinstance(child, str)
                    and child.endswith((".parquet", ".csv", ".json", ".yaml"))
                )
            )
            else groups[group]
        ).append(field)
    for group, fields in groups.items():
        if fields:
            parts.append(
                f'<fieldset class="wide parameter-group"><legend>{group}</legend>'
                '<div class="recipe-grid">' + "".join(fields) + "</div></fieldset>"
            )
    if advanced:
        parts.append(
            '<details class="wide"><summary>高级参数与数据口径</summary>'
            "<p>这些字段定义原生研究契约。修改前请核对数据与上游模块说明。</p>"
            '<div class="recipe-grid">' + "".join(advanced) + "</div></details>"
        )
    return "".join(parts)


def _read_fields(base, data, prefix, *, human_units=False):
    from decimal import Decimal

    from quant_studio.presentation import RATE_FIELDS

    value = copy.deepcopy(base)
    for path, key, old in _leaves(base):
        supplied = data.get(prefix + path, [])
        if key == "factors" and isinstance(old, list) and supplied[:1] == [""]:
            raw = json.dumps(data.pop(prefix + path)[1:])
        else:
            raw = _single(data, prefix + path)
        if isinstance(old, (dict, list)) or old is None:
            try:
                parsed = json.loads(raw)
            except ValueError as exc:
                if (
                    isinstance(old, list)
                    and old
                    and all(isinstance(v, str) for v in old)
                ):
                    import re

                    parsed = list(
                        dict.fromkeys(
                            v for v in re.split(r"[,，\s]+", raw.strip()) if v
                        )
                    )
                else:
                    raise QuantStudioError(f"{path} 须为有效 JSON") from exc
        elif isinstance(old, bool):
            if raw not in {"true", "false"}:
                raise QuantStudioError("布尔值无效")
            parsed = raw == "true"
        elif isinstance(old, (int, float)):
            parsed = float(raw) if any(char in raw for char in ".eE") else int(raw)
            if human_units and key in RATE_FIELDS:
                parsed = float(Decimal(raw) / 10000)
        else:
            parsed = raw
            if human_units and key in RATE_FIELDS:
                from quant_studio.form_presentation import numeric

                if numeric(old):
                    parsed = format(Decimal(raw) / 10000, "f")
        parts = [p.replace("~1", "/").replace("~0", "~") for p in path[1:].split("/")]
        target = value
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = parsed
    return value


def _single(data, key, default=None):
    values = data.pop(key, [default])
    if len(values) != 1 or values[0] is None:
        raise QuantStudioError(f"字段缺少或重复：{key}")
    return values[0]


def _csrf(token):
    return f'''<input
type="hidden"
name="_csrf_token"
value="{escape(token, quote=True)}">'''


def list_body(store, token):
    cards = "".join(
        f"""<a
class="panel"
href="/research/{r["id"]}">
<h2>{escape(r["name"])}</h2>"""
        f"""<p>{escape(load_template(r["template_id"]).title)}</p>"""
        f"""<small>版本 {r["revision"][:12]}</small>
</a>"""
        for r in store.list()
    )
    choices = "".join(
        f'''<option
value="{key}">{escape(load_template(key).title)}</option>'''
        for key in template_ids()
    )
    return f"""<header
class="page-head">
<h1>研究方案</h1>
<p>选模板，改参数，保存并复用。任务使用提交时的固定版本。</p>
</header>
<section
class="panel">
<h2>新建研究</h2>
<form
method="get"
action="/research/new">
<label>研究类型
<select
name="template">{choices}</select>
</label>
<button
class="btn">创建方案</button>
</form>
</section>
<div
class="recipe-grid">{cards or "<p>还没有保存的研究方案。</p>"}</div>
<details
class="panel">
<summary>导入已导出的研究方案</summary>
<form
method="post"
action="/research/import">{_csrf(token)}<label>方案 JSON
<textarea
name="document" rows="8" required>
</textarea>
</label>
<button
class="btn">导入为新方案</button>
</form>
</details>"""


def editor_body(
    template_id,
    token,
    record=None,
    datasets=None,
    selected_dataset=None,
    project=None,
    next_version=1,
):
    from quant_studio.form_controls import EDITOR_SCRIPT

    template = load_template(template_id)
    config = record["config"] if record else render_template(template).config
    cli = (
        record["cli"]
        if record
        else {
            k["name"]: k["default"] for k in template.knobs if k.get("target") == "cli"
        }
    )
    target = f"/research/{record['id']}" if record else "/research/new"
    name = record["name"] if record else template.title + " · 我的研究"
    source = template.metadata.get("input_source", {})
    snapshot = (
        (record or {}).get("snapshot") or setting(source.get("environment", "")) or ""
    )
    revision = (record or {}).get("revision", "")
    selected_dataset = selected_dataset or (record or {}).get("dataset_id")
    selected_item = next(
        (r for r in datasets or [] if r["id"] == selected_dataset), None
    )
    if selected_item:
        snapshot = selected_item["path"]
        if record is None and template.metadata.get("free_derivative_source"):
            config = copy.deepcopy(config)
            config.update(
                {
                    k: v
                    for k, v in selected_item.get("suggested_config", {}).items()
                    if k in {"mode", "legs", "product"} and isinstance(v, str)
                }
            )
        if record is None and template_id == "hk-equity-daily":
            from datetime import date, timedelta

            config = copy.deepcopy(config)
            try:
                first, last = (
                    date.fromisoformat(selected_item["start"]),
                    date.fromisoformat(selected_item["end"]),
                )
                config.update(
                    data_start=str(first),
                    data_end=str(last),
                    train_start=str(first + timedelta(days=35)),
                    test_start=str(first + (last - first) // 2),
                )
            except (ValueError, TypeError):
                pass
            known = [
                i
                for i in config["instruments"]
                if i["symbol"] in selected_item["symbols"]
            ]
            if known:
                config["instruments"] = known
                config["top_n"] = min(config["top_n"], len(known))
    options = '<option value="">使用下方已有路径（高级）</option>'
    for item in datasets or []:
        selected = " selected" if item["id"] == selected_dataset else ""
        options += f'''<option
value="{item["id"]}"{selected}>{escape(item["name"])} ·
{escape(str(item["start"]))} 至 {escape(str(item["end"]))}</option>'''
    data_picker = f"""<label>数据集<select
name="dataset_id">{options}</select>
</label>
<p>
<a
href="/datasets">选择、登记或更新数据集</a>
</p>"""
    if template.kind == "synthetic":
        data_picker = '<p class="note">使用内置合成数据，无需连接外部数据。</p>'
    frozen = (
        '<p class="version-state" id="version-state">'
        "尚未保存；运行时会保存当前参数。</p>"
    )
    if record:
        frozen = f'''<p class="version-state" id="version-state">已保存 ·
{escape(display_time(record["created_at"]))}。
运行将使用本次提交的参数。</p>
<details><summary>已保存版本与导出</summary><p>版本标识：<code>{revision}</code></p>
<a href="{target}/export?revision={revision}">导出方案</a> ·
<a href="{target}/config?revision={revision}">导出原生配置</a></details>'''
    binding = ""
    if project:
        binding = f'''<input type="hidden"
name="project_id"
value="{project["id"]}">
<input type="hidden"
name="project_revision"
value="{project["revision"]}">'''
        frozen = (
            f"""<p>
<a
href="/projects/{project["id"]}?tab=plan">返回 {escape(project["name"])}</a>
</p>"""
            + frozen
        )
    help_text = (
        "输入路径是这台服务端电脑的路径；远程设备使用同一份数据。"
        "日期、名单和规则应与所选数据一致。运行时先检查数据。"
    )
    external_fields = ""
    parameter_help = "名单可逐项编辑，复杂配置保留高级导入入口。"
    derivative = template_id in {
        "options-research",
        "global-futures-research",
        "options-free-sources",
        "global-futures-free-sources",
    }
    if derivative:
        parameter_help = template.metadata["data_help"]
    if template_id == "hk-equity-daily":
        parameter_help += "模板中的五只港股是可替换示例。"
    if source.get("kind") == "file":
        frozen_input = (record or {}).get("input_config")
        external_fields = (
            "<h2>原生研究配置</h2><p>加载一次后，配置内容会随方案保存。"
            "修改原文件不会改变已保存的版本。</p>"
            '<button class="btn" name="action" value="load_input">'
            "从输入路径加载原生配置并保存</button>"
        )
        if frozen_input:
            external_fields += (
                '<div class="recipe-grid">'
                + _fields(frozen_input["config"], "source:", human_units=True)
                + "</div>"
            )
    return f'''<header
class="page-head">
<h1>{escape(name)}</h1>
<p>{escape(template.summary)}</p>
<a
href="/research">返回方案列表</a> {
        f'''<a
href="{target}/history">版本历史与改动对比</a>'''
        if record
        else ""
    }</header>{frozen}
<form
class="panel" id="recipe-editor"
method="post"
action="{target}">{_csrf(token)}{binding}
<input type="hidden" name="ui_units" value="bp">
<input
type="hidden"
name="template_id"
value="{escape(template_id, quote=True)}">
<input
type="hidden"
name="revision"
value="{revision}">
<h2>方案设置</h2>
<div
class="recipe-grid">
<label>名称
<input
name="name"
maxlength="120"
value="{escape(name, quote=True)}" required>
</label>
{data_picker}<details>
<summary>高级：服务端输入路径</summary>
<label>输入路径<input
name="snapshot"
value="{escape(snapshot, quote=True)}"
placeholder="留空时使用首次配置中该研究类型的输入">
</label>
</details>
</div>
<p>{help_text}</p>
<label>本次修改说明<textarea
name="note"
maxlength="2000" rows="2"
placeholder="例如：将形成窗口从 120 改为 100">
</textarea>
</label>
{external_fields}
<h2>研究参数</h2>
<p>{parameter_help}</p>
<label class="check"><input type="checkbox" id="show-technical-names">
显示技术名称</label>
<div
class="recipe-grid">{
        _fields(
            config,
            "cfg:",
            template.knobs,
            human_units=True,
            currency=template.metadata.get("nav_currency"),
            factor_catalog=template.metadata.get("factor_catalog", ()),
        )
    }{_fields(cli, "cli:", human_units=True)}</div>
<aside class="plan-summary" aria-label="当前方案摘要"><h2>当前方案摘要</h2>
<div id="plan-summary"></div><p>运行会固定本次保存的方案版本，
之后的修改不会影响这次结果。运行时重新检查数据。</p></aside>
<div
class="actions">
<button
class="btn primary"
name="action"
value="save_run">保存为 v{next_version} 并运行</button>
{
        '<button class="btn" name="action" value="save_check">只检查数据</button>'
        if template.metadata.get("preflight_argv")
        else ""
    }
<button class="btn" name="action" value="save">保存方案</button>
<button
class="btn"
name="action"
value="copy">另存为新方案</button>
</div>
<details>
<summary>导入原生 YAML / JSON 配置</summary>
<p>此按钮使用文本框内容替代上面的研究参数，并保存为新版本。
运行参数与输入路径沿用表单。</p>
<textarea
name="native_config" rows="14" spellcheck="false">
{escape(json.dumps(config, ensure_ascii=False, indent=2))}</textarea>
<button
class="btn"
name="action"
value="import_config">导入配置并保存</button>
</details>
</form>{EDITOR_SCRIPT}<script>
const editor = document.getElementById("recipe-editor");
editor.addEventListener("input", event => {{
 if (event.target.id === "show-technical-names") return;
 const state = document.getElementById("version-state");
 state.textContent = "有未保存修改；点击运行会保存并使用当前参数。";
 state.classList.add("dirty");
 editor.querySelector('[value="save_run"]').textContent = "保存为 v{
        next_version
    } 并运行";
}});
</script>'''


class ResearchHandler:
    recipe_store = None

    def _recipes(self):
        if self.recipe_store is None:
            type(self).recipe_store = RecipeStore(self.runs_root)
        return self.recipe_store

    def _research_get(self, path):
        from quant_studio.datasets import DatasetStore
        from quant_studio.server import _layout
        from quant_studio.workbench_web import history_body

        store = self._recipes()
        query = parse_qs(urlparse(self.path).query)
        project = None
        if query.get("project"):
            from quant_studio.projects import ProjectStore

            project = ProjectStore(self.runs_root).get(
                query["project"][0], query.get("project_revision", [None])[0]
            )
        if path == "/research":
            body = list_body(store, self.csrf_token)
        elif path == "/research/new":
            template_id = query.get("template", ["synthetic-demo"])[0]
            datasets = DatasetStore(self.runs_root)
            selected = query.get("dataset", [None])[0]
            if selected:
                datasets.select(selected, template_id)
            body = editor_body(
                template_id,
                self.csrf_token,
                datasets=datasets.list(template_id),
                selected_dataset=selected,
                project=project,
            )
        else:
            parts = path.removeprefix("/research/").split("/")
            record = store.get(parts[0], query.get("revision", [None])[0])
            if len(parts) == 2 and parts[1] == "history":
                body = history_body(
                    store, record["id"], parse_qs(urlparse(self.path).query)
                )
                self._send_html(_layout("版本历史", body, "research"))
                return
            if len(parts) == 2 and parts[1] in {"export", "config"}:
                content = record if parts[1] == "export" else record["config"]
                self._send_text(
                    json.dumps(content, ensure_ascii=False, indent=2),
                    content_type="application/json; charset=utf-8",
                )
                return
            if len(parts) != 1:
                raise QuantStudioError("未知研究方案路径")
            body = editor_body(
                record["template_id"],
                self.csrf_token,
                record,
                DatasetStore(self.runs_root).list(record["template_id"]),
                project=project,
                next_version=len(store.history(record["id"])) + 1,
            )
        self._send_html(_layout("研究方案", body, "research"))

    def _research_post(self, path, data):
        store = self._recipes()
        if path == "/research/import":
            record = store.import_document(_single(data, "document"))
            if data:
                raise QuantStudioError("未知字段")
        else:
            recipe_id = (
                None if path == "/research/new" else path.removeprefix("/research/")
            )
            action = _single(data, "action")
            revision = _single(data, "revision", "")
            if action in {"preview_saved", "check_saved", "execute_saved"}:
                project_id = _single(data, "project_id", "")
                project_revision = _single(data, "project_revision", "")
                if bool(project_id) != bool(project_revision):
                    raise QuantStudioError("研究项目须同时指定身份与版本")
                project_ref = (
                    {"id": project_id, "revision": project_revision}
                    if project_id
                    else None
                )
                if not recipe_id or not revision or data:
                    raise QuantStudioError("运行须指定已保存的唯一版本")
                record = store.get(recipe_id, revision)
                if action == "preview_saved":
                    result = run(
                        recipe_template(record),
                        execute=False,
                        runs_root=self.runs_root,
                        snapshot=record["snapshot"],
                    )
                    self._redirect(f"/runs/{result.run_id}")
                else:
                    job = self._jobs().submit(
                        action.removesuffix("_saved"),
                        record["template_id"],
                        {},
                        snapshot=record["snapshot"],
                        profile=self._request_profile,
                        recipe=record,
                        research_project=project_ref,
                    )
                    self._redirect(f"/jobs/{job['job_id']}")
                return
            if action not in {
                "save",
                "copy",
                "import_config",
                "load_input",
                "save_run",
                "save_check",
            }:
                raise QuantStudioError("未知方案操作")
            units = _single(data, "ui_units", "native")
            if units not in {"native", "bp"}:
                raise QuantStudioError("未知参数单位")
            human_units = units == "bp"
            project_id = _single(data, "project_id", "")
            project_revision = _single(data, "project_revision", "")
            project = None
            if project_id:
                from quant_studio.projects import ProjectStore

                projects = ProjectStore(self.runs_root)
                project = projects.get(project_id)
                if project["revision"] != project_revision:
                    raise QuantStudioError("项目已更新，请重新打开后提交")
            elif project_revision:
                raise QuantStudioError("缺少项目身份")
            template_id = _single(data, "template_id")
            old = store.get(recipe_id, revision) if recipe_id else None
            if old and old["template_id"] != template_id:
                raise QuantStudioError("不可改变已有方案的研究类型")
            template = load_template(template_id)
            name = _single(data, "name")
            note = _single(data, "note", "")
            dataset_id = _single(data, "dataset_id", "") or None
            snapshot = _single(data, "snapshot", "")
            if dataset_id:
                from quant_studio.datasets import DatasetStore

                snapshot = DatasetStore(self.runs_root).select(dataset_id, template_id)
            source = template.metadata.get("input_source", {})
            snapshot = snapshot or setting(source.get("environment", "")) or ""
            native = _single(data, "native_config", "")
            base = old["config"] if old else render_template(template).config
            cli_base = (
                old["cli"]
                if old
                else {
                    k["name"]: k["default"]
                    for k in template.knobs
                    if k.get("target") == "cli"
                }
            )
            if action == "import_config":
                config = parse_config(native)
                # Fields from the visible form are intentionally superseded by import.
                for key in list(data):
                    if key.startswith("cfg:"):
                        data.pop(key)
            else:
                config = _read_fields(base, data, "cfg:", human_units=human_units)
            cli = _read_fields(cli_base, data, "cli:", human_units=human_units)
            input_config = copy.deepcopy((old or {}).get("input_config"))
            if action == "load_input" or (
                source.get("kind") == "file" and snapshot and input_config is None
            ):
                input_config = load_input_config(template_id, snapshot)
                for key in list(data):
                    if key.startswith("source:"):
                        data.pop(key)
            elif input_config:
                if snapshot != input_config["source_path"]:
                    raise QuantStudioError("输入文件已改变，请点击加载原生配置后再保存")
                input_config["config"] = _read_fields(
                    input_config["config"], data, "source:", human_units=human_units
                )
            if data:
                raise QuantStudioError("未知方案字段")
            record = store.save(
                name,
                template_id,
                config,
                cli=cli,
                snapshot=snapshot,
                recipe_id=None if action == "copy" else recipe_id,
                expected=revision,
                input_config=input_config,
                note=note,
                dataset_id=dataset_id,
            )
            if project:
                refs = [r for r in project["recipes"] if r["id"] != record["id"]]
                refs.append({"id": record["id"], "revision": record["revision"]})
                project = projects.save(
                    project["name"],
                    project["question"],
                    project_id=project_id,
                    expected=project_revision,
                    datasets=project["datasets"],
                    recipes=refs,
                    notes=project["notes"],
                )
            if action in {"save_run", "save_check"}:
                job = self._jobs().submit(
                    "workflow" if action == "save_run" else "check",
                    record["template_id"],
                    {},
                    snapshot=record["snapshot"],
                    profile=self._request_profile,
                    recipe=record,
                    research_project={
                        "id": project["id"],
                        "revision": project["revision"],
                    }
                    if project
                    else None,
                )
                self._redirect(f"/jobs/{job['job_id']}")
                return
            if project:
                self._redirect(
                    f"/research/{record['id']}?project={project['id']}&project_revision={project['revision']}"
                )
                return
        self._redirect(f"/research/{record['id']}")
