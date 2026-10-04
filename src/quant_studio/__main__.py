from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from quant_studio import QuantStudioError
from quant_studio.runner import preflight, run
from quant_studio.server import serve
from quant_studio.templates import load_template


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            serve(
                args.host,
                args.port,
                runs_root=args.runs_root,
                settings_path=args.settings,
            )
            return 0
        knobs = _parse_sets(args.template_id, args.settings)
        if args.command == "check":
            result = preflight(args.template_id, knobs, snapshot=args.snapshot)
            print(json.dumps(result.as_json(), ensure_ascii=False, indent=2))
            return 0 if result.status == "checked" else 2
        execute = args.command == "run" and args.execute
        result = run(args.template_id, knobs, execute=execute, snapshot=args.snapshot)
        print(json.dumps(result.as_json(), ensure_ascii=False, indent=2))
        return {"failed": 1, "blocked": 2}.get(result.status, 0)
    except (QuantStudioError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quant_studio")
    commands = parser.add_subparsers(dest="command", required=True)
    serve_parser = commands.add_parser("serve", help="启动本机页面")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8770)
    serve_parser.add_argument("--runs-root", help="研究记录目录；可放在源码仓库外")
    serve_parser.add_argument(
        "--settings",
        help="本地配置保存文件；默认使用研究记录目录下的studio-settings.json",
    )
    for name in ("preview", "check", "run"):
        command = commands.add_parser(name)
        command.add_argument("template_id")
        command.add_argument("--set", dest="settings", action="append", default=[])
        command.add_argument(
            "--snapshot",
            help="模板所需的已有输入路径；数据目录或统计套利研究配置文件",
        )
        if name == "run":
            command.add_argument("--execute", action="store_true")
    return parser


def _parse_sets(template_id: str, settings: list[str]) -> dict[str, object]:
    template = load_template(template_id)
    declarations = {knob["name"]: knob for knob in template.knobs}
    values: dict[str, object] = {}
    for setting in settings:
        if "=" not in setting:
            raise QuantStudioError(f"参数格式应为 key=value: {setting}")
        name, raw = setting.split("=", 1)
        if name not in declarations:
            raise QuantStudioError(f"未知参数 {name}")
        kind = declarations[name]["type"]
        try:
            if kind == "int":
                value: object = int(raw)
            elif kind == "number":
                value = float(raw) if any(char in raw for char in ".eE") else int(raw)
            else:
                value = raw
        except ValueError as exc:
            raise QuantStudioError(f"参数 {name} 类型错误") from exc
        values[name] = value
    return values


if __name__ == "__main__":
    raise SystemExit(main())
