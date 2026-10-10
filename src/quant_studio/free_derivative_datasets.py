"""Fixed public-source requests; queued jobs cannot supply executable URLs."""

import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from quant_studio import QuantStudioError

TEMPLATES = {"options-free-sources": "option", "global-futures-free-sources": "future"}
FIELDS = (
    "name",
    "template",
    "provider",
    "selection",
    "start",
    "end",
    "rights_note",
    "source_mode",
    "tick",
    "initial_margin",
    "maintenance_margin",
)


def collection_request(
    name,
    template,
    provider,
    selection,
    start,
    end,
    rights_note,
    source_mode="daily",
    tick="0.05",
    initial_margin="",
    maintenance_margin="",
):
    if template not in TEMPLATES or provider not in {"cboe-vix", "nse-fo", "deribit"}:
        raise QuantStudioError("请选择免费期货／期权模块和支持的数据源")
    if not 1 <= len(name.strip()) <= 120 or not 1 <= len(rights_note.strip()) <= 1000:
        raise QuantStudioError("请填写名称和个人研究使用范围，不要填写密钥")
    selected = [v.strip() for v in selection.split(",")]
    if (
        not 1 <= len(selected) <= 20
        or len(set(selected)) != len(selected)
        or any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", v) for v in selected)
    ):
        raise QuantStudioError("请填写 1–20 个不同合约，以英文逗号分隔")
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if not 0 <= (last - first).days <= 31 or last > datetime.now(UTC).date():
            raise ValueError("date window")
        value = Decimal(tick)
        if not value.is_finite() or not 0 < value <= 10000:
            raise ValueError("tick")
        if bool(initial_margin) != bool(maintenance_margin):
            raise ValueError("margin pair")
        if initial_margin:
            a, b = Decimal(initial_margin), Decimal(maintenance_margin)
            if not a.is_finite() or not b.is_finite() or not 0 <= b <= a or a <= 0:
                raise ValueError("margin")
        if provider == "cboe-vix":
            if (
                TEMPLATES[template] != "future"
                or source_mode != "daily"
                or any(last >= date.fromisoformat(v) for v in selected)
            ):
                raise ValueError("Cboe expiry")
        elif provider == "nse-fo":
            if source_mode != "daily":
                raise ValueError("NSE daily only")
        else:
            if (
                TEMPLATES[template] != "option"
                or source_mode not in {"trades", "snapshot"}
                or initial_margin
                or len(selected) > 10
            ):
                raise ValueError("Deribit analytical options")
            if (
                any(
                    not re.fullmatch(
                        r"(?:BTC|ETH)-\d{1,2}[A-Z]{3}\d{2}-\d+(?:d\d+)?-[CP]", v
                    )
                    for v in selected
                )
                or len({v.split("-")[0] for v in selected}) != 1
            ):
                raise ValueError("Deribit explicit symbols")
            if source_mode == "snapshot" and (
                first != last or last != datetime.now(UTC).date()
            ):
                raise ValueError("snapshot must be today UTC")
    except (ValueError, InvalidOperation) as exc:
        raise QuantStudioError(
            "采集参数无效：限 1–32 天；Cboe 填到期日；NSE 用 daily；"
            "Deribit 用 trades／snapshot 且不填保证金，snapshot 日期须为今天 UTC。"
            "保证金须成对且初始≥维持≥0。"
        ) from exc
    return {
        "collection_type": "derivatives",
        "free_source": True,
        "name": name.strip(),
        "template_id": template,
        "kind": TEMPLATES[template],
        "provider": provider,
        "selection": ",".join(selected),
        "start": start,
        "end": end,
        "rights_note": rights_note.strip(),
        "source_mode": source_mode,
        "tick": str(value),
        "initial_margin": str(Decimal(initial_margin)) if initial_margin else "",
        "maintenance_margin": str(Decimal(maintenance_margin))
        if maintenance_margin
        else "",
    }


def command(request, runtime, snapshot):
    checked = collection_request(
        *(
            request["template_id"] if k == "template" else request.get(k, "")
            for k in FIELDS
        )
    )
    if checked != request:
        raise QuantStudioError("免费数据请求含有未知或被修改的字段")
    argv = [
        runtime,
        "-X",
        "utf8",
        "-m",
        "quant_data_kit.derivatives.cli",
        "free-fetch",
        "--output",
        str(snapshot),
    ]
    for key in (
        "provider",
        "kind",
        "selection",
        "start",
        "end",
        "rights_note",
        "source_mode",
        "tick",
        "initial_margin",
        "maintenance_margin",
    ):
        if request[key] != "":
            argv.extend(["--" + key.replace("_", "-"), request[key]])
    return argv


def form(csrf_html):
    return f"""<section class="panel">
<h2>免费海外期货与期权</h2>
<p>从官方公开文件或 API 创建一个固定数据版本。无需 API 密钥，
每次最多 32 天，不自动重试。
Cboe / NSE 默认只分析；明确填写保证金假设后可作模型回放。Deribit 只作美元折算分析。</p>
<form method="post" action="/datasets/free-derivatives">{csrf_html}
<div class="recipe-grid">
<label>名称<input name="name" value="我的免费海外数据"
required maxlength="120">
</label>
<label>研究模块<select name="template">
<option value="global-futures-free-sources">免费源 · 海外期货</option>
<option value="options-free-sources">免费源 · 期权</option>
</select>
</label>
<label>数据源<select name="provider">
<option value="cboe-vix">Cboe · VIX 期货</option>
<option value="nse-fo">NSE · 印度指数期货／期权</option>
<option value="deribit">Deribit · BTC/ETH 期权</option>
</select>
</label>
<label>合约选择（英文逗号分隔）<input name="selection"
placeholder="Cboe: 2025-02-19,2025-03-18"
required>
</label>
<label>开始日期（UTC 日期）<input name="start"
type="date"
required>
</label>
<label>结束日期（UTC 日期）<input name="end"
type="date"
required>
</label>
<label>数据形式<select name="source_mode">
<option value="daily">daily · Cboe/NSE 日线</option>
<option value="trades">trades · Deribit 历史成交聚合</option>
<option value="snapshot">snapshot · Deribit 当前参考估值</option>
</select>
</label>
<label>个人研究使用范围<input name="rights_note" value="个人本地研究，不再分发原始数据"
required maxlength="1000">
</label>
</div>
<p class="note">NSE 例：NIFTY25FEBFUT 或 NIFTY25JAN23500PE。
Deribit 历史例：BTC-31JAN25-100000-C。
Cboe 请填写官方实际到期日（节假日可能调整）。
历史示例日期可用 2025-01-08 至 2025-01-10；
当前快照须选择仍在交易的合约并填写今天 UTC 日期。</p>
<details>
<summary>价格步长与保证金研究假设</summary>
<div class="recipe-grid">
<label>研究价格步长<input name="tick" value="0.05"
type="number" min="0.000001" max="10000" step="any"
required>
</label>
<label>每手初始保证金（合约币种，可留空）<input name="initial_margin"
type="number" min="0" step="any">
</label>
<label>每手维持保证金（可留空）<input name="maintenance_margin"
type="number" min="0" step="any">
</label>
</div>
<p>这些是假设值，不能替代历史交易所规则。
Deribit 币本位结算未进入账户模型，须留空。</p>
</details>
<button class="btn">采集并保存新数据版本</button>
</form>
</section>"""
