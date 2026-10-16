"""Plain-language operational state with explicit, bounded recovery previews."""

from html import escape

from quant_studio.etf_daily import read_preview
from quant_studio.project_web import table
from quant_studio.workbench_web import csrf

LABELS = {
    "waiting": "等待运行",
    "running": "正在运行",
    "completed": "今日已完成",
    "retry_wait": "等待自动重试",
    "needs_attention": "需要处理",
    "market_closed": "今日无需运行",
    "paused": "自动运行已停用",
    "unknown": "状态尚未核实",
    "interrupted": "执行中断，需要核验",
}
REASONS = {
    "transport_temporary": (
        "获取资料时网络传输中断",
        "本次获取未完成，不能据此更新账户",
    ),
    "publication_delay": ("数据源尚未发布完整的当日行情", "本次账户更新尚未完成"),
    "certificate_invalid": (
        "数据来源的证书验证未通过",
        "已阻止继续使用该连接，不能跳过验证",
    ),
    "tls_unverified": ("加密连接发生尚未确认原因的故障", "已停止自动重试，需核查连接"),
    "identity_changed": ("当前配置或环境与冻结记录不一致", "已阻止账户更新"),
    "historical_fill_blocked": (
        "缺少过去开盘前已经登记的订单依据",
        "原账户不能通过补造历史成交恢复",
    ),
    "native_check_failed": (
        "原有数据或账户检查没有通过",
        "本次未确认成功，需要查看检查依据",
    ),
}
STAGES = {
    "trigger": "触发",
    "master_refresh": "基础资料",
    "collection": "获取数据",
    "preflight": "检查数据",
    "observe": "策略计算与账户更新",
    "verify": "核对账本",
}
STATES = {"running": "进行中", "completed": "已完成", "blocked": "未通过"}


def body(value, token, root, preview_id=None):
    from quant_studio.etf_daily_web import details, local_time

    ops, data = value["operations"], value["data"]
    latest = value.get("latest") or {}
    scheduler = ops["scheduler"]
    page = '<div class="etf-operations" style="overflow-wrap:anywhere">'
    page += (
        '<section class="panel"><h2>'
        + escape(LABELS.get(ops["state"], "状态尚未核实"))
        + "</h2>"
    )
    page += table(
        ["你关心的问题", "当前结果"],
        [
            [
                "今天完成了吗？",
                "全部步骤已核验完成"
                if ops["state"] == "completed"
                else "尚未确认今日全部完成",
            ],
            ["数据更新到了哪一天？", data["covered_through"]],
            [
                "账户最后成功是哪一天？",
                ops["last_account_session"] or "尚无成功观察记录",
            ],
            [
                "接下来什么时候运行？",
                local_time(ops["next_attempt_at"], "当前没有已核实的后续自动运行安排"),
            ],
            [
                "现在需要我做什么？",
                "查看下方原因与可用操作"
                if ops["manual_action_required"]
                else "暂不需要操作",
            ],
        ],
    )
    page += "<p>页面最近核验：" + escape(local_time(value["checked_at"])) + "</p>"
    page += '<a class="btn" href="/today">核验当前状态（只读）</a></section>'
    page += '<section class="panel"><h2>四项状态分别看</h2>'
    task_label = (
        "已核实并启用"
        if scheduler.get("verified") and scheduler.get("enabled")
        else "已停用"
        if scheduler.get("verified")
        else "尚未核实"
    )
    page += table(
        ["部分", "结果", "依据"],
        [
            ["页面服务", "本次请求可访问", "仅说明页面有响应，不代表账户已完成"],
            ["定时任务", task_label, scheduler.get("reason") or scheduler.get("name")],
            [
                "行情数据",
                "已覆盖应到日期" if data["fresh"] else "需要更新",
                "行情采集：" + local_time(data["captured_at"]),
            ],
            [
                "模拟账户",
                ops["last_account_session"] or "尚无成功观察",
                "以原生账本核验为准；无数据不等于零收益",
            ],
        ],
    )
    page += (
        "<p>北京时间执行窗口："
        + escape("、".join(value["schedule"]["slots"]))
        + "。电脑需开机且用户已登录。</p>"
    )
    page += (
        "<p>原账户续跑核查：" + escape(ops["continuation"]["reason"]) + "</p></section>"
    )
    if value["schedule"].get("missed_slots"):
        page += (
            "<p>今天已错过的窗口："
            + escape("、".join(value["schedule"]["missed_slots"]))
            + "。不补记为成功。</p>"
        )
    page += (
        '<section class="panel"><h2>本次进度</h2>'
        "<p>触发 → 获取数据 → 检查数据 → 策略计算与账户更新 → 核对账本</p>"
    )
    rows = [
        [
            STAGES.get(r["stage"], r["stage"]),
            STATES.get(r["state"], r["state"]),
            local_time(r["recorded_at"]),
        ]
        for r in latest.get("stages", [])
    ]
    page += (
        table(["步骤", "结果", "时间"], rows)
        if rows
        else "<p>今日尚无新运维入口的运行记录。</p>"
    )
    page += '</section><section class="panel"><h2>发生了什么，接下来怎么办？</h2>'
    if latest.get("reason_code"):
        cause, impact = REASONS.get(
            latest["reason_code"], REASONS["native_check_failed"]
        )
        page += (
            "<p>发生了什么："
            + escape(cause)
            + "</p><p>影响："
            + escape(impact)
            + "</p>"
        )
    elif ops["state"] == "paused":
        page += "<p>自动写入尚未启用或已经停用。页面与只读核验仍可使用。</p>"
    elif ops["state"] == "interrupted":
        page += "<p>上次执行没有完整结束记录；需要先核对账户实际写入情况。</p>"
    else:
        page += "<p>请以今日进度和账户最后成功日期判断结果。</p>"
    page += (
        "<p>系统下一步："
        + (
            (
                "在 "
                + escape(local_time(ops["next_attempt_at"]))
                + " 再检查并执行允许的步骤。"
            )
            if ops["next_attempt_at"]
            else "当前没有已核实的自动补试安排。"
        )
        + "</p>"
    )
    page += (
        "<p>是否需要你处理："
        + (
            "需要查看原因；可先只读核验，再查看恢复预览。"
            if ops["manual_action_required"]
            else "目前不需要。"
        )
        + "</p>"
    )
    if latest and ops["state"] not in {"completed", "running"}:
        page += f'''<form method="post" action="/today/run">{csrf(token)}
<input type="hidden" name="command" value="recovery-preview">
<input type="hidden" name="operation" value="{escape(latest["id"], quote=True)}">
<button class="btn">查看重试或恢复方案</button></form>'''
    if preview_id and root is not None:
        preview = read_preview(root, preview_id)
        page += "<h3>恢复预览</h3><p>" + escape(preview["reason"]) + "</p>"
        page += (
            "<p>预览有效期 15 分钟；"
            "确认时重新核验账户、配置、数据、环境和运行记录。</p>"
        )
        if preview["action"] != "none":
            page += f'''<form method="post" action="/today/run">{csrf(token)}
<input type="hidden" name="command" value="recovery-confirm">
<input type="hidden" name="preview" value="{escape(preview_id, quote=True)}">
<label><input type="checkbox" name="confirmed" value="yes" required>
我已查看方案，确认执行允许的步骤</label>
<button class="btn">确认执行</button></form>'''
        page += details(preview, "查看预览依据")
    page += details(latest, "本次运行的详细记录") + "</section>"
    page += '<section class="panel"><h2>真实运行验收</h2>'
    acceptance = ops["acceptance"]
    page += (
        f"<p>最长连续自然成功：{acceptance['longest_consecutive']} / 6 个交易日。</p>"
    )
    page += (
        "<p>手动操作、隔离测试和历史回放不计入。"
        "这里只验证运行可靠性，不证明策略有效。</p>"
    )
    page += details(acceptance, "验收区间及缺口") + "</section>"
    execution = value["execution"]
    page += (
        '<section class="panel"><h2>账户与历史证据</h2>'
        "<p>原账户、资金、参数和观察窗口保持冻结。过去缺口保留，不补造过去成交。</p>"
    )
    if execution["available"]:
        page += "<p>以下是模拟账本，并非券商成交。</p>"
        for name, label in [
            ("positions", "模拟持仓"),
            ("orders", "原生委托"),
            ("fills", "模拟成交"),
            ("costs", "原生费用明细"),
        ]:
            if name in execution["tables"]:
                item = execution["tables"][name]
                page += "<h3>" + label + "</h3>" + table(item["columns"], item["rows"])
        page += details(execution.get("cost_assumptions"), "冻结费用与滑点假设")
        page += details(execution.get("metrics"), "原生研究与成本指标")
        page += details(
            execution.get("cost_window"), "最近目标窗口的预算、费用及成交数量"
        )
    else:
        page += "<p>尚无已核验的观察记录，持仓、收益和成本不可用。</p>"
    page += details(value.get("legacy_latest"), "迁移前的最近运行（历史记录）")
    page += details(value.get("history_context"), "历史检查与账户依据")
    page += details(ops["history"], "新运维入口全部记录")
    research = value.get("research_input") or {}
    if research.get("research_recipe"):
        choices = '<option value="">新建研究项目</option>'
        if root is not None:
            from quant_studio.projects import ProjectStore

            choices += "".join(
                f'<option value="{p["id"]}">{escape(p["name"])}</option>'
                for p in ProjectStore(root).list()
            )
        page += f'''<form method="post" action="/today/run">{csrf(token)}
<input type="hidden" name="command" value="connect-project">
<input type="hidden" name="operation" value="{escape(research["id"], quote=True)}">
<p>可连接的研究快照截止：{escape(research["data"]["covered_through"])}</p>
<label>连接到项目<select name="project_id">{choices}</select></label>
<button class="btn">将快照与冻结配方连接到研究项目</button></form>'''
    return (
        page
        + '<p><a href="/accounts">查看原生账户</a> · '
        + '<a href="/jobs">查看手动任务</a></p></section></div>'
    )
