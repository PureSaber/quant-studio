"""Research plan forms, with a native-config escape hatch and immutable executions."""

from __future__ import annotations

import copy
import json
from html import escape
from urllib.parse import parse_qs, urlparse

from quant_studio import QuantStudioError
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
        if isinstance(child, dict) and child:
            yield from _leaves(child, path)
        else:
            yield path, key, child


def _fields(value, prefix):
    parts = []
    for path, key, child in _leaves(value):
        name = escape(prefix + path, quote=True)
        label = escape(_LABELS.get(key, key))
        if isinstance(child, (dict, list)) or child is None:
            raw = json.dumps(child, ensure_ascii=False, indent=2)
            control = (
                f'<textarea name="{name}" rows="5" spellcheck="false">'
                f"{escape(raw)}</textarea>"
            )
        elif isinstance(child, bool):
            control = (
                f'<select name="{name}">'
                + "".join(
                    f'<option value="{val}" '
                    f"{'selected' if child == (val == 'true') else ''}>"
                    f"{label}</option>"
                    for val, label in (("true", "是"), ("false", "否"))
                )
                + "</select>"
            )
        else:
            kind = "number" if isinstance(child, (int, float)) else "text"
            if (
                isinstance(child, str)
                and len(child) == 10
                and child[4:5] == "-"
                and child[7:8] == "-"
            ):
                kind = "date"
            control = (
                f'<input type="{kind}" step="any" name="{name}" '
                f'value="{escape(str(child), quote=True)}">'
            )
        parts.append(
            f'<label class="recipe-field">{label}<small>{escape(path)}</small>'
            f"{control}</label>"
        )
    return "".join(parts)


def _read_fields(base, data, prefix):
    value = copy.deepcopy(base)
    for path, _, old in _leaves(base):
        raw = _single(data, prefix + path)
        if isinstance(old, (dict, list)) or old is None:
            try:
                parsed = json.loads(raw)
            except ValueError as exc:
                raise QuantStudioError(f"{path} 须为有效 JSON") from exc
        elif isinstance(old, bool):
            if raw not in {"true", "false"}:
                raise QuantStudioError("布尔值无效")
            parsed = raw == "true"
        elif isinstance(old, (int, float)):
            parsed = float(raw) if any(char in raw for char in ".eE") else int(raw)
        else:
            parsed = raw
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
    return (
        f'<input type="hidden" name="_csrf_token" value="{escape(token, quote=True)}">'
    )


def list_body(store, token):
    cards = "".join(
        f'<a class="panel" href="/research/{r["id"]}"><h2>{escape(r["name"])}</h2>'
        f"<p>{escape(load_template(r['template_id']).title)}</p>"
        f"<small>版本 {r['revision'][:12]}</small></a>"
        for r in store.list()
    )
    choices = "".join(
        f'<option value="{key}">{escape(load_template(key).title)}</option>'
        for key in template_ids()
    )
    return f"""<header class="page-head"><h1>研究方案</h1>
<p>选模板，改参数，保存并复用。任务使用提交时的固定版本。</p></header>
<section class="panel"><h2>新建研究</h2>
<form method="get" action="/research/new"><label>研究类型
<select name="template">{choices}</select></label>
<button class="btn">创建方案</button></form></section>
<div class="recipe-grid">{cards or "<p>还没有保存的研究方案。</p>"}</div>
<details class="panel"><summary>导入已导出的研究方案</summary>
<form method="post" action="/research/import">{_csrf(token)}<label>方案 JSON
<textarea name="document" rows="8" required></textarea></label>
<button class="btn">导入为新方案</button></form></details>"""


def editor_body(template_id, token, record=None):
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
    frozen = ""
    if record:
        actions = (
            '<button class="btn" name="action" value="preview_saved">'
            "预览已保存版本</button>"
        )
        if template.metadata.get("preflight_argv"):
            actions += (
                '<button class="btn" name="action" value="check_saved">'
                "预检已保存版本</button>"
            )
        actions += (
            '<button class="btn primary" name="action" value="execute_saved">'
            "运行已保存版本</button>"
        )
        frozen = f'''<section class="panel"><h2>运行保存的版本</h2>
<p>版本 <code>{revision[:12]}</code>。下方参数编辑后，请先保存；这里始终运行此版本。</p>
<form method="post" action="{target}">{_csrf(token)}
<input type="hidden" name="revision" value="{revision}">
<div class="actions">{actions}
<a class="btn" href="{target}/export?revision={revision}">导出方案</a>
<a class="btn" href="{target}/config?revision={revision}">导出原生配置</a>
</div></form></section>'''
    help_text = (
        "输入路径是这台服务端电脑的路径；远程设备使用同一份数据。"
        "日期、名单和规则应与所选数据一致，保存后先预检。"
    )
    external_fields = ""
    parameter_help = "字段来自原生研究模块；名单使用 JSON 数组，可增减条目。"
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
                + _fields(frozen_input["config"], "source:")
                + "</div>"
            )
    return f'''<header class="page-head"><h1>{escape(name)}</h1>
<p>{escape(template.summary)}</p>
<a href="/research">返回方案列表</a></header>{frozen}
<form class="panel" method="post" action="{target}">{_csrf(token)}
<input type="hidden" name="template_id" value="{escape(template_id, quote=True)}">
<input type="hidden" name="revision" value="{revision}">
<h2>方案设置</h2><div class="recipe-grid"><label>名称
<input name="name" maxlength="120" value="{escape(name, quote=True)}" required></label>
<label>服务端输入路径<input name="snapshot" value="{escape(snapshot, quote=True)}"
placeholder="留空时使用首次配置中该研究类型的输入"></label></div><p>{help_text}</p>
{external_fields}
<h2>研究参数</h2><p>{parameter_help}</p>
<div class="recipe-grid">{_fields(config, "cfg:")}{_fields(cli, "cli:")}</div>
<div class="actions"><button class="btn primary" name="action" value="save">
保存方案</button>
<button class="btn" name="action" value="copy">另存为新方案</button></div>
<details><summary>导入原生 YAML / JSON 配置</summary>
<p>此按钮使用文本框内容替代上面的研究参数，并保存为新版本。
运行参数与输入路径沿用表单。</p>
<textarea name="native_config" rows="14" spellcheck="false">
{escape(json.dumps(config, ensure_ascii=False, indent=2))}</textarea>
<button class="btn" name="action" value="import_config">导入配置并保存</button>
</details></form>'''


class ResearchHandler:
    recipe_store = None

    def _recipes(self):
        if self.recipe_store is None:
            type(self).recipe_store = RecipeStore(self.runs_root)
        return self.recipe_store

    def _research_get(self, path):
        from quant_studio.server import _layout

        store = self._recipes()
        query = parse_qs(urlparse(self.path).query)
        if path == "/research":
            body = list_body(store, self.csrf_token)
        elif path == "/research/new":
            template_id = query.get("template", ["synthetic-demo"])[0]
            body = editor_body(template_id, self.csrf_token)
        else:
            parts = path.removeprefix("/research/").split("/")
            record = store.get(parts[0], query.get("revision", [None])[0])
            if len(parts) == 2 and parts[1] in {"export", "config"}:
                content = record if parts[1] == "export" else record["config"]
                self._send_text(
                    json.dumps(content, ensure_ascii=False, indent=2),
                    content_type="application/json; charset=utf-8",
                )
                return
            if len(parts) != 1:
                raise QuantStudioError("未知研究方案路径")
            body = editor_body(record["template_id"], self.csrf_token, record)
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
                    )
                    self._redirect(f"/jobs/{job['job_id']}")
                return
            if action not in {"save", "copy", "import_config", "load_input"}:
                raise QuantStudioError("未知方案操作")
            template_id = _single(data, "template_id")
            old = store.get(recipe_id, revision) if recipe_id else None
            if old and old["template_id"] != template_id:
                raise QuantStudioError("不可改变已有方案的研究类型")
            template = load_template(template_id)
            name = _single(data, "name")
            snapshot = _single(data, "snapshot", "")
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
                config = _read_fields(base, data, "cfg:")
            cli = _read_fields(cli_base, data, "cli:")
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
                    input_config["config"], data, "source:"
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
            )
        self._redirect(f"/research/{record['id']}")
