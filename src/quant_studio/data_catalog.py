"""Bounded data inspection with explicit sample scope and registered byte identities."""

import csv
import io
import json
import subprocess
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.datasets import DatasetStore, file_hash
from quant_studio.runtime import configured_python
from quant_studio.settings import subprocess_environment
from quant_studio.templates import load_template


def catalog(root, *, query="", template=""):
    return [
        item
        for item in DatasetStore(root).list()
        if (not template or item["template_id"] == template)
        and query.casefold()
        in json.dumps(
            {k: item[k] for k in ("name", "provider", "symbols", "template_id")},
            ensure_ascii=False,
        ).casefold()
    ]


def registered_file(root, identifier, filename):
    store = DatasetStore(root)
    item = store.get(identifier)
    if filename not in item["hashes"]:
        raise QuantStudioError("只能查看数据集清单中已登记的文件")
    base = Path(item["path"]).resolve()
    path = base if filename == "." else (base / filename).resolve()
    if filename != "." and not path.is_relative_to(base):
        raise QuantStudioError("文件路径越界")
    if not path.is_file() or file_hash(path) != item["hashes"][filename]:
        raise QuantStudioError("文件缺失或完整性发生变化，请登记新版本")
    return item, path


def column_descriptions(root, identifier, filename):
    item = DatasetStore(root).get(identifier)
    if "manifest.json" not in item["hashes"]:
        return {}
    _, path = registered_file(root, identifier, "manifest.json")
    if path.stat().st_size > 512_000:
        raise QuantStudioError("数据字典清单超过512KB")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
        definitions = manifest.get("column_descriptions", {}).get(filename, {})
        if not isinstance(definitions, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) or len(v) > 2000
            for k, v in definitions.items()
        ):
            raise ValueError("column_descriptions须为字段名到说明的映射")
    except (ValueError, AttributeError) as exc:
        raise QuantStudioError("数据字典格式无效") from exc
    if file_hash(path) != item["hashes"]["manifest.json"]:
        raise QuantStudioError("预览期间数据字典发生变化")
    return definitions


def preview_file(root, identifier, filename, *, max_rows=10000):
    if not isinstance(max_rows, int) or not 1 <= max_rows <= 10000:
        raise QuantStudioError("检查行数须在1至10000之间")
    item, path = registered_file(root, identifier, filename)
    descriptions = column_descriptions(root, identifier, filename)
    if path.suffix.lower() == ".parquet":
        python = configured_python(
            load_template(item["template_id"]).metadata.get("workspace_repo")
        )
        if not python:
            raise QuantStudioError(
                "Parquet预览需要为此应用配置含PyArrow的独立Python环境"
            )
        try:
            result = subprocess.run(
                [
                    python,
                    "-I",
                    "-X",
                    "utf8",
                    str(Path(__file__).with_name("parquet_preview.py")),
                    str(path),
                    str(max_rows),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
                env=subprocess_environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise QuantStudioError("Parquet预览失败：" + str(exc)) from exc
        if result.returncode:
            raise QuantStudioError("Parquet预览失败：" + result.stderr[-1500:])
        try:
            report = json.loads(result.stdout)
        except ValueError as exc:
            raise QuantStudioError("预览环境没有返回数据摘要") from exc
        if file_hash(path) != item["hashes"][filename]:
            raise QuantStudioError("预览期间数据发生变化")
        for column in report["columns"]:
            column["description"] = descriptions.get(column["name"], "未声明")
        return {
            **report,
            "dataset_id": identifier,
            "identity": item["identity"],
            "file": filename,
        }
    if path.suffix.lower() not in {".csv", ".tsv"}:
        raise QuantStudioError("此预览支持CSV/TSV；其他格式请在独立研究环境中打开")
    # The byte limit bounds parsing even when a CSV contains enormous quoted cells.
    with path.open("rb") as stream:
        raw = stream.read(4_000_001)
    if len(raw) > 4_000_000:
        raise QuantStudioError("文件超过4MB预览上限，请使用研究环境查询")
    try:
        text = raw.decode("utf-8-sig")
        reader = csv.reader(
            io.StringIO(text, newline=""),
            delimiter="\t" if path.suffix.lower() == ".tsv" else ",",
            strict=True,
        )
        header = next(reader)
        if (
            not header
            or len(header) > 200
            or len(set(header)) != len(header)
            or any(not v for v in header)
        ):
            raise QuantStudioError("数据表列名为空、重复或超过200列")
        missing, rows, count, complete = [0] * len(header), [], 0, True
        duplicates, seen = 0, set()
        for row in reader:
            if count == max_rows:
                complete = False
                break
            if len(row) != len(header):
                raise QuantStudioError(f"第{count + 2}行列数与表头不一致")
            count += 1
            key = tuple(row)
            duplicates += key in seen
            seen.add(key)
            for i, cell in enumerate(row):
                missing[i] += not cell.strip()
            if len(rows) < 20:
                rows.append(row)
    except (UnicodeError, csv.Error, StopIteration) as exc:
        raise QuantStudioError("无法读取UTF-8数据表：" + str(exc)) from exc
    # Detect concurrent replacement of registered bytes during reading.
    if file_hash(path) != item["hashes"][filename]:
        raise QuantStudioError("预览期间数据发生变化")
    return {
        "dataset_id": identifier,
        "identity": item["identity"],
        "file": filename,
        "columns": [
            {
                "name": name,
                "description": descriptions.get(name, "未声明"),
                "missing": missing[i],
            }
            for i, name in enumerate(header)
        ],
        "rows": rows,
        "scanned_rows": count,
        "scan_complete": complete,
        "duplicate_rows": duplicates,
        "scope": "空值按空白单元格计数；重复按整行比较；未检查交易日覆盖或历史可得性。",
    }
