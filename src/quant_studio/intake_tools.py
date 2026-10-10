"""Browser uploads and an explicit bridge to the independent QDK environment."""

import base64
import binascii
import hashlib
import json
import os
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.desk import workspace_root
from quant_studio.recipes import _atomic
from quant_studio.runner import _execute_subprocess
from quant_studio.runtime import configured_python
from quant_studio.settings import subprocess_environment
from quant_studio.workbench_web import field

UPLOAD_LIMIT = 24 * 1024 * 1024
_ID = re.compile(r"[a-f0-9]{32}\Z")
_LOCK = threading.RLock()
_TYPES = {"string", "integer", "number", "boolean", "date", "datetime"}
PURPOSES = {
    "exploration": "数据探索",
    "daily_bars_research": "日线行情研究",
    "historical_financial_factor_backtest": "历史财务因子回放",
}


def _hash(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _safe_directory(root, *parts):
    path = root.joinpath(*parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise QuantStudioError("导入路径越界")
    for current in (path, *path.parents):
        if current.is_symlink() or (
            hasattr(current, "is_junction") and current.is_junction()
        ):
            raise QuantStudioError("导入目录不可包含链接")
        if current == root:
            break
    return path


class IntakeWorkspace:
    def __init__(self, runs_root):
        self.runs_root = Path(runs_root).absolute()
        self.root = _safe_directory(self.runs_root, ".intake")

    def upload(self, filename, encoded):
        if (
            not isinstance(filename, str)
            or not 1 <= len(filename) <= 200
            or any(c in filename for c in "/\\:\x00\r\n")
            or filename in {".", ".."}
            or Path(filename).suffix.lower() not in {".csv", ".tsv", ".parquet"}
        ):
            raise QuantStudioError("请选择CSV、TSV或Parquet文件，文件名不能含路径")
        if not isinstance(encoded, str) or len(encoded) > ((UPLOAD_LIMIT + 2) // 3) * 4:
            raise QuantStudioError("网页上传上限为24MiB")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise QuantStudioError("上传内容编码无效") from exc
        if not raw or len(raw) > UPLOAD_LIMIT:
            raise QuantStudioError("上传文件为空或超过24MiB")
        identifier = uuid.uuid4().hex
        directory = _safe_directory(self.root, "incoming", identifier)
        directory.mkdir(parents=True)
        source = directory / ("source" + Path(filename).suffix.lower())
        with source.open("xb") as handle:
            handle.write(raw)
        item = {
            "id": identifier,
            "filename": filename,
            "source": source.name,
            "sha256": _hash(source),
            "bytes": len(raw),
            "created_at": datetime.now(UTC).isoformat(),
        }
        _atomic(directory / "upload.json", json.dumps(item, ensure_ascii=False))
        return item

    def upload_record(self, identifier):
        if not _ID.fullmatch(str(identifier)):
            raise QuantStudioError("上传标识无效")
        directory = _safe_directory(self.root, "incoming", identifier)
        try:
            item = json.loads((directory / "upload.json").read_text(encoding="utf-8"))
            if item["id"] != identifier or item["source"] not in {
                "source.csv",
                "source.tsv",
                "source.parquet",
            }:
                raise ValueError("invalid upload")
            return item
        except (OSError, KeyError, ValueError, TypeError) as exc:
            raise QuantStudioError("上传记录不可读取") from exc

    def source(self, identifier):
        item = self.upload_record(identifier)
        path = _safe_directory(self.root, "incoming", identifier, item["source"])
        if not path.is_file() or _hash(path) != item["sha256"]:
            raise QuantStudioError("上传原件缺失或发生变化")
        return path

    def upload_list(self):
        return sorted(
            [
                self.upload_record(path.name)
                for path in (self.root / "incoming").glob("*")
                if path.is_dir() and _ID.fullmatch(path.name)
            ],
            key=lambda value: value["created_at"],
            reverse=True,
        )

    def prepare(self, upload_id, name, contract, *, parent=None):
        self.source(upload_id)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise QuantStudioError("数据集名称须为1至120字")
        identifier = uuid.uuid4().hex
        directory = _safe_directory(self.root, "requests", identifier)
        directory.mkdir(parents=True)
        contract_path = directory / "contract.json"
        _atomic(
            contract_path, json.dumps(contract, ensure_ascii=False, allow_nan=False)
        )
        record = {
            "id": identifier,
            "upload_id": upload_id,
            "name": name.strip(),
            "parent": parent,
            "contract_sha256": _hash(contract_path),
            "source_sha256": self.upload_record(upload_id)["sha256"],
            "status": "prepared",
            "created_at": datetime.now(UTC).isoformat(),
        }
        _atomic(directory / "request.json", json.dumps(record, ensure_ascii=False))
        return record

    def request(self, identifier):
        if not _ID.fullmatch(str(identifier)):
            raise QuantStudioError("导入请求标识无效")
        directory = _safe_directory(self.root, "requests", identifier)
        try:
            record = json.loads(
                (directory / "request.json").read_text(encoding="utf-8")
            )
            if (
                record["id"] != identifier
                or _hash(directory / "contract.json") != record["contract_sha256"]
            ):
                raise QuantStudioError("导入契约发生变化")
            if (
                self.upload_record(record["upload_id"])["sha256"]
                != record["source_sha256"]
            ):
                raise QuantStudioError("原件身份变化")
            self.source(record["upload_id"])
            return record
        except (OSError, KeyError, ValueError, TypeError) as exc:
            raise QuantStudioError("导入请求不可读取") from exc

    def request_list(self):
        folder = self.root / "requests"
        if not folder.exists():
            return []
        return sorted(
            [
                self.request(p.name)
                for p in folder.iterdir()
                if p.is_dir() and _ID.fullmatch(p.name)
            ],
            key=lambda value: value["created_at"],
            reverse=True,
        )

    def update(self, identifier, **values):
        with _LOCK:
            record = self.request(identifier)
            record.update(values)
            _atomic(
                self.root / "requests" / identifier / "request.json",
                json.dumps(record, ensure_ascii=False),
            )
            return record

    def backend(self, action, arguments=(), *, control=None):
        if action not in {
            "inspect-source",
            "import",
            "list",
            "show",
            "check",
            "read",
            "diff",
            "check-snapshot",
            "verify-snapshot",
            "read-snapshot",
        }:
            raise QuantStudioError("未知数据操作")
        python, source = self.runtime()
        script = source / "src/quant_data_kit/research_intake.py"
        argv = [python, "-I", "-X", "utf8", str(script), action]
        if action != "inspect-source" and not action.endswith("-snapshot"):
            argv += ["--root", str(self.root / "catalog")]
        argv += [str(item) for item in arguments]
        result = _execute_subprocess(
            argv,
            cwd=source,
            env=subprocess_environment(),
            timeout=600,
            control=control,
        )
        try:
            value = json.loads(result.stdout)
            if (
                value["schema_version"] != "qdk.research-intake-response/v1"
                or value["action"] != action
            ):
                raise ValueError("response contract")
        except (KeyError, ValueError, TypeError) as exc:
            raise QuantStudioError(
                "数据环境未返回有效回执：" + result.stderr[-2000:]
            ) from exc
        if result.returncode and value.get("ok"):
            raise QuantStudioError("数据环境退出状态与回执矛盾")
        return value

    def runtime(self):
        config_path = os.environ.get("QUANT_STUDIO_INTAKE_CONFIG")
        if config_path:
            try:
                config = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
                python, source = config["python"], Path(config["source"])
            except (OSError, KeyError, ValueError, TypeError) as exc:
                raise QuantStudioError("数据接入环境配置无效") from exc
        else:
            python, workspace = configured_python("quant-data-kit"), workspace_root()
            source = workspace / "quant-data-kit" if workspace else None
        if (
            not python
            or not Path(python).is_absolute()
            or not Path(python).is_file()
            or not source
        ):
            raise QuantStudioError("请在环境配置中连接quant-data-kit的独立Python和源码")
        script = source / "src/quant_data_kit/research_intake.py"
        if not source.is_absolute() or not script.is_file():
            raise QuantStudioError("所选QDK源码尚未包含数据准入接口")
        return python, source

    def register(self, name, version, *, purpose="exploration", scope=None):
        from quant_studio.datasets import DatasetStore

        if purpose not in PURPOSES:
            raise QuantStudioError("未知数据用途")
        if not isinstance(version, str) or not version:
            raise QuantStudioError("请选择固定数据版本")
        scope = scope or {}
        if set(scope) - {"columns", "symbols", "start", "end"}:
            raise QuantStudioError("研究范围包含未知字段")
        scope = {
            key: value for key, value in scope.items() if value not in (None, "", [])
        }
        arguments = ["--name", name, "--version", version, "--purpose", purpose]
        for key in ("columns", "symbols", "start", "end"):
            if value := scope.get(key):
                arguments += [
                    "--" + key,
                    *(value if isinstance(value, list) else [value]),
                ]
        response = self.backend("check", arguments)
        check = response.get("data") or {}
        if not response.get("ok") or check.get("allowed") is not True:
            raise QuantStudioError("此用途或范围未通过数据准入，请先查看质量报告")
        shown = self.backend("show", ["--name", name, "--version", version])
        if not shown.get("ok") or shown["data"]["version"]["id"] != version:
            raise QuantStudioError("数据版本身份不一致")
        path = _safe_directory(self.root, "catalog", "versions", version)
        hashes = {}
        for item in path.rglob("*"):
            _safe_directory(path, item.relative_to(path))
            if item.is_file():
                hashes[item.relative_to(path).as_posix()] = _hash(item)
        if not hashes or "manifest.json" not in hashes:
            raise QuantStudioError("数据快照不完整")
        metadata = shown["data"]["contract"].get("metadata", {})
        record = {
            "schema": "quant-studio.dataset/v1",
            "id": uuid.uuid4().hex,
            "name": name,
            "template_id": "research-intake",
            "kind": "intake",
            "parent": None,
            "path": str(path),
            "hashes": hashes,
            "registered_at": datetime.now(UTC).isoformat(),
            "provider": metadata.get("provider") or metadata.get("source", "未声明"),
            "symbols": scope.get("symbols", []),
            "start": scope.get("start") or "按快照",
            "end": scope.get("end") or "按快照",
            "evidence": "已核验所选用途；未认证市场完整性",
            "limits": f"固定用途：{PURPOSES[purpose]}；研究代码仍需遵守所选范围。",
            "intake": {
                "name": name,
                "version": version,
                "purpose": purpose,
                "scope": scope,
                "check": check,
            },
            "identity": hashlib.sha256(
                json.dumps(hashes, sort_keys=True).encode()
            ).hexdigest(),
        }
        store = DatasetStore(self.runs_root)
        _atomic(
            store.root / (record["id"] + ".json"),
            json.dumps(record, ensure_ascii=False),
        )
        return record

    def inspect(self, upload_id, *, encoding="utf-8-sig", delimiter=","):
        return self.backend(
            "inspect-source",
            [
                "--source",
                self.source(upload_id),
                "--encoding",
                encoding,
                "--delimiter",
                delimiter,
            ],
        )

    def execute(self, identifier, control):
        record = self.request(identifier)
        # Exclusive claim prevents repeated queue submissions from publishing twice.
        directory = self.root / "requests" / identifier
        try:
            with (directory / "execution.claim").open("x") as handle:
                handle.write(datetime.now(UTC).isoformat())
        except FileExistsError as exc:
            raise QuantStudioError("此导入请求已经执行，请新建请求重试") from exc
        self.update(identifier, status="running")
        argv = [
            "--source",
            self.source(record["upload_id"]),
            "--name",
            record["name"],
            "--contract",
            directory / "contract.json",
        ]
        if record["parent"]:
            argv += ["--parent", record["parent"]]
        try:
            value = self.backend("import", argv, control=control)
            status = "succeeded" if value.get("ok") else "blocked"
            _atomic(directory / "response.json", json.dumps(value, ensure_ascii=False))
            self.update(identifier, status=status, response=value)
            return {
                "status": status,
                "message": f"导入记录：/intake/requests/{identifier}",
                "result_url": f"/intake/requests/{identifier}",
            }
        except Exception:
            self.update(
                identifier,
                status="interrupted" if control.cancel_requested else "failed",
            )
            raise


def contract_from_form(data, columns):
    kind = field(data, "kind", "table")
    if kind not in {"table", "daily_bars", "history"}:
        raise QuantStudioError("请选择数据类型")
    metadata = {
        key: field(data, key, "")
        for key in ("source", "provider", "timezone", "adjustment", "availability")
    }
    encoding = field(data, "encoding", "utf-8-sig")
    delimiter = field(data, "delimiter", ",")
    if encoding not in {"utf-8", "utf-8-sig", "gb18030"} or delimiter not in {
        ",",
        "\t",
        ";",
        "|",
    }:
        raise QuantStudioError("编码或分隔符无效")
    keys = [
        item.strip()
        for item in field(data, "primary_key", "").split(",")
        if item.strip()
    ]
    mapping, types, formats, units = {}, {}, {}, {}
    for index, column in enumerate(columns):
        target = field(data, f"map_{index}", column).strip()
        dtype = field(data, f"type_{index}", "string")
        fmt = field(data, f"format_{index}", "").strip()
        unit = field(data, f"unit_{index}", "").strip()
        if not target:
            continue
        if target in mapping:
            raise QuantStudioError("规范列名重复：" + target)
        if dtype not in _TYPES:
            raise QuantStudioError("字段类型无效")
        mapping[target], types[target] = column, dtype
        if fmt:
            formats[target] = fmt
        if unit:
            units[target] = unit
    if not mapping or any(key not in mapping for key in keys):
        raise QuantStudioError("请选择有效列映射及主键")
    metadata["units"] = units
    return {
        "schema_version": "qdk.research-intake-contract/v1",
        "kind": kind,
        "input": {"format": "csv", "encoding": encoding, "delimiter": delimiter},
        "mapping": mapping,
        "types": types,
        "formats": formats,
        "primary_key": keys,
        "metadata": {key: value for key, value in metadata.items() if value},
        "limits": {"max_bytes": UPLOAD_LIMIT, "max_rows": 1000000},
    }
