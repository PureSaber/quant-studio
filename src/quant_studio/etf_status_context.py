"""Optional read-only enrichment of the old frozen daily v1 response."""

import hashlib
import json
from pathlib import Path

from quant_studio.datasets import file_hash


def history_context(profile_path):
    """Inspect verified earlier receipts separately from current passes."""
    context = {"earlier_success": None, "earlier_preflight": None, "errors": []}
    try:
        profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
        keys = {"profile_sha256": file_hash(profile_path)}
        for field, key in (
            ("recipe", "recipe_sha256"),
            ("forward_config", "operator_sha256"),
            ("master_refresh", "master_refresh_sha256"),
        ):
            keys[key] = file_hash(Path(profile[field])) if profile.get(field) else None
        context["current_identity"] = keys
        root = Path(profile["output_root"]).resolve()
        receipts = []
        for path in root.glob("*/operation.json"):
            try:
                if root not in path.resolve().parents:
                    raise ValueError("回执路径越界")
                value = json.loads(path.read_text(encoding="utf-8"))
                canonical = json.dumps(
                    {k: v for k, v in value.items() if k != "sha256"},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                if hashlib.sha256(canonical.encode()).hexdigest() != value.get(
                    "sha256"
                ):
                    raise ValueError("回执哈希不匹配")
                if any(value.get(key) != expected for key, expected in keys.items()):
                    receipts.append(value)
            except (OSError, ValueError, TypeError) as exc:
                context["errors"].append(f"{path.parent.name}：{exc}")
        for value in sorted(receipts, key=lambda row: row.get("started_at", "")):
            if value.get("state") == "completed":
                context["earlier_success"] = value
            if any(
                s.get("stage") == "preflight" and s.get("state") == "completed"
                for s in value.get("stages", [])
            ):
                context["earlier_preflight"] = value
    except (OSError, ValueError, KeyError, TypeError) as exc:
        context["errors"].append(str(exc))
    return context


def explain_issue(raw):
    """Explain known native codes and retain unclassified evidence."""
    text = str(raw)
    groups = [
        (
            (
                "ETF daily-price coverage failed:",
                "SESSION_DATA_MISSING",
                "SESSION_DATA_NOT_CAPTURED",
                "Source study snapshot is stale",
                "Source study benchmark is stale",
            ),
            "行情尚未形成完整的收盘快照",
            "本日预检或账户观察被阻断，既有账本保留",
            "检查最终日线和基准覆盖后，回到今日运行更新并预检",
            "/data-center",
            "查看行情与质量",
        ),
        (
            (
                "CALENDAR_MISSING",
                "CALENDAR_NOT_COVERED",
                "Exchange calendar does not cover",
                "explicitly known exchange session",
            ),
            "交易日历缺失或未覆盖所选日期",
            "无法认定交易日及观察窗口",
            "在数据质量页核对交易所日历覆盖，再生成新预览或更新",
            "/data-center",
            "检查交易日历",
        ),
        (
            ("CAPTURE_TIME_MISSING", "CAPTURE_TIME_INVALID"),
            "采集时间缺少时区或晚于当前时间",
            "无法证明行情在观察时已可获得",
            "核对原始采集时间与本机时钟，重新采集可验证快照",
            "/data-center",
            "检查采集证据",
        ),
        (
            ("TIMELY_ORDER_MISSING:", "historical_backfill_blocked"),
            "缺少及时订单证据，或请求的是历史观察日",
            "不能补造过去成交或新增历史前向证据",
            "打开账户检查失败日和订单时间；保留失败并评估后续窗口",
            "/accounts",
            "核查账户与订单证据",
        ),
        (
            (
                "ACCOUNT_IDENTITY_MISMATCH",
                "Registered account verification failed",
                "Paper account differs",
                "Input identity verification failed",
                "input prefix",
                "historical revision",
            ),
            "冻结账户或历史输入身份不一致",
            "观察停止，旧账本不能由新输入覆盖",
            "核对账户冻结定义和输入哈希，恢复正确绑定后先核验",
            "/accounts",
            "核验冻结账户",
        ),
        (
            (
                "Native code identity",
                "runtime code identity",
                "Source study code differs",
                "CONFIG_INVALID",
                "CONFIG_UNREADABLE",
                "approved hash",
                "Frozen baseline recipe changed",
                "configuration changed",
            ),
            "运行环境或已批准配置与冻结身份不一致",
            "原生门禁拒绝执行",
            "核对实际解释器、版本和配置；当前冻结账户继续使用原解释器",
            "/delivery",
            "检查运行身份与配置",
        ),
        (
            ("STALE_PREVIEW",),
            "登记预览已过期或其证据发生变化",
            "本次不会新建账户",
            "返回所属项目重新选择候选并预览",
            "/projects",
            "返回研究项目",
        ),
        (
            (
                "Complete the entire preregistered",
                "Research prerequisites failed",
                "Current trading-state history is incomplete",
                "required_history",
                "history coverage",
            ),
            "候选族或研究所需历史证据不完整",
            "暂不能登记模拟账户",
            "查看项目失败实验和原生预检，补齐指定历史后重跑完整候选族",
            "/projects",
            "检查研究与预检",
        ),
        (
            (
                "interrupted",
                "retryable_failure",
                "no terminal event",
                "prior native failure",
            ),
            "原生观察中断或保留了失败尝试",
            "此前成功观察保留，本次需人工处理",
            "先检查任务与账户锁；按原生显式重试规则恢复，不能绕过门禁",
            "/jobs",
            "检查中断与失败任务",
        ),
    ]
    for markers, cause, impact, action, href, label in groups:
        if any(marker in text for marker in markers):
            return dict(
                cause=cause,
                impact=impact,
                action=action,
                href=href,
                label=label,
                raw=text,
            )
    return dict(
        cause="尚未分类的原生异常，原因待核实",
        impact="此项没有足够证据判定影响，保留原始诊断",
        action="打开本页完整阶段回执，定位阶段与错误原文，再查看任务日志",
        href="#daily-evidence",
        label="定位原生阶段证据",
        raw=text,
    )
