"""Data status, history retrieval and environment delivery pages."""

import json
import re
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from quant_studio import QuantStudioError
from quant_studio.data_maintenance import DataMaintenance
from quant_studio.datasets import DatasetStore, file_hash
from quant_studio.environment_delivery import (
    ACTIONS,
    DeliveryStore,
    delivery_config,
)
from quant_studio.projects import digest
from quant_studio.research_history import search_history
from quant_studio.workbench_web import csrf, field


def _json(value):
    return "<pre>" + escape(json.dumps(value, ensure_ascii=False, indent=2)) + "</pre>"


def data_center_body(root, token, query, jobs):
    from quant_studio.project_web import table

    store = DataMaintenance(root)
    target = query.get("asof", [""])[0]
    body = '<header class="page-head"><h1>数据更新与质量中心</h1></header>'
    body += f'''<section class="panel"><form method="get">
<label>本次研究要求的截止日期<input type="date" name="asof"
value="{escape(target, quote=True)}"></label><button class="btn">检查覆盖日期</button>
</form><p>以你要求的日期判断新鲜度；登记截止不等于所有标的均已完整覆盖。</p></section>'''
    for item in DatasetStore(root).list():
        end = item.get("end", "未声明")
        state = "未指定目标日期"
        if target:
            from datetime import date

            try:
                state = (
                    "截止已覆盖"
                    if date.fromisoformat(str(end)) >= date.fromisoformat(target)
                    else "需要更新"
                )
            except ValueError:
                state = "截止日期未声明"
        body += f"""<section class="panel"><h2>{escape(item["name"])}</h2>
<p>{escape(str(item.get("provider", "未声明")))} ·
{escape(str(item.get("start", "未声明")))}至{escape(str(end))} · {state}</p>
<p><a href="/catalog?dataset={item["id"]}">字段与样本</a> ·
<a href="/datasets?verify={item["id"]}">核验全部登记文件</a></p>"""
        report = store.report(item["id"])
        if report:
            body += (
                "<p>"
                + (
                    "检查仍对应当前文件"
                    if report["current"]
                    else "检查已失效：文件发生变化"
                )
                + "</p>"
            )
            body += table(
                ["检查时间", "行数", "实际截止", "缺失标的", "重复标的日期"],
                [
                    [
                        report["checked_at"],
                        report["rows"],
                        report["observed_end"],
                        ", ".join(report["missing_symbols"]) or "无",
                        report["duplicate_keys"],
                    ]
                ],
            )
            body += _json({"空值": report["missing"], "检查范围": report["scope"]})
        elif item.get("quality"):
            body += (
                "<details><summary>原生质量回执</summary>"
                + _json(item["quality"])
                + "</details>"
            )
        else:
            body += "<p>尚无已保存的覆盖检查。</p>"
        files = [
            n for n in item["hashes"] if Path(n).suffix.lower() in {".csv", ".tsv"}
        ]
        if files:
            body += f'''<details><summary>检查实际日期与标的覆盖</summary>
<form method="post" action="/data-center/audit">{csrf(token)}
<input type="hidden" name="dataset" value="{item["id"]}">
<label>文件<select name="file">'''
            body += "".join(
                f'<option value="{escape(n, quote=True)}">{escape(n)}</option>'
                for n in files
            )
            symbols = item.get("symbols", [])
            body += f'''</select></label><label>日期列
<input name="date" value="date" required></label>
<label>标的列<input name="symbol" value="symbol" required></label>
<label>预期标的，逗号分隔<input name="expected"
value="{escape(",".join(symbols), quote=True)}"></label>
<button class="btn">保存覆盖检查</button></form></details>'''
        if item["template_id"] == "hk-equity-daily":
            body += f'''<form method="post" action="/datasets/refresh">{csrf(token)}
<input type="hidden" name="id" value="{item["id"]}">
<label>港股更新至<input type="date" name="end" required></label>
<button class="btn">采集新版本</button></form>'''
        body += "</section>"
    body += f"""<section class="panel"><h2>ETF日常增量更新</h2>
<p>首次创建独立数据目录，以后调用QDK重叠窗口更新；旧快照保持可读。</p>
<form method="post" action="/data-center/etf-create">{csrf(token)}
<label>名称<input name="name" required maxlength="120"></label>
<label>六位ETF代码<input name="symbols" placeholder="510300,510500" required></label>
<label>开始日期<input type="date" name="start" required></label>
<button class="btn">登记维护来源</button></form>"""
    for source in store.sources():
        body += f'''<form method="post" action="/data-center/etf-update">{csrf(token)}
<input type="hidden" name="source" value="{source["id"]}">
<label>{escape(source["name"])}更新至<input type="date" name="end" required></label>
<button class="btn">提交更新任务</button></form>'''
    body += '</section><section class="panel"><h2>最近更新与导入任务</h2>'
    for job in [j for j in jobs if j["action"] in {"collect", "intake"}][:30]:
        body += f"""<p><a href="/jobs/{job["job_id"]}">{escape(job["created_at"])}</a>
· {escape(job["status"])} · {escape(job.get("message") or "")}</p>"""
    return body + "</section>"


def history_body(root, query):
    text = query.get("q", [""])[0]
    body = f'''<header class="page-head"><h1>研究历史检索</h1></header>
<section class="panel"><form method="get"><label>研究问题、参数或报错
<input name="q" value="{escape(text, quote=True)}" maxlength="4000"></label>
<button class="btn">检索</button></form>
<p>按中英文关键词匹配项目版本、笔记、实验参数、指标和日志尾部。最多显示50条。</p>
<a href="/projects/notebook-compare">选择历史实验比较差异</a></section>'''
    for row in search_history(root, text, limit=50):
        body += f'''<section class="panel"><h2>
<a href="{escape(row["link"], quote=True)}"
>{escape(row["title"])}</a></h2><p>{escape(row["kind"])} · {escape(row["status"])}</p>
<pre>{escape(row["text"][:8000])}</pre></section>'''
    return body


def delivery_body(root, token):
    from quant_studio.environment_delivery import operational_snapshot

    body = '<header class="page-head"><h1>环境与交付</h1></header>'
    body += (
        '<section class="panel"><h2>正在运行的Studio</h2>'
        + _json(operational_snapshot())
        + "</section>"
    )
    try:
        config, _ = delivery_config()
    except QuantStudioError as exc:
        return body + '<section class="panel"><p>' + escape(str(exc)) + "</p></section>"
    current = Path(config["state_dir"]) / "current.json"
    body += '<section class="panel"><h2>已登记发布与迁移目标</h2>'
    body += _json(
        json.loads(current.read_text("utf-8"))
        if current.exists()
        else {"status": "尚无发布指针"}
    )
    body += _json(
        {"候选": config.get("candidates", {}), "迁移包": config.get("transfers", {})}
    )
    if config.get("local_control"):
        body += (
            "<h3>Studio关闭时的本机控制</h3><p>在独立PowerShell窗口运行下列固定入口，"
            "先查看预览，确认摘要后加 --execute --expected-plan-sha256 摘要。"
            "支持 start / stop / restart / activate / rollback；"
            "切换时加 --target 已配置名称。"
            "切换前须空闲；失败回退只恢复软件运行环境，保留较新的账户与账本。</p>"
        )
        body += _json(
            {
                "控制入口": [
                    config["python"],
                    "-I",
                    "-m",
                    "quant_workspace.local_control",
                    "--config",
                    config["local_control"],
                    "status",
                ]
            }
        )
    if config.get("backups"):
        body += (
            "<p>完整备份在本机空闲或Studio关闭后执行 preview → backup；"
            "本页可校验归档及执行隔离恢复。冻结绝对路径保留，"
            "恢复验证不会激活生产账户。</p>"
        )
        for name, row in config["backups"].items():
            command = "& " + " ".join(
                "'" + arg.replace("'", "''") + "'"
                for arg in [
                    config["python"],
                    "-I",
                    "-m",
                    "quant_workspace.operational_backup",
                    "--config",
                    row["config"],
                ]
            )
            script = (
                f"$backupPlan = {command} preview | ConvertFrom-Json\n"
                "$backupPlan | ConvertTo-Json -Depth 20\n"
                "# 审阅预览后执行：\n"
                f"{command} backup --expected-plan-sha256 $backupPlan.plan_sha256"
            )
            body += f"<h4>{escape(name)}：独立备份入口</h4><pre>{escape(script)}</pre>"
    body += f"""<form method="post" action="/delivery/prepare">{csrf(token)}
<label>操作<select name="action">"""
    body += "".join(
        f'<option value="{a}">{label}</option>' for a, label in ACTIONS.items()
    )
    body += (
        '</select></label><label>已配置候选或迁移包<select name="name">'
        '<option value="">环境检查</option>'
    )
    body += "".join(
        f'<option value="{escape(n, quote=True)}">{escape(n)}</option>'
        for n in {
            *config.get("candidates", {}),
            *config.get("transfers", {}),
            *config.get("backups", {}),
        }
    )
    body += '</select></label><button class="btn">预览操作</button></form></section>'
    store = DeliveryStore(root)
    for record in store.list()[:30]:
        body += (
            '<section class="panel"><h2>'
            + escape(ACTIONS[record["request"]["action"]])
            + "</h2>"
        )
        body += (
            f"<p>{escape(record['status'])} · {escape(record.get('message', ''))}</p>"
        )
        body += _json(record["request"])
        if record["status"] == "prepared":
            body += f'''<form method="post" action="/delivery/submit">{csrf(token)}
<input type="hidden" name="id" value="{record["id"]}">
<input type="hidden" name="revision" value="{record["request_sha256"]}">
<button class="btn primary">执行已预览操作</button></form>'''
        if record.get("job_id"):
            body += f'<a href="/jobs/{record["job_id"]}">任务日志与取消</a>'
        if record.get("result_sha256"):
            result = store.directory(record["id"]) / "result.json"
            if file_hash(result) != record["result_sha256"]:
                raise QuantStudioError("交付结果完整性失败")
            body += _json(json.loads(result.read_text("utf-8")))
        body += "</section>"
    return body


class MaintenanceHandler:
    def _maintenance_get(self, path):
        from quant_studio.server import _layout

        query = parse_qs(urlparse(self.path).query)
        if path == "/data-center":
            body = data_center_body(
                self.runs_root, self.csrf_token, query, self._jobs().list()
            )
        elif path == "/history":
            body = history_body(self.runs_root, query)
        else:
            body = delivery_body(self.runs_root, self.csrf_token)
        self._send_html(_layout("研究工作台", body, path.removeprefix("/")))

    def _maintenance_post(self, path, data):
        if path == "/data-center/audit":
            values = [
                field(data, n, "")
                for n in ("dataset", "file", "date", "symbol", "expected")
            ]
            if data:
                raise QuantStudioError("未知检查字段")
            DataMaintenance(self.runs_root).audit(
                *values[:4], [s for s in re.split(r"[,，\s]+", values[4]) if s]
            )
        elif path == "/data-center/etf-create":
            name, symbols, start = [
                field(data, n) for n in ("name", "symbols", "start")
            ]
            if data:
                raise QuantStudioError("未知数据来源字段")
            DataMaintenance(self.runs_root).create_etf(
                name, re.split(r"[,，\s]+", symbols.strip()), start
            )
        elif path == "/data-center/etf-update":
            source, end = field(data, "source"), field(data, "end")
            if data:
                raise QuantStudioError("未知更新字段")
            request = DataMaintenance(self.runs_root).request(source, end)
            job = self._jobs().submit(
                "collect",
                request["template_id"],
                {},
                profile=self._request_profile,
                collection=request,
            )
            self._redirect(f"/jobs/{job['job_id']}")
            return
        elif path == "/delivery/prepare":
            action, name = field(data, "action"), field(data, "name", "")
            if data:
                raise QuantStudioError("未知交付字段")
            DeliveryStore(self.runs_root).prepare(action, name)
        elif path == "/delivery/submit":
            identifier, revision = field(data, "id"), field(data, "revision")
            record = DeliveryStore(self.runs_root).get(identifier)
            if data or record["request_sha256"] != revision:
                raise QuantStudioError("交付请求与预览不一致")
            job = self._jobs().submit(
                "delivery",
                "workspace-delivery",
                {},
                profile=None,
                delivery={"id": identifier},
                dispatch_key=digest(["delivery", identifier]),
            )
            self._redirect(f"/jobs/{job['job_id']}")
            return
        else:
            raise QuantStudioError("未知维护操作")
        self._redirect("/delivery" if path.startswith("/delivery") else "/data-center")
