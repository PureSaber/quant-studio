"""One operational landing page with evidence links and explicit actions."""

import json
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from quant_studio import QuantStudioError
from quant_studio.etf_daily import inspect_daily, request
from quant_studio.project_web import table
from quant_studio.workbench_web import csrf, field

LABELS = {
    "completed": "已完成",
    "research_ready": "研究预检通过",
    "ready": "可观察",
    "blocked": "已阻断",
    "collection_retryable": "等待数据，稍后补试",
    "waiting_for_slot": "等待下次补试",
    "collection_exhausted": "当日补试已结束，数据仍未就绪",
    "needs_attention": "需要处理",
    "execution_failed": "模拟观察失败",
    "market_closed": "休市",
    "preflight_blocked": "研究预检阻断",
    "verification_failed": "身份核验失败",
    "paused": "暂停",
    "running": "执行中",
    "interrupted": "已中断",
    "retryable_failure": "需显式重试",
    "data_missing": "数据缺失",
    "outside_window": "不在观察区间",
    "not_due": "尚未到时",
    "sealed": "已封存",
    "candidate_required": "需要候选决策与登记",
    "pending": "等待首个观察日",
    "awaiting_observation": "已进入窗口，等待首条观察",
    "observing": "观察中",
    "ended_unsealed": "观察窗口结束，尚未封存",
    "historical_backfill_blocked": "历史回填已阻断",
    "passed": "通过",
    "failed": "失败",
}
STAGES = {
    "master_refresh": "官方主表证据更新",
    "collection": "数据更新",
    "preflight": "研究预检",
    "frozen_identity": "冻结身份",
    "observe": "账户观察",
    "verify": "账本核验",
}


def local_time(value, empty="尚无记录"):
    if not value:
        return empty
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            return parsed.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
                "%Y-%m-%d %H:%M:%S（北京时间）"
            )
    except (ValueError, TypeError):
        pass
    return str(value)


def issue_text(value):
    text = str(value)
    prefix = "ETF daily-price coverage failed: "
    if text.startswith(prefix):
        try:
            missing = json.loads(text[len(prefix) :])[
                "missing_after_first_available_bar"
            ]
            if isinstance(missing, dict) and missing:
                items = [
                    f"{symbol}（{'、'.join(dates)}）"
                    for symbol, dates in sorted(missing.items())
                ]
                return "缺少最终日线：" + "；".join(items) + "。完整行情快照尚未更新。"
        except (ValueError, KeyError, TypeError):
            pass
    if text.startswith("TIMELY_ORDER_MISSING:"):
        return (
            "缺少开盘前已经完成的订单记录，无法证明成交时点成立；"
            "本次观察已阻断，不能补造过去成交。"
        )
    return text


def details(value, label="查看原生证据"):
    return (
        f"<details><summary>{label}</summary><pre>"
        + escape(json.dumps(value, ensure_ascii=False, indent=2))
        + "</pre></details>"
    )


def today_body(token, root=None, query=None):
    body = """<header class="page-head"><h1>今日运行 · 四 ETF</h1>
<p>510300 · 510500 · 159915 · 588000；日频数据、周频基线、模拟账户。</p>
<a class="btn" href="/projects">研究项目与决策</a>
<a class="btn" href="/data-center">数据更新与质量</a>
<a class="btn" href="/delivery">运行环境</a></header>"""
    try:
        value = inspect_daily()
    except QuantStudioError as exc:
        return (
            body
            + '<section class="panel"><h2>尚未就绪</h2>'
            + f"<p>{escape(str(exc))}</p></section>"
        )
    if value.get("operations"):
        from quant_studio.etf_operations_web import body as operations_body

        return body + operations_body(
            value, token, root, (query or {}).get("recovery", [None])[0]
        )
    data, latest = value["data"], value.get("latest") or {}
    account = value.get("account") or {}
    history = value.get("history_context") or {}
    body += '<section class="panel"><h2>四项状态分别核对</h2>'
    body += table(
        ["范围", "当前结论", "证据范围"],
        [
            [
                "数据",
                "已覆盖应到交易日" if data["fresh"] else "收盘行情待更新",
                data["covered_through"],
            ],
            [
                "研究预检",
                "当前配置有通过记录"
                if value.get("preflight_passed_at")
                else "当前配置尚无完整通过记录",
                "历史通过不能替代当前配置预检",
            ],
            [
                "模拟账户",
                LABELS.get(account.get("state"), account.get("state", "尚未登记")),
                "无观察数据不等于零收益",
            ],
            [
                "调度时段",
                "当日采集时段已结束"
                if data["fresh"]
                and (value.get("schedule") or {}).get("state") == "collection_exhausted"
                else LABELS.get(
                    (value.get("schedule") or {}).get("state"),
                    (value.get("schedule") or {}).get("state", "未提供"),
                ),
                "原生配置的时段判断；实际触发以任务及运行回执为准",
            ],
        ],
    )
    if not value.get("preflight_passed_at"):
        body += (
            "<p>当前配置没有匹配的预检历史，不代表此前从未成功。"
            "配置、冻结配方或账户绑定变化会划分新的记录范围。</p>"
        )
    for key, label in (
        ("earlier_preflight", "较早配置的预检通过证据"),
        ("earlier_success", "较早配置的成功运行证据"),
    ):
        if history.get(key):
            body += details(history[key], label + "（不作为当前配置通过）")
    if history.get("errors"):
        body += details(history["errors"], "部分历史证据无法核验")
    body += "</section>"
    research_input = value.get("research_input") or latest
    if research_input.get("research_recipe"):
        choices = '<option value="">新建研究项目</option>'
        if root is not None:
            from quant_studio.projects import ProjectStore

            choices += "".join(
                f'<option value="{p["id"]}">{escape(p["name"])}</option>'
                for p in ProjectStore(root).list()
            )
        body += f'''<form method="post" action="/today/run">{csrf(token)}
<input type="hidden" name="command" value="connect-project">
<input type="hidden" name="operation" value="{research_input["id"]}">
<p>可连接的研究快照截止：{escape(research_input["data"]["covered_through"])}</p>
<label>连接到项目<select name="project_id">{choices}</select></label>
<button class="btn">将此快照与冻结配方连接到研究项目</button></form>'''
    body += '<section class="panel"><h2>数据更新了吗？</h2>'
    body += table(
        ["应到交易日", "实际截止", "行情采集时间", "新鲜度"],
        [
            [
                value["expected_session"],
                data["covered_through"],
                local_time(data["captured_at"]),
                "已覆盖" if data["fresh"] else "需要更新",
            ]
        ],
    )
    schedule = value.get("schedule")
    if schedule:
        state = schedule["state"]
        label = "到达采集时段" if state == "ready" else LABELS.get(state, state)
        if state == "collection_exhausted" and data["fresh"]:
            label = "当日采集时段已结束"
        body += f"<p>运行安排：{escape(label)}</p>"
        body += "<p>北京时间采集时段：" + escape("、".join(schedule["slots"])) + "</p>"
        body += (
            "<p>下次补试："
            + escape(local_time(schedule.get("next_attempt_at"), "本轮无后续补试"))
            + "</p>"
        )
        if state == "collection_exhausted":
            if data["fresh"]:
                body += "<p>当天完整行情已到齐，请查看下方账户状态。</p>"
            else:
                body += (
                    "<p>本日不再自动采集或观察，保留最近成功状态。"
                    "请检查数据源发布情况。</p>"
                )
    body += (
        "<p>当前配置最近完整预检通过："
        + escape(local_time(value.get("preflight_passed_at")))
        + "</p>"
    )
    for command, label in (
        ("refresh", "更新到最新已收盘交易日并预检"),
        ("observe", "核验并观察已登记模拟账户"),
    ):
        body += f'''<form method="post" action="/today/run">{csrf(token)}
<input type="hidden" name="command" value="{command}">
<button class="btn">{label}</button></form>'''
    body += '</section><section class="panel"><h2>今天执行了吗？</h2>'
    current = latest.get("state", "尚无记录")
    if not latest and account.get("state") == "pending":
        current = "等待首个观察日"
    body += f"<p>本次状态：{escape(LABELS.get(current, current))}</p>"
    if latest and latest.get("as_of") != value["expected_session"]:
        body += (
            "<p>最近回执不属于当前应到交易日；今日尚无匹配的运行证据。"
            "以下保留较早运行供核查。</p>"
        )
    body += f"<p>最近运行：{escape(local_time(latest.get('started_at')))}</p>"
    stages = {s["stage"]: s for s in latest.get("stages", [])}
    body += table(
        ["阶段", "状态"],
        [
            [STAGES.get(s["stage"], s["stage"]), LABELS.get(s["state"], s["state"])]
            for s in stages.values()
        ],
    )
    success = value.get("last_success") or {}
    body += f"<p>最近成功观察：{escape(success.get('as_of', '尚无成功观察'))}</p>"
    forward = value.get("forward")
    if forward:
        if account:
            body += (
                f"<p>模拟账户：{escape(account['account_id'])}；"
                f"实际登记：{escape(local_time(account['created_at']))}；"
                f"观察起点：{escape(account['window']['start'])}；"
                f"窗口截止：{escape(account['window']['end'])}。</p>"
                '<p><a href="/accounts">查看模拟账户与原生记录</a></p>'
            )
        gate = escape(LABELS.get(forward["state"], forward["state"]))
        body += f"<p>当前账户门禁：{gate}</p>"
        body += details(forward, "身份、日历与输入检查")
    else:
        body += (
            "<p>尚未登记模拟账户；已提名候选仍需完成最新数据预检"
            "与当前版本研究复核。</p>"
        )
    body += '</section><section class="panel"><h2>目标与模拟持仓差多少？</h2>'
    execution = value["execution"]
    if not execution["available"]:
        body += "<p>暂无已核验模拟观察；持仓与差额不可用，不按零处理。</p>"
    else:
        body += f"<p>账本观察日：{escape(execution['as_of'])}</p>"
        if execution.get("position_comparison"):
            body += table(
                ["证券", "目标数量", "模拟持仓", "差额", "待成交净数量"],
                execution["position_comparison"],
            )
        else:
            body += "<p>未提供可核验的目标数量对照；查看原生委托、成交及拒绝原因。</p>"
        for name, label in (
            ("positions", "模拟持仓"),
            ("orders", "原生委托"),
            ("fills", "模拟成交"),
        ):
            if name in execution["tables"]:
                item = execution["tables"][name]
                body += (
                    f"<h3>{label}</h3>"
                    f"<p>显示最后100行以内，共{item['total_rows']}行。</p>"
                )
                body += table(item["columns"], item["rows"])
    body += '</section><section class="panel"><h2>成本偏在哪里？</h2>'
    body += "<p>本页为模拟费用与滑点假设；未接真实成交和独立报价。</p>"
    body += (
        "<p>收盘形成信号，后续交易日按开盘价与冻结成本假设模拟；"
        "不补造过去的开盘成交。</p>"
    )
    if execution["available"]:
        body += details(execution.get("cost_assumptions"), "冻结费用与滑点假设")
        body += table(
            ["币种", "账本费用类型", "累计模拟费用"], execution.get("cost_totals", [])
        )
        body += details(execution.get("metrics", {}), "原生研究与成本指标")
        window = execution.get("cost_window")
        forecast = window.get("forecast") if window else None
        if forecast:
            body += "<h3>最近目标的预估综合成本</h3>"
            body += (
                f"<p>同一观察窗口：{escape(window['start'])} 至 "
                f"{escape(window['end'])}；成交尚可能未完成。</p>"
            )
            body += table(
                ["原生预算", "目标换手", "估算口径"],
                [
                    [
                        forecast.get("estimated_cost_amount"),
                        forecast.get("turnover"),
                        "冻结的每单位换手成本率；含费用与价格影响预算",
                    ]
                ],
            )
            body += (
                "<p>以下费用和成交数量按最近目标窗口对齐。综合预算包含价格影响，"
                "账本费用仅为原生收费，两者不直接相减；滑点保持冻结模型假设。</p>"
            )
            body += table(["币种", "原生费用类型", "本窗口账本费用"], window["fees"])
            body += table(
                ["证券", "方向", "委托数量", "模拟成交数量", "数量差额"],
                window["quantities"],
            )
        if "costs" in execution["tables"]:
            item = execution["tables"]["costs"]
            body += table(item["columns"], item["rows"])
    else:
        body += "<p>成本数据不可用。</p>"
    body += '</section><section class="panel"><h2>异常怎么处理？</h2>'
    explanation = issue_text(latest.get("error", "检查下方各阶段证据与原生风险事件。"))
    body += f"<p>{escape(explanation)}</p>"
    issues = [latest["error"]] if latest.get("error") else []
    for stage in stages.values():
        issues.extend(stage.get("evidence", {}).get("issues", []))
        if stage.get("reason"):
            issues.append(stage["reason"])
    forward_reason = (value.get("forward") or {}).get("reason")
    if forward_reason and (value.get("forward") or {}).get("state") in {
        "verification_failed",
        "data_missing",
        "interrupted",
        "retryable_failure",
        "historical_backfill_blocked",
        "execution_failed",
    }:
        issues.append(forward_reason)
    if issues:
        from quant_studio.etf_status_context import explain_issue

        for issue in dict.fromkeys(str(item) for item in issues):
            guidance = explain_issue(issue)
            body += (
                f"<article><h3>{escape(guidance['cause'])}</h3>"
                f"<p>影响：{escape(guidance['impact'])}</p>"
                f"<p>下一步：{escape(guidance['action'])}</p>"
            )
            body += (
                f'<a href="{guidance["href"]}">{guidance["label"]}</a>'
                + details(issue, "错误原文")
                + "</article>"
            )
    body += (
        "<p>数据问题进入质量中心；配置问题进入运行环境；"
        "账户中断先核验，再显式重试。历史修订或冻结身份变化需停止观察。</p>"
    )
    body += (
        '<div id="daily-evidence">'
        + details(latest, "完整阶段回执")
        + "</div>"
        + details(execution.get("diagnostics", {}), "原生风险与未成交原因")
    )
    return body + "</section>"


def today_post(handler, data):
    command = field(data, "command")
    if command == "recovery-preview":
        from quant_studio.etf_daily import preview_recovery

        operation = field(data, "operation")
        if data:
            raise QuantStudioError("未知恢复预览字段")
        identifier = preview_recovery(handler.runs_root, operation)
        handler._redirect(f"/today?recovery={identifier}")
        return
    if command == "recovery-confirm":
        from quant_studio.etf_daily import read_preview
        from quant_studio.projects import digest

        identifier = field(data, "preview")
        if field(data, "confirmed", "") != "yes" or data:
            raise QuantStudioError("请明确确认已经审阅恢复方案")
        payload = request(
            "recover-execute", read_preview(handler.runs_root, identifier)
        )
        job = handler._jobs().submit(
            "collect",
            "etf-weekly-research",
            {},
            profile=handler._request_profile,
            collection=payload,
            dispatch_key=digest(payload),
        )
        handler._redirect(f"/jobs/{job['job_id']}")
        return
    if command == "connect-project":
        from quant_studio.etf_project import connect

        operation = field(data, "operation")
        project_id = field(data, "project_id", "") or None
        if data:
            raise QuantStudioError("未知ETF连接字段")
        project = connect(handler.runs_root, operation, project_id=project_id)
        handler._redirect(f"/projects/{project['id']}#research-card")
        return
    if data:
        raise QuantStudioError("未知ETF运行字段")
    job = handler._jobs().submit(
        "collect",
        "etf-weekly-research",
        {},
        profile=handler._request_profile,
        collection=request(command),
    )
    handler._redirect(f"/jobs/{job['job_id']}")
