"""Human-facing labels without changing stored outcomes or financial evidence."""

from datetime import UTC, datetime
from html import escape

STATUS = {
    "queued": "排队中",
    "pending": "待运行",
    "prepared": "已准备",
    "running": "运行中（尚未完成）",
    "checking": "检查数据中",
    "cancelling": "正在取消",
    "cancelled": "已取消",
    "cancel_failed": "取消未确认",
    "interrupted": "已中断",
    "incomplete": "尚未完成，状态待确认",
    "checked": "数据检查通过",
    "check_failed": "数据检查未通过",
    "previewed": "仅预览",
    "succeeded": "已完成",
    "completed": "已完成",
    "failed": "失败",
    "blocked": "未执行",
    "skipped": "未执行",
    "verification_failed": "结果核验未通过",
    "saved": "已保存",
    "ready": "等待提交",
    "submitted": "已提交",
    "preparing": "准备中",
    "preflight_queued": "数据检查排队中",
    "preflight_running": "检查数据中",
    "preflight_failed": "数据检查未通过",
    "execution_queued": "运行排队中",
    "execution_running": "运行中（尚未完成）",
    "execution_failed": "运行失败",
    "unknown": "状态待确认",
    "expired": "租约失效",
}


# Import outcomes include the publication step, unlike generic job completion.
INTAKE_STATUS = {
    "prepared": "映射已确认，等待提交",
    "queued": "已排队，等待前序任务",
    "running": "正在执行全量检查",
    "succeeded": "已通过并发布",
    "blocked": "质量检查未通过",
    "failed": "导入失败，可查看原因后重试",
    "interrupted": "服务中断，原请求已保留",
    "cancelled": "已取消，原请求已保留",
}


def status_label(value):
    return STATUS.get(value, "状态待确认")


def display_time(value):
    try:
        stamp = (
            datetime.fromtimestamp(value, UTC)
            if type(value) in {int, float}
            else datetime.fromisoformat(value)
        )
        if stamp.tzinfo is not None:
            return stamp.astimezone().strftime("%Y-%m-%d %H:%M %z")
        return stamp.strftime("%Y-%m-%d %H:%M") + "（时区未记录）"
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    return str(value or "未记录")


def template_title(identifier):
    from quant_studio import QuantStudioError
    from quant_studio.templates import load_template

    try:
        return load_template(identifier).title
    except QuantStudioError:
        return {"notebook": "交互研究", "research-assistant": "研究助手"}.get(
            identifier, "后台任务"
        )


def progress_steps(job, metadata):
    action = job.get("action")
    if action not in {"workflow", "check", "execute"}:
        return ["排队", "执行任务", "收集结果"]
    steps = ["排队"]
    if action == "check" or (action == "workflow" and metadata.get("preflight_argv")):
        steps.append("检查数据")
    if action != "check":
        steps.append("运行策略")
        if metadata.get("verification_argv"):
            steps.append("核对账本")
    steps.append("收集结果")
    return steps


def progress_body(job, metadata):
    current = {
        "queued": "排队",
        "preparing": "准备任务",
        "native": "执行任务",
        "executing": "运行策略",
        "verifying": "核对账本",
        "collecting": "收集结果",
        "finished": status_label(job.get("status")),
    }.get(job.get("stage"), status_label(job.get("status")))
    steps = "".join(
        f"<li>{escape(step)}</li>" for step in progress_steps(job, metadata)
    )
    elapsed = ""
    try:
        start = datetime.fromisoformat(job["created_at"])
        end = (
            datetime.now(UTC)
            if job.get("status") in {"queued", "running", "cancelling"}
            else datetime.fromisoformat(job["updated_at"])
        )
        seconds = max(0, int((end - start).total_seconds()))
        minutes, seconds = divmod(seconds, 60)
        elapsed = f" · 提交后已过 {minutes} 分 {seconds} 秒（含排队）"
    except (KeyError, TypeError, ValueError):
        pass
    return f'<p>当前：{escape(current)}{elapsed}</p><ol class="run-steps">{steps}</ol>'


def missing_tool(message):
    return any(
        term in message
        for term in (
            "独立Python",
            "未配置Python",
            "Python不存在",
            "源码不存在",
            "未连接研究工具",
            "Worker",
        )
    )


def recovery_body(message):
    if missing_tool(message):
        title, href, action = "研究工具尚未连接", "/setup", "连接研究工具"
    elif any(
        term in message
        for term in ("数据不存在", "输入不存在", "数据缺失", "snapshot", "输入路径")
    ):
        title, href, action = "需要检查输入数据", "/data", "检查数据"
    else:
        title, href, action = (
            "本次运行未完成，请查看诊断后调整方案",
            "/projects",
            "返回研究项目",
        )
    link = f'<a class="btn" href="{href}">{action}</a>' if href else ""
    return f"""<section class="panel notice-error">
<h2>{title}</h2>{link}<details>
<summary>技术详情</summary>
<pre>{escape(message)}</pre>
</details>
</section>"""


def metric_cards(series, metadata, *, synthetic=False):
    """Only observed NAV measures; no assumed sampling rate or risk-free return."""
    missing = "不适用" if synthetic else "不可计算"
    values = [
        (
            "区间收益",
            f"{series.period_return:+.2%}"
            if series.period_return is not None
            else "不可计算",
            "从明确的期初资金计算"
            if series.initial_nav is not None
            else "首条至末条净值观测，不含首条之前的收益",
        ),
        (
            "最大回撤",
            f"{-series.max_drawdown:.2%}"
            if series.max_drawdown is not None
            else "不可计算",
            "相对历史净值峰值",
        ),
        (
            "期末净值",
            f"{series.ending:,.2f}",
            metadata.get("nav_currency", "沿用原始净值单位"),
        ),
        ("观测数量", str(len(series.rows)), "不等同于交易日数"),
        ("年化收益", missing, "未声明适用的年化口径"),
        ("夏普比率", missing, "缺少收益频率与无风险收益口径"),
    ]
    cards = "".join(
        f"""<div class="metric">
<span>{label}</span>
<strong>{escape(value)}</strong>
<small>{escape(str(note))}</small>
</div>"""
        for label, value, note in values
    )
    return f'<section class="metric-grid" aria-label="结果指标">{cards}</section>'


# Only unambiguous proportional transaction costs, not per-contract/currency fees.
RATE_FIELDS = {
    "commission",
    "slippage",
    "commission_rate",
    "slippage_rate",
    "stamp_rate",
    "stamp_tax_rate",
    "sfc_rate",
    "afrc_rate",
    "trading_rate",
    "settlement_rate",
}
