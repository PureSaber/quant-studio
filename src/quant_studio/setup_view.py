"""Discoverable setup form and a review bound to the validated selection."""

import json
from html import escape
from urllib.parse import quote

from quant_studio import QuantStudioError
from quant_studio.setup import (
    empty_profile,
    inherited_profile,
    input_sources,
    repositories,
)


def _field(name, label, value, hint):
    return (
        f'<label><span>{escape(label)}</span><input type="text" name="{escape(name)}" '
        f'value="{escape(value)}" autocomplete="off" spellcheck="false">'
        f"<small>{escape(hint)}</small></label>"
    )


def setup_body(store, csrf_token, *, profile=None, error="", reviewed=None):
    restored = store.snapshot()
    if profile is None:
        try:
            profile = restored if restored is not None else inherited_profile()
        except QuantStudioError as exc:
            profile, error = empty_profile(), str(exc)
    values, pythons = profile["environment"], profile["python_by_repo"]
    status = (
        "已恢复保存的配置" if restored is not None else "尚未保存；当前沿用启动配置"
    )
    head = (
        '<header class="page-head"><p class="kicker">首次配置</p>'
        "<h1>连接你的研究工作区</h1>"
        '<p class="lede">填写已有仓库、Python和输入来源，先校验并预览，再保存。'
        "保存后继续选择模板，执行原生数据预检；填写路径本身不代表数据已通过核验。</p>"
        f'<p class="setup-state">{status}</p></header>'
    )
    if error:
        head += f'<p class="banner">{escape(error)}</p>'
    destination = (
        '<p class="note setup-path">配置仅保存到本次服务的本地文件：'
        f"{escape(str(store.path))}。重启使用相同保存位置即可恢复；"
        "留空字段沿用启动配置，填写的选择优先用于新请求。</p>"
    )
    if reviewed is not None:
        token, checks = reviewed
        document = escape(json.dumps(profile, ensure_ascii=False, indent=2))
        ready = [(title, message) for title, message in checks if message == "可运行"]
        pending = [(title, message) for title, message in checks if message != "可运行"]

        def rows(items):
            return "".join(
                f"<li><b>{escape(title)}</b>：{escape(message)}</li>"
                for title, message in items
            )

        return (
            head
            + destination
            + (
                '<section class="panel"><h2>校验结果与保存预览</h2>'
                "<p>以下是此次明确选择。未配置的应用可稍后补充。环境检查不会运行策略或改写输入。</p>"
                f"<ul>{rows(ready)}</ul><details><summary>"
                f"其他入口与待处理项（{len(pending)}）</summary>"
                f"<ul>{rows(pending)}</ul></details>"
                f"<pre>{document}</pre>"
                '<form method="post" action="/setup">'
                '<input type="hidden" name="_csrf_token" '
                f'value="{escape(csrf_token)}">'
                f'<input type="hidden" name="review_token" value="{escape(token)}">'
                '<div class="actions"><button class="btn primary" '
                'name="action" value="save">'
                "保存这份配置</button>"
                f'<a class="chip" href="/setup?review={quote(token)}">返回修改</a>'
                "</div></form></section>"
            )
        )
    python_fields = "".join(
        _field(
            f"python:{repo}",
            repo,
            pythons.get(repo, ""),
            "已有独立环境的Python绝对路径；无需配置的应用可留空",
        )
        for repo in repositories()
    )
    data_fields = "".join(
        _field(
            f"input:{name}",
            source["label"],
            values.get(name, ""),
            "已有研究配置文件的绝对路径"
            if source.get("kind") == "file"
            else "已有完整数据目录的绝对路径",
        )
        for name, source in input_sources().items()
    )
    return (
        head
        + destination
        + (
            '<form method="post" action="/setup" class="setup-form">'
            f'<input type="hidden" name="_csrf_token" value="{escape(csrf_token)}">'
            '<section class="panel"><h2>1.工作区</h2>'
            + _field(
                "workspace",
                "工作区目录",
                values.get("QUANT_WORKSPACE_ROOT", ""),
                "包含量化应用仓库的已有目录；合成样例无需工作区",
            )
            + '</section><section class="panel"><h2>2.应用运行环境</h2>'
            "<p>只填写此次使用的应用。各应用保留自己的依赖，服务不会安装或升级环境。</p>"
            f'<div class="fields">{python_fields}</div></section>'
            '<section class="panel"><h2>3.数据与研究配置</h2>'
            "<p>选择此次使用的已有输入；之后在模板中执行完整原生预检。</p>"
            f'<div class="fields">{data_fields}</div></section>'
            '<section class="panel"><h2>4.已有账户（可选）</h2>'
            + _field(
                "accounts",
                "已有账户来源配置文件",
                values.get("QUANT_STUDIO_ACCOUNTS", ""),
                "quant-studio.accounts/v1格式的已有JSON；账户详情使用quant-pipeline环境只读核验",
            )
            + '</section><div class="actions"><button class="btn primary" '
            'name="action" value="review">校验并预览配置</button>'
            '<a class="chip" href="/templates/synthetic-demo">先体验合成样例</a>'
            "</div></form>"
        )
    )
