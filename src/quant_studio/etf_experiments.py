"""Read native ETF families, retaining failures and original metrics."""

import json
from html import escape

from quant_studio import QuantStudioError
from quant_studio.experiments import ExperimentStore
from quant_studio.result_integrity import verify_result
from quant_studio.run_record import load_run_result


def read_family(root, identifier):
    directory = ExperimentStore(root)._run(identifier)
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    if request["template_id"] != "etf-weekly-research":
        raise QuantStudioError("所选实验并非四ETF原生研究")
    result = load_run_result(directory)
    if result["status"] != "succeeded":
        return {
            "status": result["status"],
            "results": [],
            "error": "实验失败或尚未完成",
        }
    verify_result(directory, result, asset="strategy-output/studio-view.json")
    value = json.loads(
        (directory / "strategy-output/studio-view.json").read_text(encoding="utf-8")
    )
    if value.get("schema_version") != "quant.etf-research-view/v1":
        raise QuantStudioError("ETF原生研究视图契约不匹配")
    return {**value, "status": "succeeded"}


def is_etf(root, identifier):
    directory = ExperimentStore(root)._run(identifier)
    value = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    return value["template_id"] == "etf-weekly-research"


def compare_body(root, identifiers):
    from quant_studio.project_web import table

    if not 1 <= len(identifiers) <= 4 or len(set(identifiers)) != len(identifiers):
        raise QuantStudioError("请选择1至4个不同实验；每项展示全部候选及失败项")
    body = (
        "<h1>四 ETF 原生实验比较</h1>"
        "<p>基准与全部候选并列保留；历史结果为回顾性证据。</p>"
    )
    bases = []
    for identifier in identifiers:
        body += (
            '<section class="panel"><h2>'
            f'<a href="/runs/{identifier}">{identifier}</a></h2>'
        )
        try:
            view = read_family(root, identifier)
        except QuantStudioError as exc:
            body += f'<p role="alert">不可比较：{escape(str(exc))}</p></section>'
            bases.append(None)
            continue
        basis = view.get("comparison_basis")
        bases.append(basis)
        body += (
            f"<p>状态：{escape(view['status'])}；{escape(view.get('error', ''))}</p>"
        )
        rows = []
        for item in view["results"]:
            metrics = item.get("metrics") or {}
            rows.append(
                [
                    item["candidate"],
                    item["status"],
                    str(metrics.get("total_return", "不可用")),
                    str(metrics.get("max_drawdown", "不可用")),
                    str(item.get("comparison") or "未登记"),
                    str(item.get("error") or ""),
                ]
            )
        body += table(
            [
                "候选",
                "状态",
                "扣费收益（比例）",
                "最大回撤（比例）",
                "基准对照",
                "失败原因",
            ],
            rows,
        )
        body += "<details><summary>区间、资金、成本、快照与检验协议</summary><pre>"
        body += (
            escape(json.dumps(basis, ensure_ascii=False, indent=2))
            + "</pre></details></section>"
        )
    comparable = bool(bases) and all(b is not None and b == bases[0] for b in bases)
    message = (
        "区间、资金、成本、输入和检验协议一致；仅比较描述性结果。"
        if comparable
        else "不可比：存在不同区间、资金、费用、快照、检验协议或失败实验。未合并排名。"
    )
    return '<p role="status">' + message + "</p>" + body
