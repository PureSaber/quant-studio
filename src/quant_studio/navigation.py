"""Navigation and template evidence labels shared by desktop and mobile."""

from html import escape

GROUPS = (
    (
        "data",
        "数据",
        (
            ("data", "数据概览", "/data"),
            ("datasets", "数据集", "/datasets"),
            ("catalog", "数据目录", "/catalog"),
            ("data-center", "数据更新与质量", "/data-center"),
            ("intake", "导入与质量", "/intake"),
        ),
    ),
    (
        "research",
        "研究",
        (
            ("onboarding", "开始研究", "/onboarding"),
            ("projects", "研究项目", "/projects"),
            ("findings", "研究结论", "/projects/findings"),
            ("research", "研究方案", "/research"),
            ("strategy", "策略模板", "/strategy"),
            ("experiments", "实验笔记", "/experiments"),
            ("history", "研究历史", "/history"),
        ),
    ),
    (
        "runs",
        "运行与比较",
        (
            ("today", "今日运行", "/today"),
            ("backtest", "回测", "/backtest"),
            ("batches", "批量实验", "/batches"),
            ("durable", "后台因子研究", "/durable-research"),
            ("jobs", "任务", "/jobs"),
            ("results", "结果与比较", "/results"),
        ),
    ),
    (
        "accounts",
        "账户",
        (
            ("accounts", "账户概览", "/accounts"),
            ("fund-reconcile", "基金对账", "/fund-reconcile"),
        ),
    ),
    (
        "system",
        "系统",
        (
            ("setup", "首次配置", "/setup"),
            ("environment", "环境", "/environment"),
            ("platform-status", "版本与验收", "/platform-status"),
            ("operations", "服务状态", "/operations"),
            ("delivery", "环境与交付", "/delivery"),
        ),
    ),
)
UTILITY = (("overview", "总览", "/"), ("logout", "退出登录", "/logout"))


PRIMARY = (
    ("overview", "首页", "/"),
    ("projects", "研究项目", "/projects"),
    ("data", "数据", "/data"),
    ("experiments", "运行记录", "/experiments"),
    ("accounts", "模拟跟踪", "/accounts"),
)
SECONDARY = (("strategy", "模板库", "/strategy"), ("setup", "设置", "/setup"))


def navigation(active):
    aliases = {
        "research": "projects",
        "findings": "projects",
        "onboarding": "projects",
        "catalog": "data",
        "datasets": "data",
        "intake": "data",
        "data-center": "data",
        "jobs": "experiments",
        "results": "experiments",
        "backtest": "experiments",
        "batches": "experiments",
        "durable": "experiments",
        "history": "experiments",
        "today": "accounts",
        "fund-reconcile": "accounts",
        "environment": "setup",
        "platform-status": "setup",
        "operations": "setup",
        "delivery": "setup",
    }
    selected = aliases.get(active, active)

    def link(item):
        key, label, href = item
        current = ' aria-current="page"' if selected == key else ""
        mark = " on" if selected == key else ""
        return f'<a class="nav{mark}" href="{href}"{current}>{label}</a>'

    parts = ['<a class="nav nav-create" href="/projects/new">＋ 新建研究</a>']
    parts.extend(map(link, PRIMARY))
    parts.append('<div class="nav-secondary">')
    parts.extend(map(link, SECONDARY))
    parts.append(link(("logout", "退出登录", "/logout")) + "</div>")
    label = next(
        (label for key, label, _ in PRIMARY + SECONDARY if key == selected), "当前页面"
    )
    return "".join(parts), label


def related_navigation(active):
    """Keep specialist tools discoverable inside their destination, not the sidebar."""
    if active in {
        "experiments",
        "jobs",
        "results",
        "backtest",
        "batches",
        "durable",
        "history",
    }:
        group = (
            ("experiments", "运行记录", "/experiments"),
            ("batches", "批量实验", "/batches"),
            ("durable", "后台因子研究", "/durable-research"),
        )
    elif active in {"accounts", "today", "fund-reconcile"}:
        group = (
            ("accounts", "模拟跟踪", "/accounts"),
            ("today", "今日运行", "/today"),
            ("fund-reconcile", "基金对账", "/fund-reconcile"),
        )
    elif active in {"overview", "projects", "research", "findings", "strategy"}:
        return ""
    else:
        group = next(
            (
                children
                for _, _, children in GROUPS
                if any(key == active for key, _, _ in children)
            ),
            (),
        )
    return (
        '<nav class="tabs" aria-label="相关功能">'
        + "".join(
            f'<a href="{href}"'
            + (' aria-current="page"' if key == active else "")
            + f">{escape(label)}</a>"
            for key, label, href in group
        )
        + "</nav>"
    )


def template_evidence_label(template):
    """Describe declared defaults; template readiness does not certify input data."""
    allowed = template.metadata.get("result_evidence", {}).get("values", [])
    if (
        template.kind == "synthetic"
        or template.base_config.get("evidence_kind") == "synthetic"
        or allowed == ["synthetic"]
    ):
        return "默认数据：合成样例"
    return "数据性质：按实际输入核验"
