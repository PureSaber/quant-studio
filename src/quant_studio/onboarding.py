"""State-aware first research path over persisted Studio records."""

from __future__ import annotations

import json
from contextlib import suppress
from html import escape
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.desk import list_runs
from quant_studio.intake_tools import IntakeWorkspace
from quant_studio.setup import SettingsStore


def _read_state(root: Path, settings_store=None) -> dict:
    state = {
        "config": None,
        "config_error": "",
        "uploads": [],
        "datasets": [],
        "projects": [],
        "notebooks": [],
        "batches": [],
        "jobs": [],
        "runs": [],
    }
    try:
        selected = settings_store or SettingsStore(root / "studio-settings.json")
        state["config"] = selected.snapshot()
    except QuantStudioError as exc:
        state["config_error"] = str(exc)
    with suppress(QuantStudioError):
        state["uploads"] = IntakeWorkspace(root).upload_list()
    if (root / ".datasets").is_dir():
        try:
            from quant_studio.datasets import DatasetStore

            state["datasets"] = DatasetStore(root).list()
        except (QuantStudioError, OSError, ValueError, KeyError, TypeError):
            pass
    if (root / ".projects").is_dir():
        try:
            from quant_studio.projects import ProjectStore

            state["projects"] = ProjectStore(root).list()
        except (QuantStudioError, OSError, ValueError, KeyError, TypeError):
            pass
    notebook_root = root / ".notebooks"
    if notebook_root.is_dir():
        state["notebooks"] = [
            draft
            for draft in notebook_root.glob("*/research.ipynb")
            if draft.is_file() and not draft.is_symlink()
        ]
    if (root / ".batches").is_dir():
        try:
            from quant_studio.research_batches import BatchStore

            state["batches"] = BatchStore(root).list()
        except (QuantStudioError, OSError, ValueError, KeyError, TypeError):
            pass
    job_root = root / ".jobs"
    if job_root.is_dir():
        for path in job_root.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(record, dict) and record.get("job_id"):
                    state["jobs"].append(record)
            except (OSError, ValueError, TypeError):
                continue
    state["runs"] = list_runs(root, limit=None)
    return state


def _state_badge(done: bool, done_text: str, pending_text: str) -> str:
    label = done_text if done else pending_text
    css = "ready" if done else ""
    return f'<span class="badge {css}">{escape(label)}</span>'


def _names(items, *, limit=3):
    values = [
        escape(str(item.get("name") or item.get("batch_id") or "未命名"))
        for item in items[:limit]
    ]
    return "、".join(values)


def onboarding_body(root, token, *, settings_store=None):
    """Render the next concrete action from saved config and research state."""

    if not isinstance(token, str):
        raise QuantStudioError("页面令牌无效")
    root = Path(root).resolve()
    state = _read_state(root, settings_store)
    admitted = [
        item
        for item in state["datasets"]
        if item.get("kind") == "intake"
        and isinstance(item.get("intake"), dict)
        and isinstance(item["intake"].get("check"), dict)
        and item["intake"]["check"].get("allowed") is True
    ]
    active_jobs = [
        item
        for item in state["jobs"]
        if item.get("status") in {"queued", "running", "cancelling"}
    ]
    completed_runs = [
        item for item in state["runs"] if item.get("status") in {"succeeded", "checked"}
    ]
    finished_batches = [
        item
        for item in state["batches"]
        if item.get("status") in {"completed", "cancelled", "interrupted"}
    ]
    config_done = state["config"] is not None and not state["config_error"]
    if state["config_error"]:
        config_text = "配置需要修正：" + state["config_error"]
    elif config_done:
        config_text = "配置已保存；每次任务仍会独立预检所需环境和输入。"
    else:
        config_text = "尚未保存本地运行配置。先确认现有Python和仓库路径。"

    project_link = (
        f"/projects/{state['projects'][0]['id']}" if state["projects"] else "/projects"
    )
    upload_count = len(state["uploads"])
    admitted_count = len(admitted)
    project_count = len(state["projects"])
    notebook_count = len(state["notebooks"])
    batch_count = len(state["batches"])
    result_count = len(completed_runs) + len(finished_batches)
    task_text = (
        f"当前有{len(active_jobs)}个任务正在排队或执行。"
        if active_jobs
        else "当前没有排队或执行中的任务。"
    )
    admitted_badge = _state_badge(
        admitted_count > 0,
        f"已有{admitted_count}个通过准入的数据版本",
        "尚无通过准入的数据版本",
    )
    project_badge = _state_badge(
        project_count > 0, f"已有{project_count}个固定项目", "尚无研究项目"
    )
    notebook_badge = _state_badge(
        notebook_count > 0,
        f"已有{notebook_count}个Notebook草稿",
        "尚无Notebook草稿",
    )
    recent_data = f"<p>最近数据：{_names(admitted)}</p>" if admitted else ""
    recent_projects = (
        f"<p>最近项目：{_names(state['projects'])}</p>" if state["projects"] else ""
    )
    disclaimer = (
        "合成样例只用于熟悉流程和检查代码路径，不能替代真实数据、完整观测或独立核验。"
    )
    body = f'''<header class="page-head"><h1>开始研究</h1>
<p>按下面顺序完成一次可复查的研究。每一步都读取已保存记录；关闭浏览器不会取消后台任务。</p></header>
<section class="panel"><h2>运行准备</h2>
{_state_badge(config_done, "配置已保存", "需要检查配置")}
<p>{escape(config_text)}</p><p><a href="/setup">检查或修改本地配置</a></p></section>
<ol class="onboarding">
<li><section class="panel"><h2>1．导入原件</h2>
{_state_badge(upload_count > 0, f"已留存{upload_count}份原件", "尚无原件")}
<p>上传CSV、TSV或Parquet。原文件按字节身份保留；解析失败时可换编码或分隔符重试。</p>
<a href="/intake">上传或继续处理原件</a></section></li>
<li><section class="panel"><h2>2．通过质量准入</h2>
{admitted_badge}
<p>核对全量范围、问题行列、严重度和处理建议。未通过的记录会保留，不会自动补值。</p>
<a href="/intake">查看导入与质量报告</a> · <a href="/catalog">查看固定数据版本</a>
{recent_data}</section></li>
<li><section class="panel"><h2>3．固定研究项目</h2>
{project_badge}
<p>写清研究问题，并连接已经准入的数据版本和精确方案版本。保存后形成新的项目版本。</p>
<a href="/projects">创建或打开研究项目</a>
{recent_projects}</section></li>
<li><section class="panel"><h2>4．选择Notebook模板</h2>
{notebook_badge}
<p>在项目页选择数据质量探索、价格收益与观测缺口或历史财务可得性模板，
再到JupyterLab补充代码。模板不会复制真实策略。</p>
<a href="{project_link}">进入项目并创建Notebook草稿</a></section></li>
<li><section class="panel"><h2>5．提交批量实验</h2>
{_state_badge(batch_count > 0, f"已有{batch_count}个批次", "尚无批次")}
<p>先预览参数网格和候选数量，再显式提交。每个候选先预检，成功和失败都会保留。</p>
<a href="/batches">预览或查看批量实验</a><p>{escape(task_text)}</p></section></li>
<li><section class="panel"><h2>6．查看结果</h2>
{_state_badge(result_count > 0, f"已有{result_count}项可复查记录", "尚无完成结果")}
<p>查看任务日志、输入身份、运行产物和失败原因，再决定是否比较或重试。</p>
<a href="/experiments">查看实验笔记</a> · <a href="/results">查看运行结果</a> ·
<a href="/jobs">查看任务</a></section></li></ol>
<section class="panel"><p class="note">{disclaimer}</p></section>'''
    return body


def body(root, token):
    return onboarding_body(root, token)


__all__ = ["body", "onboarding_body"]
