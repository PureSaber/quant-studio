"""Inspect completion records without repairing or executing historical runs."""

import json
from pathlib import Path


def load_run_result(directory: Path) -> dict:
    try:
        result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        if (
            isinstance(result, dict)
            and isinstance(result.get("status"), str)
            and result["status"]
            and isinstance(result.get("argv", []), list)
        ):
            return result
        detail = "结果记录的结构无效"
    except FileNotFoundError:
        detail = "尚未写入结果记录"
    except (OSError, ValueError) as exc:
        detail = f"无法读取完整结果记录：{exc}"
    return {"status": "incomplete", "argv": [], "message": detail}
