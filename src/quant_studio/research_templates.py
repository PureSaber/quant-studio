"""Question-led Notebook templates for admitted immutable research data."""

from __future__ import annotations

from quant_studio import QuantStudioError

TEMPLATE_IDS = {
    "data-quality-exploration",
    "price-return-missingness",
    "historical-financial-availability",
}


def research_quality_prompts() -> dict[str, list[str]]:
    return {
        "automatic_checks": [
            "固定数据版本、文件集合与逐文件SHA-256在读取前后保持一致",
            "QDK读取器源码、独立解释器版本及pandas/numpy/pyarrow版本保持一致",
            "登记时的purpose、columns、symbols和日期范围与实际读取完全一致",
            "QDK准入结果、重复键、类型、缺失和可用时点规则没有阻断项",
        ],
        "human_judgment": [
            "特征是否在决策时点真实可得，业务语义是否仍可能包含未来信息",
            "样本、标的、日期窗口和缺失处理是否引入选择偏差或幸存者偏差",
            "尝试过多少假设、参数和子样本，是否需要多重检验或独立留出集",
            "统计差异是否有经济含义，结论能否推广到成本、容量和制度变化后",
        ],
    }


def _source(cell_type: str, identity: str, source: str) -> dict:
    cell = {"cell_type": cell_type, "metadata": {}, "source": source, "id": identity}
    if cell_type == "code":
        cell.update(execution_count=None, outputs=[])
    return cell


def _intake_datasets(context: dict) -> list[dict]:
    return [item for item in context["datasets"] if item.get("kind") == "intake"]


def _check_ready(item: dict) -> bool:
    intake = item.get("intake", {})
    check = intake.get("check", {})
    return (
        isinstance(check, dict)
        and check.get("allowed") is True
        and check.get("status") == "ready"
        and check.get("purpose") == intake.get("purpose")
        and check.get("version_id") == intake.get("version")
    )


def compatible_datasets(template: str, context: dict) -> list[dict]:
    if template not in TEMPLATE_IDS:
        raise QuantStudioError("未知Notebook研究模板")
    datasets = [item for item in _intake_datasets(context) if _check_ready(item)]
    if template == "data-quality-exploration":
        selected = datasets
    elif template == "price-return-missingness":
        selected = [
            item
            for item in datasets
            if item["intake"]["purpose"] == "daily_bars_research"
            and {"symbol", "date", "close"}
            <= set(item["intake"]["check"].get("scope", {}).get("columns", []))
        ]
    else:
        selected = [
            item
            for item in datasets
            if item["intake"]["purpose"] == "historical_financial_factor_backtest"
            and "available_at"
            in item["intake"]["check"].get("scope", {}).get("columns", [])
            and not {
                issue.get("rule")
                for issue in item["intake"]["check"].get("issues", [])
                if isinstance(issue, dict) and issue.get("severity") == "error"
            }
            & {
                "historical_available_at",
                "available_at_order",
                "point_in_time_availability",
            }
        ]
    if not selected:
        if template == "historical-financial-availability":
            raise QuantStudioError(
                "历史财务可用时点模板需要已按PIT用途准入、含available_at的固定版本"
            )
        if template == "price-return-missingness":
            raise QuantStudioError(
                "价格收益模板需要按日线用途准入且固定范围含symbol、date、close"
            )
        raise QuantStudioError("数据质量模板需要至少一个已通过准入的intake固定版本")
    return selected


def _common_cells(project: dict, template: str, context: dict) -> list[dict]:
    selected = compatible_datasets(template, context)
    selected_ids = [item["id"] for item in selected]
    prompts = research_quality_prompts()
    quality = (
        "## 研究质量边界\n\n**自动检查**\n- "
        + "\n- ".join(prompts["automatic_checks"])
        + "\n\n**需人工判断**\n- "
        + "\n- ".join(prompts["human_judgment"])
    )
    loading = (
        "import json\nfrom pathlib import Path\nimport pandas as pd\n"
        "from notebook_data_reader import read_intake_dataset\n\n"
        "context = json.loads(Path('inputs.json').read_text(encoding='utf-8'))\n"
        f"selected_ids = {selected_ids!r}\n"
        "selected = [d for d in context['datasets'] if d['id'] in selected_ids]\n"
        "reads = {d['id']: read_intake_dataset(\n"
        "    d, context['intake_runtime'], base=Path.cwd()) for d in selected}\n"
        "dataset = selected[0]\nresult = reads[dataset['id']]\n"
        "data = pd.DataFrame(result['rows'])\n"
        "{'dataset': dataset['name'], 'purpose': dataset['intake']['purpose'],\n"
        " 'scope': dataset['intake']['scope'], 'rows': len(data),\n"
        " 'qdk_check': result['check']['status']}"
    )
    return [
        _source(
            "markdown",
            "question",
            f"# {project['question']}\n\n固定项目版本：`{project['revision']}`",
        ),
        _source("markdown", "quality-boundary", quality),
        _source("code", "fixed-intake", loading),
    ]


def notebook_cells(template: str, project: dict, context: dict) -> list[dict]:
    cells = _common_cells(project, template, context)
    if template == "data-quality-exploration":
        cells.extend(
            [
                _source(
                    "markdown",
                    "quality-question",
                    "## 问题与假设\n\n问题：哪些字段、键或时间区间最可能改变结论？\n\n"
                    "假设：若数据适合当前范围，主键重复为0，核心字段缺失集中度可解释，"
                    "且QDK问题账本没有被范围筛选掩盖的阻断项。",
                ),
                _source(
                    "code",
                    "quality-checks",
                    "quality = {\n"
                    "    'shape': data.shape,\n"
                    "    'dtypes': data.dtypes.astype(str).to_dict(),\n"
                    "    'missing': data.isna().sum().sort_values(\n"
                    "        ascending=False).to_dict(),\n"
                    "    'duplicate_rows': int(data.duplicated().sum()),\n"
                    "    'qdk_issues': result['check']['issues'],\n"
                    "}\nquality",
                ),
                _source(
                    "markdown",
                    "quality-interpretation",
                    "## 解释\n\n先判断缺失是否集中在特定字段、标的或时期，"
                    "再决定是否删除、"
                    "保留缺失指示或缩小问题范围。重复和类型问题属于数据身份问题，"
                    "不应通过Notebook静默修补。把QDK自动检查与业务上可接受的缺失机制分开陈述。",
                ),
            ]
        )
    elif template == "price-return-missingness":
        cells.extend(
            [
                _source(
                    "markdown",
                    "return-question",
                    "## 问题与假设\n\n问题：收益分布和极端值是否由价格缺失、"
                    "重复日期或长时间间隔驱动？\n\n"
                    "假设：同一标的按日期排序后，close有效且键唯一；不跨缺失价格填充收益。",
                ),
                _source(
                    "code",
                    "return-checks",
                    "prices = data.copy()\n"
                    "prices['date'] = pd.to_datetime(prices['date'], errors='raise')\n"
                    "prices['close'] = pd.to_numeric(\n"
                    "    prices['close'], errors='coerce')\n"
                    "prices = prices.sort_values(['symbol', 'date'])\n"
                    "prices['return'] = prices.groupby('symbol')['close'].pct_change(\n"
                    "    fill_method=None)\n"
                    "checks = {\n"
                    "    'duplicate_symbol_date': int(\n"
                    "        prices.duplicated(['symbol', 'date']).sum()),\n"
                    "    'missing_close': int(prices['close'].isna().sum()),\n"
                    "    'missing_return': int(prices['return'].isna().sum()),\n"
                    "    'date_gap_days': prices.groupby('symbol')['date'].diff()\n"
                    "        .dt.days.describe().to_dict(),\n"
                    "    'return_summary': prices['return'].describe().to_dict(),\n"
                    "}\nchecks",
                ),
                _source(
                    "markdown",
                    "return-interpretation",
                    "## 解释\n\n首个收益为空是定义结果；"
                    "其他空值要与close缺失和日期间隔对照。"
                    "长间隔可能来自停牌、节假日或数据缺口，不能自动当成日收益。"
                    "极端收益应回到原始价格、复权口径和公司行动核验后再解释。",
                ),
            ]
        )
    else:
        cells.extend(
            [
                _source(
                    "markdown",
                    "availability-question",
                    "## 问题与假设\n\n问题：每条财务记录在回测决策时点"
                    "是否已经真实可得？\n\n"
                    "假设：available_at来自实际披露证据，不早于period_end；"
                    "任何因子连接都必须使用available_at不晚于决策时点的最近版本。",
                ),
                _source(
                    "code",
                    "availability-checks",
                    "history = data.copy()\n"
                    "history['available_at'] = pd.to_datetime(\n"
                    "    history['available_at'], utc=True, errors='raise')\n"
                    "availability = {'missing_available_at': int(\n"
                    "    history['available_at'].isna().sum())}\n"
                    "if 'period_end' in history:\n"
                    "    history['period_end'] = pd.to_datetime(\n"
                    "        history['period_end'], utc=True, errors='raise')\n"
                    "    premature = history['available_at'] < history['period_end']\n"
                    "    availability['available_before_period_end'] = int(\n"
                    "        premature.sum())\n"
                    "    availability['lag_days'] = (\n"
                    "        history['available_at'] - history['period_end']\n"
                    "    ).dt.days.describe().to_dict()\n"
                    "    assert not premature.any(), (\n"
                    "        '发现披露时间早于报告期末，停止PIT分析')\n"
                    "availability",
                ),
                _source(
                    "markdown",
                    "availability-interpretation",
                    "## 解释\n\n通过格式和先后顺序检查仍不证明披露时间真实。"
                    "必须人工核对公告来源、"
                    "更正/重述版本及交易日可用边界。构造因子时按available_at做as-of连接，"
                    "并把尝试过的因子、滞后和样本切分计入多重检验判断。",
                ),
            ]
        )
    return cells


__all__ = [
    "TEMPLATE_IDS",
    "compatible_datasets",
    "notebook_cells",
    "research_quality_prompts",
]
