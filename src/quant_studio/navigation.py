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


def navigation(active):
    def link(item):
        key, label, href = item
        mark = " on" if key == active else ""
        current = ' aria-current="page"' if key == active else ""
        return f'<a class="nav{mark}" href="{href}"{current}>{label}</a>'

    parts = [link(UTILITY[0])]
    items = list(UTILITY)
    for key, title, children in GROUPS:
        items.extend(children)
        opened = " open" if any(item[0] == active for item in children) else ""
        parts.append(
            f'<details class="nav-group" data-section="{key}"{opened}>'
            f"<summary>{escape(title)}</summary>"
            + "".join(map(link, children))
            + "</details>"
        )
    parts.append(link(UTILITY[1]))
    label = next((label for key, label, _ in items if key == active), "当前页面")
    return "".join(parts), label


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
