"""Show verified family conclusions separately from position publication."""

from __future__ import annotations

import hashlib
import json
from html import escape

from quant_studio import QuantStudioError
from quant_studio.counterfactual_view import source_snapshot
from quant_studio.nav import TABLE_FILES
from quant_studio.runner import safe_run_file

LABELS = {
    "base": "原策略",
    "position_neutral": "取消仓位模型降档",
    "style_neutral": "取消风格信号倾斜",
    "joint": "联合干预",
}


def load_counterfactual_view(directory, result):
    expected = result.get("research_view_sha256")
    if not expected:
        if result["status"] == "succeeded" or result.get("report"):
            raise QuantStudioError("反事实结果缺少已核验的候选族证据")
        return None
    path = safe_run_file(directory, "strategy-output/studio-view/view.json")
    try:
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError("展示证据已改变")
        view = json.loads(content)
        if (
            view["schema_version"] != "quant-studio.timing-counterfactual-view/v1"
            or view["project"] != "quant-timing"
        ):
            raise ValueError("未知反事实展示契约")
        complete = view["receipt"]["status"] == "complete"
        if (
            result["returncode"] != view["native_exit_code"]
            or result["status"] != ("succeeded" if complete else "failed")
            or result["report"] != "strategy-output/report.html"
            or result.get("evidence_kind") != view["evidence_kind"]
        ):
            raise ValueError("运行状态或报告路径与候选族证据不一致")
        native = safe_run_file(directory, "strategy-output")
        if source_snapshot(native) != view["source_files"]:
            raise ValueError("原生文件内容或文件集合已改变")
        if {p.name for p in path.parent.iterdir()} != {"view.json"}:
            raise ValueError("展示目录包含未核验文件")
        if any(
            safe_run_file(directory, name).exists()
            for name in {"nav.csv", "benchmark_nav.csv", *TABLE_FILES}
        ):
            raise ValueError("候选族不能混入单次研究曲线或表格")
        return view
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise QuantStudioError(f"反事实证据核验失败：{exc}") from exc


def counterfactual_panel(directory, view):
    if view is None:
        return ""
    receipt = view["receipt"]
    complete = receipt["status"] == "complete"
    status = "研究比较完成" if complete else "家族结论不可用"
    candidates = receipt["candidates"]
    passed = sum(item["status"] == "complete" for item in candidates.values())
    states = "".join(
        f"<tr><td>{escape(LABELS[name])}</td><td>"
        + ("完成" if item["status"] == "complete" else "失败")
        + f"</td><td>{escape(item.get('reason', ''))}</td></tr>"
        for name, item in candidates.items()
    )
    body = (
        '<section class="panel counterfactual"><h2>固定候选族比较</h2>'
        f"<p><strong>{status}</strong> · 完成候选{passed}/{len(candidates)}</p>"
        '<p class="banner">仓位发布：所有候选禁用。本次研究不更新已有账户。</p>'
        "<p>风格干预："
        + ("适用" if receipt["style_applicable"] else "不适用，原配置无风格模块")
        + "；新增独立前向日期：0。</p>"
        '<div class="table-scroll" tabindex="0"><table><thead><tr>'
        "<th>候选</th><th>研究状态</th><th>失败原因</th>"
        f"</tr></thead><tbody>{states}</tbody></table></div>"
        f"<p>{escape(receipt.get('source_error') or '')}</p>"
        "<details><summary>干预与比较口径</summary><p>"
        "固定原输入与费用，保留原预热和全部后续约束；取消模型降档不等于最终满仓。"
        "取消风格倾斜仍保留组基准和偏离限制。各路径完整重算。</p><p>"
        "效应为干预净收益减原策略，单位为百分点；联合交互为联合干预减两项单独干预再加原策略。"
        "分量差加总至效应，模型成本不等于实测滑点。测试折不重建账户、不拼接重叠日期。"
        "历史重放用于解释已知样本，不能据此挑选新策略或增加独立前向证据。</p></details>"
    )
    if complete:
        for period in receipt["periods"]:
            title = (
                "描述性全区间"
                if period["id"] == "descriptive"
                else "测试折" + period["id"].removeprefix("fold-")
            )
            body += (
                '<details class="counterfactual-period"'
                + (" open" if period is receipt["periods"][0] else "")
                + f"><summary>{escape(title)} · "
                + f"{escape(str(period['first_return_date']))}至"
                + f"{escape(str(period['last_return_date']))}</summary>"
                + _period_table(period)
                + "</details>"
            )
    else:
        body += '<p class="note">必需候选或输入核验失败，不生成效应和联合交互结论。</p>'
    base = f"/runs/{escape(directory.name)}/files/strategy-output/"
    links = " · ".join(
        f'<a href="{base}{name}"{download}>{label}</a>'
        for name, label, download in (
            ("periods.csv", "下载完整分折效应CSV", " download"),
            ("protocol.json", "查看冻结协议", ""),
            ("result.json", "查看原生核验结果", ""),
            ("report.html", "打开原生报告", ""),
        )
    )
    return (
        body + f"<p>{links}</p></section>"
        "<style>.counterfactual-period{margin-top:18px;"
        "border-top:1px solid #dce4ee;padding-top:12px}"
        ".counterfactual summary{cursor:pointer;font-weight:600}"
        ".counterfactual-components{margin-top:12px;overflow-wrap:anywhere}"
        ".counterfactual td:first-child{min-width:10em}"
        ".counterfactual .effect-bar{height:5px;border-radius:3px;background:#2563eb}"
        ".counterfactual .effect-bar.negative{background:#be3455}</style>"
        '<details class="panel"><summary>原策略配置</summary><pre>'
        + escape(json.dumps(view["effective_config"], ensure_ascii=False, indent=2))
        + "</pre></details>"
    )


def _period_table(period):
    effects = period["effects"]
    maximum = max(abs(item["effect"]) for item in effects.values())
    component_labels = {"base_cost": "模型基础成本", "impact_cost": "模型冲击成本"}
    rows = []
    breakdowns = []
    for name, item in effects.items():
        width = abs(item["effect"]) / maximum * 100 if maximum else 0
        negative = " negative" if item["effect"] < 0 else ""
        components = "".join(
            f"<li>{escape(component_labels.get(key, key))}："
            f"{value * 100:+.4f}个百分点</li>"
            for key, value in item["component_effects"].items()
        )
        rows.append(
            f"<tr><td>{escape(LABELS[name])}</td>"
            f"<td>{item['net_return'] * 100:+.4f}%</td>"
            f"<td>{item['effect'] * 100:+.4f}"
            f'<div class="effect-bar{negative}" aria-hidden="true" '
            f'style="width:{width:.2f}%"></div></td></tr>'
        )
        breakdowns.append(
            '<details class="counterfactual-components"><summary>'
            f"{escape(LABELS[name])}：展开分量差</summary>"
            f"<ul>{components}</ul>残差{item['reconciliation_residual']:.2e}"
            "</details>"
        )
    interaction = period["interaction"]
    interaction_text = (
        "不适用" if interaction is None else f"{interaction * 100:+.4f}个百分点"
    )
    return (
        f"<p>计分决策数：{period['n_decisions']}；联合交互：{interaction_text}</p>"
        '<div class="table-scroll" tabindex="0"><table><thead><tr>'
        "<th>候选</th><th>区间净收益</th><th>相对原策略效应/百分点</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
        + "".join(breakdowns)
    )
