from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from quant_studio import QuantStudioError
from quant_studio.runner import preflight, run
from quant_studio.server import serve
from quant_studio.templates import load_template


def main(argv: Sequence[str] | None = None) -> int:
    actual = list(argv) if argv is not None else sys.argv[1:]
    if actual and actual[0] == "modules":
        from quant_studio.module_tools import main as modules_main

        return modules_main(actual[1:])
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            if args.templates:
                os.environ["QUANT_STUDIO_TEMPLATES"] = args.templates
            serve(
                args.host,
                args.port,
                runs_root=args.runs_root,
                settings_path=args.settings,
                access_path=args.access,
                tls_cert=args.tls_cert,
                tls_key=args.tls_key,
                stop_file=args.stop_file,
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
    parser = argparse.ArgumentParser(
        prog="quant_studio",
        epilog=(
            "本地模块：quant_studio modules init/register --help；"
            "运维：python -m quant_studio.operations --help"
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    serve_parser = commands.add_parser("serve", help="启动本机页面")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8770)
    serve_parser.add_argument("--runs-root", help="研究记录目录；可放在源码仓库外")
    serve_parser.add_argument("--access", help="受控访问凭据文件（仅密码哈希）")
    serve_parser.add_argument("--tls-cert", help="HTTPS 证书 PEM")
    serve_parser.add_argument("--tls-key", help="HTTPS 私钥 PEM")
    serve_parser.add_argument("--templates", help="管理员注册的本机自定义研究模块目录")
    serve_parser.add_argument(
        "--stop-file", help="本机停止标记；出现后安全关闭服务与任务"
    )
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
