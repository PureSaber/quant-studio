"""Registered immutable input identities and explicit native data acquisition."""

import hashlib
import json
import re
import sys
import threading
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.recipes import _atomic
from quant_studio.runtime import configured_python
from quant_studio.settings import subprocess_environment
from quant_studio.templates import load_template

_LOCK = threading.RLock()
_ID = re.compile(r"[a-f0-9]{32}\Z")
PROVIDERS = {"hk-equity-daily": ["akshare_sina_hk", "akshare_eastmoney_hk"]}


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def describe_input(template_id, path):
    template = load_template(template_id)
    path = Path(path)
    if not path.is_absolute() or not path.exists():
        raise QuantStudioError("请选择存在的服务端绝对路径")
    path = path.resolve()
    source = template.metadata.get("input_source", {})
    required = source.get("required_files", [])
    if not source:
        raise QuantStudioError("此研究类型没有声明外部数据输入")
    if source.get("kind") == "file":
        if not path.is_file() or path.stat().st_size > 48_000:
            raise QuantStudioError("研究配置必须是 48 KB 以内的文件")
        from quant_studio.recipes import parse_config

        parse_config(path.read_text(encoding="utf-8-sig"))
        return {
            "path": str(path),
            "hashes": {".": file_hash(path)},
            "provider": "原生配置文件",
            "symbols": [],
            "start": "未声明",
            "end": "未声明",
            "evidence": "待原生预检",
            "limits": "登记绑定配置文件；其引用的行情文件由原生预检检查。",
        }
    if not path.is_dir():
        raise QuantStudioError("此研究类型需要数据目录")
    missing = [name for name in required if not (path / name).is_file()]
    if missing:
        raise QuantStudioError("缺少必需文件：" + "、".join(missing))
    manifest_path = path / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.is_file() else {}
    declared = manifest.get("files", {})
    selected = {name: path / name for name in required}
    if manifest_path.is_file():
        selected["manifest.json"] = manifest_path
    if isinstance(declared, dict):
        for name, expected in declared.items():
            if (
                not isinstance(name, str)
                or Path(name).is_absolute()
                or ".." in Path(name).parts
            ):
                raise QuantStudioError("清单含有越界路径")
            candidate = (path / name).resolve()
            if not candidate.is_relative_to(path) or not candidate.is_file():
                raise QuantStudioError(f"清单文件缺失或越界：{name}")
            digest = (
                expected
                if isinstance(expected, str)
                else expected.get("sha256")
                if isinstance(expected, dict)
                else None
            )
            if digest and file_hash(candidate) != digest:
                raise QuantStudioError(f"清单文件完整性检查失败：{name}")
            selected[name] = candidate
    if (
        manifest.get("schema") == "quant-hk-snapshot/v1"
        and manifest.get("status") != "complete"
    ):
        raise QuantStudioError("港股快照未完整采集")
    # Directories without native hash manifests get a byte identity, not certification.
    if not declared:
        for candidate in path.rglob("*"):
            if candidate.is_file() and candidate.suffix.lower() in {
                ".csv",
                ".parquet",
                ".json",
                ".yaml",
                ".yml",
            }:
                if not candidate.resolve().is_relative_to(path):
                    raise QuantStudioError("数据目录包含越界链接")
                selected[candidate.relative_to(path).as_posix()] = candidate
                if len(selected) > 3000:
                    raise QuantStudioError("数据文件过多，请选择具体快照目录")
    if not selected:
        raise QuantStudioError("目录内没有支持的数据文件")
    origin = manifest.get("source", {})
    provider = str(
        manifest.get("provider")
        or (
            origin.get("provider", "本地已声明输入")
            if isinstance(origin, dict)
            else origin or "本地已声明输入"
        )
    )
    evidence = (
        "合成 / 测试"
        if any(v in provider.lower() for v in ("fixture", "synthetic", "demo"))
        else "来源按原始清单；待原生预检"
    )
    return {
        "path": str(path),
        "hashes": {name: file_hash(p) for name, p in selected.items()},
        "provider": provider,
        "symbols": manifest.get("symbols", []),
        "start": manifest.get("start", manifest.get("requested_start", "未声明")),
        "end": manifest.get("end", manifest.get("requested_end", "未声明")),
        "evidence": evidence,
        "limits": (
            "文件字节与登记时一致；日期完整性、历史规则和因子要求以原生预检为准。"
        ),
    }


class DatasetStore:
    def __init__(self, runs_root):
        self.runs_root = Path(runs_root)
        self.root = self.runs_root / ".datasets"
        self.root.mkdir(parents=True, exist_ok=True)

    def register(self, name, template_id, path, *, parent=None):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise QuantStudioError("数据集名称须为 1–120 字")
        value = {
            "schema": "quant-studio.dataset/v1",
            "id": uuid.uuid4().hex,
            "name": name.strip(),
            "template_id": template_id,
            "parent": parent,
            "registered_at": datetime.now(UTC).isoformat(),
            **describe_input(template_id, path),
        }
        value["identity"] = hashlib.sha256(
            json.dumps(value["hashes"], sort_keys=True).encode()
        ).hexdigest()
        with _LOCK:
            _atomic(
                self.root / (value["id"] + ".json"),
                json.dumps(value, ensure_ascii=False, indent=2),
            )
        return value

    def get(self, identifier):
        if not _ID.fullmatch(str(identifier)):
            raise QuantStudioError("数据集标识无效")
        try:
            return read_json(self.root / f"{identifier}.json")
        except (OSError, ValueError) as exc:
            raise QuantStudioError("数据集不存在或记录损坏") from exc

    def list(self, template_id=None):
        records = [self.get(path.stem) for path in self.root.glob("*.json")]
        return sorted(
            [
                r
                for r in records
                if template_id is None or r["template_id"] == template_id
            ],
            key=lambda r: r["registered_at"],
            reverse=True,
        )

    def verify(self, identifier):
        item = self.get(identifier)
        problems = []
        root = Path(item["path"])
        for name, digest in item["hashes"].items():
            path = root if name == "." else root / name
            if (
                not path.resolve().is_relative_to(root.resolve())
                or path.is_symlink()
                or not path.is_file()
                or file_hash(path) != digest
            ):
                problems.append(f"文件缺失或发生变化：{name}")
        try:
            if item.get("kind") == "intake":
                from quant_studio.intake_tools import IntakeWorkspace, _safe_directory

                link = item["intake"]
                _safe_directory(root)
                arguments = ["--snapshot", str(root), "--purpose", link["purpose"]]
                for key, value in link["scope"].items():
                    if key not in {"columns", "symbols", "start", "end"}:
                        raise QuantStudioError("登记范围包含未知字段")
                    if value:
                        arguments += [
                            "--" + key,
                            *(value if isinstance(value, list) else [value]),
                        ]
                result = IntakeWorkspace(self.runs_root).backend(
                    "check-snapshot", arguments
                )
                checked = result.get("data") or {}
                if (
                    not result.get("ok")
                    or not checked.get("allowed")
                    or checked.get("version_id") != link["version"]
                ):
                    problems.append("已登记用途的数据准入复核失败")
                for path in root.rglob("*"):
                    _safe_directory(root, path.relative_to(root))
                current = {
                    "hashes": {
                        p.relative_to(root).as_posix(): file_hash(p)
                        for p in root.rglob("*")
                        if p.is_file()
                    }
                }
            else:
                current = describe_input(item["template_id"], item["path"])
            if set(current["hashes"]) != set(item["hashes"]):
                problems.append("数据文件集合发生变化，请登记新版本")
        except (OSError, ValueError, QuantStudioError) as exc:
            problems.append(str(exc))
        return {
            "valid": not problems,
            "problems": problems,
            "files": len(item["hashes"]),
        }

    def select(self, identifier, template_id):
        item = self.get(identifier)
        if item["template_id"] != template_id:
            raise QuantStudioError("数据集不属于此研究类型")
        report = self.verify(identifier)
        if not report["valid"]:
            raise QuantStudioError(
                "数据完整性发生变化；请登记为新数据集版本："
                + "；".join(report["problems"][:5])
            )
        return item["path"]


def collection_request(name, template_id, symbols, start, end, provider, parent=None):
    if template_id not in PROVIDERS or provider not in PROVIDERS[template_id]:
        raise QuantStudioError("请选择已支持的显式数据源")
    codes = list(dict.fromkeys(re.split(r"[,，\s]+", symbols.strip())))
    if not 1 <= len(codes) <= 20 or any(
        not re.fullmatch(r"\d{5}", code) for code in codes
    ):
        raise QuantStudioError("请输入 1–20 个五位港股代码，用空格或逗号分隔")
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as exc:
        raise QuantStudioError("日期格式应为 YYYY-MM-DD") from exc
    if first >= last or (last - first).days > 3660 or last > datetime.now().date():
        raise QuantStudioError("日期范围须先后有序、不超过十年且不晚于今天")
    if not name.strip() or len(name) > 120:
        raise QuantStudioError("请填写 1–120 字的数据集名称")
    return {
        "name": name,
        "template_id": template_id,
        "symbols": codes,
        "start": start,
        "end": end,
        "provider": provider,
        "parent": parent,
    }


def collect_dataset(runs_root, job, control):
    from quant_studio.runner import _execute_subprocess

    root = Path(runs_root)
    request = job["collection"]
    # Revalidate persisted job fields; no caller-controlled executable or output path.
    request = collection_request(
        request["name"],
        request["template_id"],
        " ".join(request["symbols"]),
        request["start"],
        request["end"],
        request["provider"],
        request.get("parent"),
    )
    identifier = uuid.uuid4().hex
    directory = root / ".collections" / identifier
    directory.mkdir(parents=True)
    snapshot = directory / "snapshot"
    receipt = {
        "id": identifier,
        "request": request,
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
    }
    path = directory / "receipt.json"
    _atomic(path, json.dumps(receipt, ensure_ascii=False))
    runtime = configured_python("quant-hk-equity") or sys.executable
    program = (
        "import json,sys; from pathlib import Path; "
        "from quant_data_kit.hong_kong import capture_hk_snapshot; "
        "p=json.loads(sys.argv[1]); capture_hk_snapshot("
        "Path(sys.argv[2]),p['symbols'],p['start'],p['end'],provider=p['provider'])"
    )
    argv = [runtime, "-X", "utf8", "-c", program, json.dumps(request), str(snapshot)]
    try:
        control.stage("native", "正在调用已选数据源；新建快照，不覆盖旧数据")
        outcome = _execute_subprocess(
            argv,
            cwd=directory,
            env=subprocess_environment(),
            timeout=600,
            control=control,
        )
        (directory / "stdout.txt").write_text(outcome.stdout, encoding="utf-8")
        (directory / "stderr.txt").write_text(outcome.stderr, encoding="utf-8")
        if outcome.returncode:
            raise QuantStudioError("原生采集失败，详见任务日志；未登记为可用数据集")
        item = DatasetStore(root).register(
            request["name"],
            request["template_id"],
            str(snapshot),
            parent=request.get("parent"),
        )
        receipt.update(status="succeeded", dataset_id=item["id"])
        return {
            "status": "succeeded",
            "message": (
                f"数据采集并登记完成：{item['name']}。"
                "可在数据集页面选择；研究前请执行原生预检。"
            ),
        }
    except Exception as exc:
        receipt.update(status="failed", error=str(exc))
        raise
    finally:
        receipt["finished_at"] = datetime.now(UTC).isoformat()
        _atomic(path, json.dumps(receipt, ensure_ascii=False, indent=2))
