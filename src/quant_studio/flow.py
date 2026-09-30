from __future__ import annotations

import json
from typing import Any

import yaml

from quant_studio import QuantStudioError
from quant_studio.templates import (
    Template,
    apply_factor_selection,
    render_template,
)

PREVIEW_CONFIG = "<本次运行>/config"
PREVIEW_SNAPSHOT = "<本次运行>/snapshot"
PREVIEW_OUTPUT = "<本次运行>/strategy-output"


def flow_steps(template: Template) -> list[tuple[str, str]]:
    steps = [("data", "数据")]
    if template.metadata.get("factor_catalog"):
        steps.append(("factors", "因子"))
    steps.extend([("trade", "交易"), ("run", "回测"), ("code", "代码")])
    return steps


def compile_document(
    template: Template,
    knobs: dict[str, Any] | None = None,
    factors: list[str] | None = None,
    *,
    snapshot: str | None = None,
) -> str:
    rendered = render_template(template, knobs)
    if factors is not None:
        apply_factor_selection(rendered.config, template, factors)
    config_text = _config_text(template.config_format, rendered.config)
    argv = format_argv(
        list(template.argv),
        rendered.values,
        config=f"{PREVIEW_CONFIG}.{template.config_format}",
        snapshot=snapshot or PREVIEW_SNAPSHOT,
        output=PREVIEW_OUTPUT,
    )
    command = "\n".join(argv) if argv else "进程内合成样例，无外部命令"
    return f"配置\n{config_text}\n参数列表\n{command}\n"


def format_argv(
    argv: list[str],
    values: dict[str, Any],
    *,
    config: str,
    snapshot: str,
    output: str,
) -> list[str]:
    replacements = {
        **{name: str(value) for name, value in values.items()},
        "config": config,
        "snapshot": snapshot,
        "output": output,
    }
    try:
        return [part.format_map(replacements) for part in argv]
    except KeyError as exc:
        raise QuantStudioError(f"命令占位符无效: {exc.args[0]}") from exc


def _config_text(config_format: str, config: dict[str, Any]) -> str:
    if config_format == "yaml":
        return yaml.safe_dump(config, allow_unicode=True, sort_keys=False).rstrip()
    return json.dumps(config, ensure_ascii=False, indent=2)
