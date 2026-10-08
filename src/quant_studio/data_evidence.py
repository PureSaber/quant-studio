"""Strict data availability views backed only by native preflight evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from quant_studio import QuantStudioError
from quant_studio.run_record import load_run_result
from quant_studio.runner import _validate_preflight
from quant_studio.settings import setting
from quant_studio.templates import Template, load_template, template_ids


@dataclass(frozen=True)
class AvailabilityEvidence:
    template_id: str
    title: str
    source: str
    observed_through: str
    available_at: str
    missing: tuple[str, ...]
    blockers: tuple[str, ...]
    status: str
    authority: str
    run_id: str | None = None


def evidence_from_preflight(
    template: Template,
    request: dict,
    result: dict,
    evidence: object | None,
    *,
    run_id: str | None = None,
) -> AvailabilityEvidence:
    source = _source(template, request)
    status = str(result.get("status") or "unknown")
    if status != "checked":
        message = result.get("message")
        blockers = (str(message),) if isinstance(message, str) and message else ()
        return AvailabilityEvidence(
            template.id,
            template.title,
            source,
            "unknown",
            "unknown",
            (),
            blockers,
            "blocked" if blockers else "unknown",
            "native preflight failure" if blockers else "none",
            run_id,
        )
    try:
        _validate_preflight(template, evidence)
    except QuantStudioError as exc:
        return AvailabilityEvidence(
            template.id,
            template.title,
            source,
            "unknown",
            "unknown",
            (),
            (f"预检证据无效：{exc}",),
            "blocked",
            "invalid native preflight",
            run_id,
        )
    contract = template.metadata.get("availability_contract")
    if not isinstance(contract, dict):
        return AvailabilityEvidence(
            template.id,
            template.title,
            source,
            "unknown",
            "unknown",
            (),
            (),
            "ready",
            "validated native preflight; availability fields undeclared",
            run_id,
        )
    problems: list[str] = []
    observed = _declared_text(evidence, contract.get("observed_through"), problems)
    available = _declared_text(evidence, contract.get("available_at"), problems)
    missing = _declared_list(evidence, contract.get("missing"), problems)
    blockers = _declared_list(evidence, contract.get("blockers"), problems)
    blockers = (*blockers, *problems)
    return AvailabilityEvidence(
        template.id,
        template.title,
        source,
        observed,
        available,
        missing,
        blockers,
        "blocked" if missing or blockers else "ready",
        "validated native preflight",
        run_id,
    )


def latest_availability(runs_root: str | Path) -> list[AvailabilityEvidence]:
    root = Path(runs_root)
    latest: dict[str, tuple[int, Path, dict, dict]] = {}
    if root.is_dir():
        for directory in root.iterdir():
            request_path = directory / "request.json"
            if not directory.is_dir() or not request_path.is_file():
                continue
            try:
                request = json.loads(request_path.read_text(encoding="utf-8"))
                template_id = request["template_id"]
                if template_id not in template_ids():
                    continue
                result = load_run_result(directory)
                if result.get("status") not in {"checked", "check_failed"}:
                    continue
                stamp = (directory / "result.json").stat().st_mtime_ns
            except (OSError, ValueError, KeyError, TypeError):
                continue
            current = latest.get(template_id)
            if current is None or stamp > current[0]:
                latest[template_id] = (stamp, directory, request, result)
    records = []
    for template_id in template_ids():
        template = load_template(template_id)
        if template.kind == "synthetic":
            continue
        found = latest.get(template_id)
        if found is None:
            records.append(
                AvailabilityEvidence(
                    template.id,
                    template.title,
                    _source(template, {}),
                    "unknown",
                    "unknown",
                    (),
                    ("尚无原生预检证据",),
                    "unknown",
                    "none",
                )
            )
            continue
        _, directory, request, result = found
        evidence: object | None = None
        if result.get("status") == "checked":
            try:
                evidence = json.loads(
                    (directory / "preflight.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                evidence = None
        records.append(
            evidence_from_preflight(
                template, request, result, evidence, run_id=directory.name
            )
        )
    return records


def _source(template: Template, request: dict) -> str:
    selected = request.get("snapshot")
    if isinstance(selected, str) and selected:
        return selected
    declaration = template.metadata.get("input_source")
    if isinstance(declaration, dict):
        environment = declaration.get("environment")
        configured = setting(environment) if isinstance(environment, str) else None
        if configured:
            return configured
    if not template.metadata.get("requires_snapshot"):
        return "上游模板内置输入"
    return "unknown"


def _lookup(value: object, path: object) -> object:
    if not isinstance(path, str) or not path:
        return None
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _declared_text(value: object, path: object, problems: list[str]) -> str:
    if path is None:
        return "unknown"
    found = _lookup(value, path)
    if found is None:
        return "unknown"
    if not isinstance(found, str) or not found.strip() or len(found) > 160:
        problems.append(f"预检可用性证据结构无效：{path}")
        return "unknown"
    return found


def _declared_list(value: object, path: object, problems: list[str]) -> tuple[str, ...]:
    if path is None:
        return ()
    found = _lookup(value, path)
    if found is None:
        return ()
    if not isinstance(found, list) or not all(
        isinstance(item, str) and item and len(item) <= 400 for item in found
    ):
        problems.append(f"预检可用性证据结构无效：{path}")
        return ()
    return tuple(found)
