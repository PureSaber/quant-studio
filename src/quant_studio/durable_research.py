"""Submit shared-snapshot studies to an independently operated research worker."""

import hashlib
import json
import os
import re
import subprocess
from copy import deepcopy
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.recipes import _atomic


class DurableResearchClient:
    def __init__(self, config: Path):
        try:
            self.config = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise QuantStudioError("后台研究配置无法读取，请检查管理员配置") from exc
        if (
            not isinstance(self.config, dict)
            or self.config.get("schema_version") != "quant-studio.durable-research/v1"
        ):
            raise QuantStudioError("后台研究配置版本不支持")
        for key in (
            "python",
            "database",
            "artifact_root",
            "cache_database",
            "requests_root",
        ):
            value = self.config.get(key)
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise QuantStudioError(f"后台研究配置需要绝对路径：{key}")
        if not Path(self.config["python"]).is_file():
            raise QuantStudioError("后台研究解释器不存在")
        self.snapshots = self.config.get("snapshots")
        if not isinstance(self.snapshots, dict) or not self.snapshots:
            raise QuantStudioError("尚未配置可研究的不可变快照")
        for key, item in self.snapshots.items():
            if not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", key):
                raise QuantStudioError("快照标识无效")
            if not isinstance(item, dict) or not isinstance(item.get("title"), str):
                raise QuantStudioError("快照需要名称及完整研究请求")
            request = item.get("request")
            if not isinstance(request, dict) or not isinstance(
                request.get("payload"), dict
            ):
                raise QuantStudioError("快照需要完整研究请求")
            factors = request["payload"].get("factors")
            if (
                request.get("kind") != "rolling_research"
                or not isinstance(factors, list)
                or len(factors) != 1
                or not isinstance(factors[0], dict)
                or type(factors[0].get("window_periods")) is not int
                or not 1 <= factors[0]["window_periods"] <= 4096
            ):
                raise QuantStudioError("网页入口需要单因子的滚动研究请求")

    def _call(self, *args: str) -> object:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME", "QUANT_RESEARCH_REGISTRY_DB"}
        }
        environment.update(
            QUANT_RESEARCH_STORE_ROOT=self.config["artifact_root"],
            QUANT_RESEARCH_CACHE_DB=self.config["cache_database"],
        )
        command = [
            self.config["python"],
            "-I",
            "-m",
            "quant_pipeline.durable_client",
            "--database",
            self.config["database"],
            *args,
        ]
        try:
            completed = subprocess.run(
                command,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise QuantStudioError("后台研究客户端未完成请求") from exc
        if completed.returncode:
            raise QuantStudioError(
                "后台研究请求被拒绝，请检查固定输入、任务身份及运行环境"
            )
        try:
            return json.loads(completed.stdout)
        except (ValueError, TypeError) as exc:
            raise QuantStudioError("后台研究客户端返回了无效结果") from exc

    def submit(self, snapshot: str, window: str, key: str, revision: str) -> dict:
        if snapshot not in self.snapshots or not re.fullmatch(r"[a-f0-9]{32}", key):
            raise QuantStudioError("研究快照或提交标识无效")
        if not re.fullmatch(r"[1-9][0-9]{0,3}", window) or int(window) > 4096:
            raise QuantStudioError("观察窗口必须为1至4096个周期")
        if revision != self.revision(snapshot):
            raise QuantStudioError("研究输入配置已经改变，请刷新页面")
        request = deepcopy(self.snapshots[snapshot]["request"])
        request["run_id"] = "studio-" + key
        request["payload"]["factors"][0]["window_periods"] = int(window)
        encoded = json.dumps(
            request, sort_keys=True, ensure_ascii=False, allow_nan=False
        )
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        root = Path(self.config["requests_root"])
        root.mkdir(parents=True, exist_ok=True)
        destination = root / f"{digest}.json"
        if destination.exists() and destination.read_text(encoding="utf-8") != encoded:
            raise QuantStudioError("已保存请求的内容身份不一致")
        _atomic(destination, encoded)
        return self._call(
            "submit",
            "--request",
            str(destination),
            "--key",
            "studio/" + key,
            "--research",
        )

    def revision(self, snapshot: str) -> str:
        value = json.dumps(self.snapshots[snapshot], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def list(self) -> list[dict]:
        return [
            job
            for job in self._call("list", "--limit", "30")
            if job["request"]["kind"] == "rolling_research"
        ]

    def query(self, action: str, job_id: str) -> object:
        if action not in {"get", "cancel", "events", "attempts"}:
            raise QuantStudioError("后台研究操作不支持")
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise QuantStudioError("后台研究任务标识无效")
        job = self._call("get", job_id)
        if job["request"]["kind"] != "rolling_research":
            raise QuantStudioError("此任务不属于后台因子研究")
        return job if action == "get" else self._call(action, job_id)


def configured_client() -> DurableResearchClient:
    value = os.environ.get("QUANT_STUDIO_DURABLE_CONFIG")
    if not value or not Path(value).is_absolute():
        raise QuantStudioError("请先配置独立研究Worker及共享快照")
    return DurableResearchClient(Path(value))
