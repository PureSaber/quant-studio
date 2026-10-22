"""Versioned hypotheses and append-only human decisions over saved evidence."""

import json
import re
import uuid
from datetime import UTC, date, datetime, timedelta, timezone

from quant_studio import QuantStudioError
from quant_studio.datasets import file_hash
from quant_studio.projects import ProjectStore, digest
from quant_studio.recipes import _atomic

FIELDS = {
    "mechanism": "经济机制",
    "conditions": "预期有效与失效条件",
    "benchmark": "基准与资金、成本口径",
    "primary_metric": "主要判断指标",
    "rejection_rule": "否定假设的条件",
    "experiments": "候选、消融与成本压力范围",
    "split_protocol": "训练、验证与测试划分",
}
ROLES = {
    "development": "开发样本",
    "retrospective": "回顾性样本",
    "prospective": "预登记未来区间（尚非完成的前向证据）",
}
VERDICTS = {"supported": "支持", "not_supported": "不支持", "inconclusive": "证据不足"}
ACTIONS = {
    "continue_observation": "继续观察",
    "revise_hypothesis": "修改假设",
    "stop_research": "停止研究",
    "nominate_paper": "提名独立模拟候选",
}


def validate_card(value, *, previous=None):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != set(FIELDS) | {
        "sample_role",
        "start",
        "end",
    }:
        raise QuantStudioError("研究卡字段不完整")
    for key in FIELDS:
        if (
            not isinstance(value[key], str)
            or not value[key].strip()
            or len(value[key]) > 4000
        ):
            raise QuantStudioError(f"{FIELDS[key]}须为1至4000字")
    if value["sample_role"] not in ROLES:
        raise QuantStudioError("样本用途无效")
    try:
        start, end = (
            date.fromisoformat(value["start"]),
            date.fromisoformat(value["end"]),
        )
    except (ValueError, TypeError) as exc:
        raise QuantStudioError("研究区间须为日期") from exc
    if start > end:
        raise QuantStudioError("研究开始日晚于结束日")
    # Identical historical cards remain readable/editable after their start date.
    # Any changed protocol is a new registration, not retroactive preregistration.
    if (
        value["sample_role"] == "prospective"
        and value != previous
        and start <= datetime.now(timezone(timedelta(hours=8))).date()
    ):
        raise QuantStudioError("未来区间必须晚于实际登记日，旧样本不能重新标为未来留出")
    return dict(value)


class DecisionStore:
    def __init__(self, root):
        self.projects = ProjectStore(root)
        self.root = self.projects.root

    def append(
        self,
        project_id,
        revision,
        *,
        verdict,
        action,
        rationale,
        evidence,
        candidate="",
        account_id="",
        next_review_date="",
    ):
        project = self.projects.get(project_id)
        if project["revision"] != revision:
            raise QuantStudioError("项目已更新，请刷新后再记录决策")
        if not project.get("research_card"):
            raise QuantStudioError("请先登记研究卡")
        if verdict not in VERDICTS or action not in ACTIONS:
            raise QuantStudioError("研究结论或后续行动无效")
        if not isinstance(next_review_date, str):
            raise QuantStudioError("下次复核日期须为 YYYY-MM-DD")
        if next_review_date:
            try:
                parsed = date.fromisoformat(next_review_date)
                if parsed.isoformat() != next_review_date:
                    raise ValueError("noncanonical date")
            except (ValueError, TypeError) as exc:
                raise QuantStudioError("下次复核日期须为 YYYY-MM-DD") from exc
        if (
            not isinstance(rationale, str)
            or not rationale.strip()
            or len(rationale) > 8000
        ):
            raise QuantStudioError("决策理由须为1至8000字")
        if (
            not isinstance(evidence, list)
            or not evidence
            or len(set(evidence)) != len(evidence)
        ):
            raise QuantStudioError("必须选择不重复的项目实验作为依据")
        experiments = {r["run_id"]: r for r in self.projects.experiments(project_id)}
        refs = []
        for identifier in evidence:
            if identifier not in experiments:
                raise QuantStudioError("决策证据不属于本项目")
            record = experiments[identifier]
            if action == "nominate_paper":
                from quant_studio.result_integrity import verify_result
                from quant_studio.run_record import load_run_result

                directory = self.projects.runs_root / identifier
                if record["status"] != "succeeded":
                    raise QuantStudioError("提名需要成功实验；失败证据可用于研究结论")
                verify_result(directory, load_run_result(directory))
                from quant_studio.etf_experiments import is_etf, read_family

                if not is_etf(self.projects.runs_root, identifier):
                    raise QuantStudioError("此提名入口仅接四ETF原生研究")
                family = read_family(self.projects.runs_root, identifier)
                if not family.get("family_complete") or not any(
                    r["candidate"] == candidate and r["status"] == "completed"
                    for r in family["results"]
                ):
                    raise QuantStudioError(
                        "须完成整个研究族，并选择其中已完成的原生候选"
                    )
            directory = self.projects.runs_root / identifier
            refs.append(
                {
                    "run_id": identifier,
                    "status": record["status"],
                    "result_sha256": file_hash(directory / "result.json"),
                    "request_sha256": file_hash(directory / "request.json"),
                    "research_project": record["research_project"],
                }
            )
        if action == "nominate_paper" and not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,100}", candidate
        ):
            raise QuantStudioError("提名须填写原生候选ID")
        account = None
        if account_id:
            from quant_studio.accounts import account_sources, inspect_source

            source = next((s for s in account_sources() if s.id == account_id), None)
            if source is None:
                raise QuantStudioError("账户未配置")
            inspection = inspect_source(source)
            account = {
                "source_id": source.id,
                "account_id": inspection["account_id"],
                "definition_sha256": inspection["definition_sha256"],
            }
        record = {
            "schema": "quant-studio.research-decision/v1",
            "id": uuid.uuid4().hex,
            "project_id": project_id,
            "project_revision": revision,
            "recorded_at": datetime.now(UTC).isoformat(),
            "actor": "human",
            "verdict": verdict,
            "action": action,
            "rationale": rationale,
            "evidence": refs,
            "candidate": candidate,
            "account": account,
            "next_review_date": next_review_date,
        }
        record["sha256"] = digest(record)
        directory = self.root / project_id / "decisions"
        directory.mkdir(exist_ok=True)
        _atomic(
            directory / (record["id"] + ".json"), json.dumps(record, ensure_ascii=False)
        )
        return record

    def list(self, project_id):
        self.projects.get(project_id)
        result = []
        for path in (self.root / project_id / "decisions").glob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if value["project_id"] != project_id or value["sha256"] != digest(
                    {k: v for k, v in value.items() if k != "sha256"}
                ):
                    raise ValueError("identity mismatch")
                self.projects.get(project_id, value["project_revision"])
                for ref in value["evidence"]:
                    directory = self.projects.runs_root / ref["run_id"]
                    if not directory.resolve().is_relative_to(self.projects.runs_root):
                        raise ValueError("evidence path escapes run root")
                    if any(
                        file_hash(directory / f"{name}.json") != ref[f"{name}_sha256"]
                        for name in ("result", "request")
                    ):
                        raise ValueError("evidence changed")
                result.append(value)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise QuantStudioError("决策或引用证据完整性核验失败") from exc
        return sorted(result, key=lambda r: r["recorded_at"], reverse=True)
