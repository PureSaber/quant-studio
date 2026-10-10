"""Explicit derivative collection forms and immutable queued requests."""

import hashlib
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.runtime import template_python
from quant_studio.templates import load_template

TEMPLATES = {"options-research": "option", "global-futures-research": "future"}


def collection_request(
    name,
    template,
    provider,
    contracts_path="",
    start="",
    end="",
    rights_note="",
    dataset="",
    max_cost_usd="",
):
    if template not in TEMPLATES or provider not in {"demo", "dataway", "databento"}:
        raise QuantStudioError("请选择期货／期权模块及支持的数据源")
    if not name.strip() or len(name) > 120:
        raise QuantStudioError("数据集名称须为 1–120 字")
    result = {
        "name": name.strip(),
        "template_id": template,
        "provider": provider,
        "kind": TEMPLATES[template],
        "collection_type": "derivatives",
    }
    if provider == "demo":
        return result
    path = Path(contracts_path)
    if not path.is_absolute() or not path.is_file() or path.stat().st_size > 128000:
        raise QuantStudioError("请填写服务端 128 KB 以内的合约规则 JSON 文件绝对路径")
    raw = path.read_bytes()
    try:
        contracts = json.loads(raw.decode("utf-8-sig"))
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (ValueError, UnicodeError) as exc:
        raise QuantStudioError("合约 JSON 或日期格式无效") from exc
    if (
        not isinstance(contracts, list)
        or not 1 <= len(contracts) <= 40
        or any(
            not isinstance(c, dict) or c.get("kind") != result["kind"]
            for c in contracts
        )
    ):
        raise QuantStudioError(
            "规则文件须包含 1–40 个同类合约；完整规则由原生数据层再检查"
        )
    if not 0 <= (last - first).days <= 31 or last > date.today():
        raise QuantStudioError("每次采集限连续 1–32 天，且不晚于今天")
    if not rights_note.strip() or len(rights_note) > 1000:
        raise QuantStudioError("请填写数据授权来源与使用范围（1–1000 字）")
    result.update(
        contracts=contracts,
        contracts_sha256=hashlib.sha256(raw).hexdigest(),
        start=start,
        end=end,
        rights_note=rights_note.strip(),
    )
    if provider == "databento":
        try:
            cost = Decimal(max_cost_usd)
            if not cost.is_finite() or not 0 <= cost <= 100:
                raise ValueError("invalid cost")
        except (InvalidOperation, ValueError) as exc:
            raise QuantStudioError(
                "Databento 必须填写 0–100 美元的本次费用估算上限"
            ) from exc
        if not re.fullmatch(r"[A-Z0-9]+\.[A-Z0-9]+", dataset):
            raise QuantStudioError("请填写 Databento 数据集名称，如 GLBX.MDP3")
        result.update(dataset=dataset, max_cost_usd=str(cost))
    return result


def command(request, directory, snapshot):
    template = load_template(request["template_id"])
    runtime = template_python(template)
    if not runtime:
        raise QuantStudioError("请先配置该衍生品研究模块的独立 Python 环境")
    # Revalidate the persisted request through the same function. No arbitrary
    # executable, module or provider URL can be supplied by a queued job.
    if request["provider"] == "demo":
        verified = collection_request(request["name"], request["template_id"], "demo")
        if verified != request:
            raise QuantStudioError("演示采集请求含有未知字段")
        return [
            runtime,
            "-X",
            "utf8",
            "-m",
            "quant_data_kit.derivatives.cli",
            "demo",
            "--kind",
            request["kind"],
            "--output",
            str(snapshot),
        ]
    rules = directory / "contracts.json"
    rules.write_text(
        json.dumps(request["contracts"], ensure_ascii=False), encoding="utf-8"
    )
    checked = collection_request(
        request["name"],
        request["template_id"],
        request["provider"],
        str(rules.resolve()),
        request["start"],
        request["end"],
        request["rights_note"],
        request.get("dataset", ""),
        request.get("max_cost_usd", ""),
    )
    if {k: v for k, v in checked.items() if k != "contracts_sha256"} != {
        k: v for k, v in request.items() if k != "contracts_sha256"
    }:
        raise QuantStudioError("采集请求字段无效")
    argv = [
        runtime,
        "-X",
        "utf8",
        "-m",
        "quant_data_kit.derivatives.cli",
        "fetch",
        "--provider",
        request["provider"],
        "--contracts",
        str(rules),
        "--output",
        str(snapshot),
        "--start",
        request["start"],
        "--end",
        request["end"],
        "--rights-note",
        request["rights_note"],
    ]
    if request["provider"] == "databento":
        argv.extend(
            ["--dataset", request["dataset"], "--max-cost-usd", request["max_cost_usd"]]
        )
    return argv


def form(csrf_html):
    return f"""<section class="panel"><h2>期货与期权数据</h2>
<p>演示数据用于验证功能。真实采集需要已有数据权限和合约规则文件；接口未提供的结算价、期权标的价格需补齐后才能回放。</p>
<form method="post" action="/datasets/derivatives">{csrf_html}<div class="recipe-grid">
<label>名称<input name="name" value="我的衍生品数据" maxlength="120" required></label>
<label>研究模块<select name="template">
<option value="options-research">期权链与组合</option>
<option value="global-futures-research">海外期货与换月</option></select></label>
<label>数据源<select name="provider">
<option value="demo">生成演示数据（合成）</option>
<option value="dataway">汇升 Dataway（已有权限）</option>
<option value="databento">Databento（需已有账号）</option></select></label>
</div><details><summary>真实数据采集设置（演示数据无需填写）</summary>
<div class="recipe-grid">
<label>服务端合约规则 JSON 路径<input name="contracts_path"
placeholder="选择真实数据源时必填"></label>
<label>开始日期<input name="start" type="date"></label>
<label>结束日期<input name="end" type="date"></label>
<label>授权来源与使用范围<input name="rights_note" maxlength="1000"
placeholder="真实数据必填，不要填写密钥"></label>
<label>Databento 数据集<input name="dataset" placeholder="GLBX.MDP3"></label>
<label>本次费用估算上限（美元）<input name="max_cost_usd" type="number"
min="0" max="100" step="0.01"></label>
</div><p class="note">API 密钥由服务端环境提供。Databento 会先检查费用估算，再下载；
此数值不是供应商保证的硬性扣费上限。选择付费源并提交即按此估算上限执行一次请求，不自动重试。</p></details>
<button class="btn">创建新数据集</button></form></section>"""
