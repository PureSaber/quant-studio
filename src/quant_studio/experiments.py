"""Searchable experiment annotations and comparisons of verified saved outputs."""

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import yaml

from quant_studio import QuantStudioError
from quant_studio.desk import list_runs
from quant_studio.nav import parse_nav_csv
from quant_studio.recipes import _atomic
from quant_studio.run_record import load_run_result
from quant_studio.runner import safe_run_file
from quant_studio.templates import load_template

_LOCK = threading.RLock()


def configuration_diff(before, after):
    rows = []

    def walk(left, right, path):
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(left.keys() | right.keys()):
                walk(left.get(key), right.get(key), f"{path}.{key}" if path else key)
        elif left != right:
            rows.append({"path": path, "before": left, "after": right})

    for field in ("name", "config", "cli", "snapshot", "input_config"):
        walk(before.get(field), after.get(field), field)
    return rows


class ExperimentStore:
    def __init__(self, runs_root):
        self.root = Path(runs_root)
        self.notes = self.root / ".annotations"
        self.notes.mkdir(parents=True, exist_ok=True)

    def _run(self, identifier):
        directory = safe_run_file(self.root, identifier)
        if not (directory / "request.json").is_file():
            raise QuantStudioError("运行不存在")
        return directory

    def annotation(self, identifier):
        self._run(identifier)
        path = self.notes / f"{identifier}.json"
        if not path.exists():
            return {"title": "", "note": "", "revision": ""}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or any(
                not isinstance(value.get(key), str)
                for key in ("title", "note", "revision")
            ):
                raise ValueError("Invalid annotation")
            return value
        except (OSError, ValueError):
            return {
                "title": "",
                "note": "",
                "revision": "",
                "annotation_error": "备注无法读取",
            }

    def annotate(self, identifier, title, note, *, expected):
        if (
            not isinstance(title, str)
            or len(title) > 120
            or not isinstance(note, str)
            or len(note) > 4000
        ):
            raise QuantStudioError("实验名称最多 120 字，备注最多 4000 字")
        with _LOCK:
            previous = self.annotation(identifier)
            if previous.get("annotation_error"):
                raise QuantStudioError("备注无法读取；请保留损坏文件并恢复备份后重试")
            if previous["revision"] != expected:
                raise QuantStudioError("备注已更新，请刷新后重试")
            value = {
                "title": title,
                "note": note,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            value["revision"] = hashlib.sha256(
                json.dumps(value, sort_keys=True).encode()
            ).hexdigest()
            _atomic(
                self.notes / f"{identifier}.json", json.dumps(value, ensure_ascii=False)
            )
            return value

    def list(self, *, query="", status="", template=""):
        records = []
        for item in list_runs(self.root, limit=None):
            try:
                request = json.loads(
                    (self._run(item["run_id"]) / "request.json").read_text(
                        encoding="utf-8"
                    )
                )
                annotation = self.annotation(item["run_id"])
                recipe = request.get("recipe") or {}
                from quant_studio.presentation import template_title

                record = {
                    **item,
                    **annotation,
                    "recipe": recipe,
                    "research_project": request.get("research_project"),
                    "title": annotation["title"]
                    or recipe.get("name")
                    or template_title(item["template_id"]),
                }
                searchable = json.dumps(record, ensure_ascii=False).casefold()
                if (
                    (not query or query.casefold() in searchable)
                    and (not status or item["status"] == status)
                    and (not template or item["template_id"] == template)
                ):
                    records.append(record)
            except (OSError, ValueError, QuantStudioError):
                continue
        return records

    def compare(self, identifiers):
        if not 2 <= len(identifiers) <= 4 or len(set(identifiers)) != len(identifiers):
            raise QuantStudioError("请选择 2–4 个不同实验")
        rows = [self.measure(identifier) for identifier in identifiers]
        reasons = []
        if any(row["return"] is None for row in rows):
            reasons.append("部分实验没有通过核验的完整区间净值")
        if any(row["evidence"] == "未声明" for row in rows):
            reasons.append("部分实验缺少数据性质记录")
        for key, label in [
            ("template_id", "研究类型"),
            ("dates", "观测日期"),
            ("currency", "币种"),
            ("evidence", "数据性质"),
            ("opening_basis", "期初口径"),
        ]:
            if any(row[key] != rows[0][key] for row in rows[1:]):
                reasons.append(f"{label}不同")
        return {
            "runs": rows,
            "comparable": not reasons,
            "reasons": reasons,
            "diff": configuration_diff(
                rows[0]["configuration"], rows[1]["configuration"]
            ),
        }

    def measure(self, identifier):
        from quant_studio.server import resolve_run_asset

        directory = self._run(identifier)
        result = load_run_result(directory)
        # Check frozen metadata bytes before parsing their values as configuration.
        # A damaged YAML/JSON input must not turn comparison into a server error.
        if result["status"] == "succeeded":
            try:
                from quant_studio.result_integrity import verify_result

                verify_result(directory, result, asset="nav.csv")
            except (OSError, ValueError, QuantStudioError) as exc:
                return {
                    "run_id": identifier,
                    "title": self.annotation(identifier)["title"] or identifier[:12],
                    "template_id": "",
                    "status": result["status"],
                    "return": None,
                    "drawdown": None,
                    "currency": "未声明",
                    "dates": [],
                    "evidence": "未声明",
                    "opening_basis": "未核验",
                    "series": None,
                    "configuration": {},
                    "error": str(exc),
                }
        request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
        provenance_path = directory / "provenance.json"
        provenance = (
            json.loads(provenance_path.read_text(encoding="utf-8"))
            if provenance_path.exists()
            else {}
        )
        template = load_template(request["template_id"])
        config_path = directory / f"config.{template.config_format}"
        config = (
            yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if config_path.exists()
            else {}
        )
        opening_key = template.metadata.get("nav_initial_value_key")
        opening = (
            config.get(opening_key)
            if opening_key
            else template.metadata.get("nav_initial_value")
        )
        row = {
            "run_id": identifier,
            "title": self.annotation(identifier)["title"]
            or (request.get("recipe") or {}).get("name")
            or template.title,
            "template_id": template.id,
            "status": result["status"],
            "return": None,
            "drawdown": None,
            "currency": template.metadata.get("nav_currency", "未声明"),
            "dates": [],
            "evidence": provenance.get("input", {}).get("provider")
            or result.get("evidence_kind")
            or ("synthetic" if template.id == "synthetic-demo" else "未声明"),
            "opening_basis": "explicit" if opening is not None else "csv",
            "series": None,
            "configuration": request.get("recipe")
            or {
                "config": config,
                "cli": request.get("knobs", {}),
                "snapshot": request.get("snapshot"),
            },
            "error": str(result.get("message") or result.get("error") or ""),
        }
        if (
            result["status"] != "succeeded"
            or result.get("verification_status") == "failed"
        ):
            return row
        row["error"] = ""
        try:
            path = resolve_run_asset(self.root, identifier, "nav.csv")
            series = parse_nav_csv(path, initial_nav=opening)
            if series:
                row.update(
                    series=series,
                    dates=[date for date, _ in series.rows],
                    **{"return": series.period_return, "drawdown": series.max_drawdown},
                )
        except (OSError, ValueError, QuantStudioError) as exc:
            row["error"] = str(exc)
        return row

    def evidence_summary(self, identifier):
        """Only expose measured values after the same gate used by result views."""
        row = self.measure(identifier)
        return {
            "run_id": identifier,
            "status": row["status"],
            "verified": row["return"] is not None and not row["error"],
            "return": str(row["return"]) if row["return"] is not None else None,
            "drawdown": str(row["drawdown"]) if row["drawdown"] is not None else None,
            "metric_units": "ratio",
            "currency": row["currency"],
            "dates": [row["dates"][0], row["dates"][-1]] if row["dates"] else [],
            "observations": len(row["dates"]),
            "evidence_kind": row["evidence"],
            "opening_basis": row["opening_basis"],
            "error": row["error"],
            "unavailable": ["gross_return", "fees", "turnover", "benchmark_return"],
            "configuration": (
                json.loads(json.dumps(row["configuration"], default=str))
                if not row["error"]
                else None
            ),
        }
