"""Data maintenance receipts, explicit CSV coverage audits and managed ETF refresh."""

import csv
import json
import re
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.data_catalog import registered_file
from quant_studio.datasets import DatasetStore, file_hash
from quant_studio.notebook_data_reader import snapshot_hashes
from quant_studio.projects import digest
from quant_studio.recipes import _atomic


class DataMaintenance:
    def __init__(self, root):
        self.runs = Path(root).resolve()
        self.root = self.runs / ".data-maintenance"

    def _source_path(self, identifier):
        if not re.fullmatch(r"[a-f0-9]{32}", str(identifier)):
            raise QuantStudioError("数据维护来源标识无效")
        path = self.root / "sources" / identifier
        if path.resolve() != path:
            raise QuantStudioError("数据维护目录不可为链接")
        return path

    def sources(self):
        return [self.source(p.name) for p in (self.root / "sources").glob("*")]

    def source(self, identifier):
        try:
            if (self._source_path(identifier) / "source.json").is_symlink():
                raise QuantStudioError("数据来源文件不可为链接")
            return json.loads(
                (self._source_path(identifier) / "source.json").read_text("utf-8")
            )
        except (OSError, ValueError) as exc:
            raise QuantStudioError("数据维护来源不存在") from exc

    def create_etf(self, name, symbols, start):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise QuantStudioError("数据名称须为1至120字")
        if not 1 <= len(symbols) <= 20 or any(
            not re.fullmatch(r"\d{6}", s) for s in symbols
        ):
            raise QuantStudioError("请输入1至20个六位ETF代码")
        date.fromisoformat(start)
        value = {
            "id": uuid.uuid4().hex,
            "name": name.strip(),
            "symbols": list(dict.fromkeys(symbols)),
            "start": start,
            "provider": "akshare_sina_etf",
        }
        directory = self._source_path(value["id"])
        directory.mkdir(parents=True)
        _atomic(directory / "source.json", json.dumps(value, ensure_ascii=False))
        return value

    def request(self, identifier, end):
        source = self.source(identifier)
        if date.fromisoformat(end) < date.fromisoformat(source["start"]):
            raise QuantStudioError("更新截止不能早于开始日期")
        catalog = self._source_path(identifier) / "data" / "catalog.json"
        current = (
            json.loads(catalog.read_text("utf-8"))["current_snapshot_id"]
            if catalog.exists()
            else None
        )
        return {
            "collection_type": "etf-maintenance",
            "template_id": "a-share-four-factor",
            "source_id": identifier,
            "end": end,
            "expected_snapshot": current,
            "source_sha256": digest(source),
        }

    def execute(self, request, control):
        from quant_studio.intake_tools import IntakeWorkspace
        from quant_studio.runner import _execute_subprocess
        from quant_studio.settings import subprocess_environment

        identifier = request["source_id"]
        if self.request(identifier, request["end"]) != request:
            raise QuantStudioError("数据源已更新，请刷新页面后重新提交")
        source = self.source(identifier)
        directory = self._source_path(identifier)
        data_root = directory / "data"
        python, _ = IntakeWorkspace(self.runs).runtime()
        action = "update" if request["expected_snapshot"] else "build"
        argv = [
            str(python),
            "-I",
            "-X",
            "utf8",
            "-m",
            "quant_data_kit.research_dataset",
            action,
            "--root",
            str(data_root),
            "--end",
            request["end"],
        ]
        if action == "build":
            argv += [
                "--symbols",
                *source["symbols"],
                "--start",
                source["start"],
                "--provider",
                source["provider"],
            ]
        receipt = {
            "id": uuid.uuid4().hex,
            "source_id": identifier,
            "request": request,
            "started_at": datetime.now(UTC).isoformat(),
            "status": "running",
        }
        receipt_path = directory / (receipt["id"] + ".json")
        _atomic(receipt_path, json.dumps(receipt, ensure_ascii=False))
        try:
            outcome = _execute_subprocess(
                argv,
                cwd=directory,
                env=subprocess_environment(),
                timeout=900,
                control=control,
            )
            if outcome.returncode:
                raise QuantStudioError("ETF更新失败：" + outcome.stderr[-2000:])
            evidence = json.loads(outcome.stdout)
            snapshot_id = evidence["snapshot_id"]
            if not re.fullmatch(r"sha256-[a-f0-9]{64}", snapshot_id):
                raise QuantStudioError("ETF快照标识无效")
            snapshot = data_root / "snapshots" / snapshot_id
            hashes = snapshot_hashes(snapshot)
            manifest = json.loads((snapshot / "manifest.json").read_text("utf-8"))
            existing = [
                d
                for d in DatasetStore(self.runs).list()
                if d.get("maintenance_source") == identifier
            ]
            record = {
                "schema": "quant-studio.dataset/v1",
                "kind": "research-etf",
                "id": uuid.uuid4().hex,
                "name": source["name"],
                "template_id": "a-share-four-factor",
                "maintenance_source": identifier,
                "parent": existing[0]["id"] if existing else None,
                "registered_at": datetime.now(UTC).isoformat(),
                "path": str(snapshot),
                "hashes": hashes,
                "identity": digest(hashes),
                "provider": source["provider"],
                "symbols": source["symbols"],
                "start": manifest["requested_start"],
                "end": manifest["requested_end"],
                "evidence": "QDK原生覆盖与数值检查",
                "quality": evidence,
                "limits": json.dumps(
                    manifest.get("consumer_contract", {}), ensure_ascii=False
                ),
            }
            _atomic(
                DatasetStore(self.runs).root / f"{record['id']}.json",
                json.dumps(record, ensure_ascii=False),
            )
            receipt.update(
                status="succeeded", dataset_id=record["id"], evidence=evidence
            )
            return {
                "status": "succeeded",
                "message": "ETF新快照已登记，旧研究数据版本保留",
            }
        except Exception as exc:
            receipt.update(status="failed", message=str(exc))
            raise
        finally:
            receipt["finished_at"] = datetime.now(UTC).isoformat()
            _atomic(receipt_path, json.dumps(receipt, ensure_ascii=False))

    def audit(self, identifier, filename, date_column, symbol_column, expected_symbols):
        item, path = registered_file(self.runs, identifier, filename)
        if (
            path.suffix.lower() not in {".csv", ".tsv"}
            or path.stat().st_size > 64_000_000
        ):
            raise QuantStudioError(
                "覆盖检查支持64MB以内CSV/TSV；Parquet可在数据目录预览"
            )
        symbols, days, seen, missing = set(), [], set(), {}
        duplicates = rows = 0
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(
                stream, delimiter="\t" if path.suffix.lower() == ".tsv" else ","
            )
            if not reader.fieldnames or not {date_column, symbol_column} <= set(
                reader.fieldnames
            ):
                raise QuantStudioError("请指定文件中实际存在的日期列和标的列")
            for row in reader:
                rows += 1
                if rows > 1_000_000 or None in row or None in row.values():
                    raise QuantStudioError("数据超过100万行或行列数不一致")
                for key, value in row.items():
                    missing[key] = missing.get(key, 0) + (not value.strip())
                symbol = row[symbol_column]
                day = date.fromisoformat(row[date_column]).isoformat()
                symbols.add(symbol)
                days.append(day)
                key = (symbol, day)
                duplicates += key in seen
                seen.add(key)
        if file_hash(path) != item["hashes"][filename]:
            raise QuantStudioError("检查期间数据发生变化")
        report = {
            "dataset_id": identifier,
            "identity": item["identity"],
            "file": filename,
            "checked_at": datetime.now(UTC).isoformat(),
            "rows": rows,
            "observed_start": min(days) if days else None,
            "observed_end": max(days) if days else None,
            "symbols": sorted(symbols),
            "missing_symbols": sorted(set(expected_symbols) - symbols),
            "duplicate_keys": duplicates,
            "missing": missing,
            "date_column": date_column,
            "symbol_column": symbol_column,
            "scope": "全文件扫描；重复按标的与日期；未校验交易日历或历史可得性",
        }
        target = self.root / "quality" / identifier / (uuid.uuid4().hex + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic(target, json.dumps(report, ensure_ascii=False))
        return report

    def report(self, identifier):
        item = DatasetStore(self.runs).get(identifier)
        paths = list((self.root / "quality" / identifier).glob("*.json"))
        if not paths:
            return None
        reports = [json.loads(p.read_text("utf-8")) for p in paths]
        report = max(reports, key=lambda r: r["checked_at"])
        try:
            registered_file(self.runs, identifier, report["file"])
            report["current"] = report["identity"] == item["identity"]
        except QuantStudioError:
            report["current"] = False
        return report
