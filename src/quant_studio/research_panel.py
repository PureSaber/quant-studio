"""Verified timing summary rendered without importing the research runtime."""

from __future__ import annotations

import hashlib
import json
from html import escape

from quant_studio import QuantStudioError
from quant_studio.desk import html_table
from quant_studio.nav import parse_nav_csv
from quant_studio.runner import safe_run_file


def load_timing_view(directory, result):
    expected = result.get("research_view_sha256")
    if not expected:
        if result["status"] == "succeeded":
            raise QuantStudioError("择时结果缺少已核验的研究证据")
        return None
    path = safe_run_file(directory, "strategy-output/studio-view/view.json")
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise QuantStudioError("择时展示证据已改变，请保留记录并重新运行")
    view = json.loads(path.read_text(encoding="utf-8"))
    if view["schema_version"] != "quant-studio.timing-view/v1":
        raise QuantStudioError("未知择时展示契约")
    for section, prefix in (
        ("source_files", "strategy-output/"),
        ("view_files", "strategy-output/studio-view/"),
    ):
        for name, digest in view[section].items():
            source = safe_run_file(directory, prefix + name)
            if not source.is_file():
                raise QuantStudioError(f"择时原生证据或展示文件缺失：{name}")
            if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                raise QuantStudioError(f"择时原生证据或展示文件已改变：{name}")
    # The main curve/table copies must also still match the verified projection.
    for name in (
        "positions.csv",
        "orders.csv",
        "costs.csv",
        "nav.csv",
        "benchmark_nav.csv",
    ):
        copy = safe_run_file(directory, name)
        if result["status"] == "succeeded" and not copy.is_file():
            raise QuantStudioError(f"择时展示文件缺失：{name}")
        if not copy.exists():
            continue
        if name not in view["view_files"]:
            raise QuantStudioError(f"择时结果包含未核验曲线或表格：{name}")
        if name in {"nav.csv", "benchmark_nav.csv"}:
            if parse_nav_csv(copy) != parse_nav_csv(
                directory / "strategy-output/studio-view" / name
            ):
                raise QuantStudioError(f"择时曲线已改变：{name}")
        elif hashlib.sha256(copy.read_bytes()).hexdigest() != view["view_files"].get(
            name
        ):
            raise QuantStudioError(f"择时结果表已改变：{name}")
    return view


def timing_panel(directory, view):
    if view is None:
        return ""
    decision = view["publication"]
    label = {
        "use": "已发布研究仓位系数",
        "hold_previous": "保留旧仓位，未发布新系数",
        "blocked": "仓位发布被阻断",
    }[decision["action"]]
    scale = decision["position_scale"]
    scale_text = "未发布" if scale is None else f"{scale:.2%}"
    reason = decision["signals"].get("block_reason") or decision["signals"].get(
        "macro_policy", ""
    )
    reason_html = f"<p>原生原因：{escape(str(reason))}</p>" if reason else ""

    def pct(value):
        return "不可用" if value is None else f"{value * 100:+.4f}%"

    table = html_table(
        directory / "strategy-output/studio-view/folds.csv",
        "滚动测试折（原生计分）",
        column_labels={
            "fold": "测试折",
            "train_end": "训练结束",
            "test_start": "测试开始",
            "test_end": "测试结束",
            "n_decisions": "计分决策数",
            "net_return": "净收益比例",
            "benchmark_return": "基准收益比例",
            "matched_net_return": "匹配敞口净收益比例",
            "excess_return": "超额比例",
            "matched_excess_return": "匹配敞口超额比例",
            "avg_turnover": "平均换手比例",
            "max_drawdown": "带符号回撤比例",
        },
    )
    return (
        '<section class="panel"><h2>仓位发布状态</h2>'
        f"<p><strong>{escape(label)}</strong> · "
        f"{escape(decision['as_of'])} · {scale_text}</p>"
        f'{reason_html}<p class="note">'
        "系数仅供显式配置的下游研究读取；本次运行未更新已有账户。</p></section>"
        '<section class="panel"><h2>滚动样本外结果</h2>'
        f"<p>有效计分折：{view['scored_folds']}/{view['fold_count']} · "
        f"因果审计：{'通过' if view['leakage_passed'] else '未通过'}</p>"
        f"<p>平均测试折超额：{pct(view['mean_excess_return'])}；匹配敞口平均超额：{pct(view['mean_matched_excess_return'])}</p>"
        '<p class="note">平均折收益不等于复合收益；审计与计分通过不证明正超额。'
        "表内收益和回撤保留原生比例，预览前12行。</p></section>"
        + table
        + f'<p><a href="/runs/{escape(directory.name)}/files/'
        'strategy-output/studio-view/folds.csv" download>下载完整滚动测试折CSV</a></p>'
        + '<details class="panel"><summary>本次实际研究配置</summary><pre>'
        + escape(json.dumps(view["effective_config"], ensure_ascii=False, indent=2))
        + "</pre></details>"
    )
