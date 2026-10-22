"""Read evidence status without converting successful runs into research gates."""

import json
from html import escape

from quant_studio import QuantStudioError
from quant_studio.result_integrity import verify_result
from quant_studio.run_record import load_run_result
from quant_studio.runner import safe_run_file

OBJECTIVE_LABELS = {
    "meets_objective": "目标点估计满足",
    "failed": "目标未满足",
    "insufficient_evidence": "目标证据不足",
}


def evidence_summary(directory):
    result = load_run_result(directory)
    if result.get("status") != "succeeded":
        return ["未完成或失败的实验；不计入验收"], []
    try:
        verify_result(directory, result)
        objectives, provisional = [], False
        for name in sorted(result["output_hashes"]):
            if not name.endswith("/result.json"):
                continue
            value = json.loads(safe_run_file(directory, name).read_text("utf-8"))
            if value.get("schema_version") != "quant.research-result/v1":
                continue
            provisional |= value.get("evidence_state") == "provisional"
            provisional |= value.get("provisional") is True
            evaluation = value.get("objective_evaluation") or {}
            label = OBJECTIVE_LABELS.get(evaluation.get("status"), "目标未评审")
            objectives.append(f"{value.get('candidate', name)}：{label}")
        provisional |= result.get("evidence_state") == "provisional"
        provisional |= result.get("provisional") is True
        label = (
            "临时证据（provisional）；不计入 A/B/C 验收"
            if provisional
            else "产物完整性已核验；经济有效性及未来门槛另行评审"
        )
        return [label], objectives
    except (OSError, ValueError, TypeError, AttributeError, QuantStudioError):
        return ["证据未核验；不计入验收"], []


def gate_a_summary(account, daily):
    if not account:
        return "未关联模拟账户"
    if not daily or not daily.get("operations"):
        return "当前原生运行状态未核实"
    native = daily.get("account") or {}
    if any(
        native.get(key) != account.get(key)
        for key in ("account_id", "definition_sha256")
    ):
        return "当前运行账户与决策所关联的定义不一致"
    acceptance = daily["operations"].get("acceptance") or {}
    count = acceptance.get("longest_consecutive")
    if type(count) is not int or count < 0:
        return "自然交易日凭证未核实"
    checked = daily.get("checked_at") or "时间未提供"
    return f"最长连续自然成功 {count}/6；核验时间 {checked}；不代表策略有效"


def summary_body(root, record, daily):
    review = record.get("next_review_date") or "尚未安排"
    body = f"<p>下次复核：{escape(review)}</p>"
    body += f"<p>Gate A：{escape(gate_a_summary(record.get('account'), daily))}</p>"
    for ref in record["evidence"]:
        labels, objectives = evidence_summary(root / ref["run_id"])
        text = "；".join(labels + (objectives or ["未提供原生目标评审结果"]))
        body += f"<p>实验 {escape(ref['run_id'])}：{escape(text)}</p>"
    body += "<p>Gate B/C 不由运行成功或点估计满足自动判定。</p>"
    return body
